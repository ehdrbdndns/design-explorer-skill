#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zlib
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from itertools import combinations
from pathlib import Path, PurePosixPath
from urllib.parse import unquote_to_bytes, urlparse


AXES = ("layout", "typography", "palette", "density", "imagery", "interaction")
ALLOWED_MOCKUP_STATUSES = {"pending", "success", "failed"}
LEGACY_GENERATION_ACCOUNTING_KEYS = {
    "generation_attempts_used",
    "last_generation_authorized_at",
    "last_generation_direction_id",
    "last_generation_authorized_direction_id",
}
SECRET_KEYS = {
    "apikey",
    "token",
    "secret",
    "password",
    "cookie",
    "authorization",
    "pairingtoken",
}
SECRET_KEY_PATTERN = re.compile(
    r"(?:api|access|refresh|auth|bearer|pairing)?token|api(?:key|secret)|clientsecret"
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
BEARER_CANDIDATE_PATTERN = re.compile(
    r"\bBearer\s+([A-Za-z0-9._~+/-]{24,}={0,2})", re.IGNORECASE
)
RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
VIEWPORT_PATTERN = re.compile(r"[1-9]\d*x[1-9]\d*")
MAX_VIEWPORT_DIMENSION = 10_000
MAX_PNG_CHUNK_BYTES = 32 * 1024 * 1024
MAX_PNG_FILE_BYTES = 64 * 1024 * 1024
PROMPT_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
PROVIDER_OUTPUT_PATTERN = re.compile(
    r"provider:(?P<provider>[a-z0-9][a-z0-9-]*):"
    r"(?P<artifact>[A-Za-z0-9][A-Za-z0-9._-]*)"
)
BLOCKED_HOST_SUFFIXES = (
    "localhost",
    "local",
    "test",
    "invalid",
    "example",
    "onion",
    "internal",
    "alt",
    "home.arpa",
    "nip.io",
    "sslip.io",
)


def valid_rfc3339(value) -> bool:
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def has_bearer_secret(value: str) -> bool:
    for match in BEARER_CANDIDATE_PATTERN.finditer(value):
        candidate = match.group(1)
        jwt_segments = candidate.split(".")
        is_jwt = (
            len(jwt_segments) == 3
            and all(len(segment) >= 4 for segment in jwt_segments)
        )
        has_opaque_signal = any(
            character.isdigit() or character in "_+/=" for character in candidate
        )
        if is_jwt or has_opaque_signal:
            return True
    return False


def read_json(run_dir: Path, name: str, errors: list[str]):
    path = run_dir / name
    if not path.is_file():
        errors.append(f"missing {name}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        errors.append(f"invalid {name}: {error}")
        return None


def valid_hostname(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    if re.fullmatch(r"[0-9.]+", value):
        return False
    try:
        ascii_hostname = value.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if not ascii_hostname or len(ascii_hostname) > 253:
        return False
    label_pattern = re.compile(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    )
    return all(label_pattern.fullmatch(label) for label in ascii_hostname.split("."))


def valid_url(value) -> bool:
    if not isinstance(value, str) or not value or any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for character in value
    ):
        return False
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or not valid_hostname(hostname)
    ):
        return False
    normalized_hostname = hostname.rstrip(".").casefold()
    if any(
        normalized_hostname == suffix
        or normalized_hostname.endswith(f".{suffix}")
        for suffix in BLOCKED_HOST_SUFFIXES
    ):
        return False
    try:
        address = ipaddress.ip_address(normalized_hostname)
        return address.is_global and not any(
            (
                address.is_loopback,
                address.is_private,
                address.is_link_local,
                address.is_reserved,
                address.is_multicast,
                address.is_unspecified,
            )
        )
    except ValueError:
        return "." in normalized_hostname


def valid_artifact_ref(value, allow_provider_hint: bool = False) -> bool:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    value = value.strip()
    if "\\" in value or "@" in value or value.startswith("~"):
        return False
    if "://" in value:
        return False
    if allow_provider_hint and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9][A-Za-z0-9._-]*", value
    ):
        return True
    if ":" in value:
        return False
    parts = value.split("/")
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in parts)
    )


def normalized_viewport(value) -> str | None:
    if not isinstance(value, str) or not VIEWPORT_PATTERN.fullmatch(value):
        return None
    width, height = (int(part) for part in value.split("x"))
    if width > MAX_VIEWPORT_DIMENSION or height > MAX_VIEWPORT_DIMENSION:
        return None
    return f"{width}x{height}"


def normalized_string_list(value, label: str, errors: list[str]) -> list[str] | None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        errors.append(f"{label} must be a list of non-empty strings")
        return None
    normalized = [item.strip() for item in value]
    if normalized != value or len(set(normalized)) != len(normalized):
        errors.append(f"{label} must be normalized and unique")
        return None
    return normalized


def resolved_scoped_path(base: Path, relative: str) -> Path | None:
    if not valid_artifact_ref(relative):
        return None
    try:
        base = base.resolve(strict=True)
        candidate = (base / relative).resolve(strict=True)
        candidate.relative_to(base)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        if path.stat().st_size > MAX_PNG_FILE_BYTES:
            return None
        stream = path.open("rb")
    except OSError:
        return None
    with stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            return None
        dimensions = None
        saw_idat = False
        chunk_index = 0
        while True:
            header = stream.read(8)
            if len(header) != 8:
                return None
            length, chunk_type = struct.unpack(">I4s", header)
            if length > MAX_PNG_CHUNK_BYTES:
                return None
            if chunk_index == 0 and (chunk_type != b"IHDR" or length != 13):
                return None
            if chunk_index > 0 and chunk_type == b"IHDR":
                return None
            if chunk_type == b"IEND" and length != 0:
                return None

            remaining = length
            crc = zlib.crc32(chunk_type)
            ihdr = bytearray()
            while remaining:
                block = stream.read(min(remaining, 64 * 1024))
                if not block:
                    return None
                if chunk_type == b"IHDR":
                    ihdr.extend(block)
                crc = zlib.crc32(block, crc)
                remaining -= len(block)
            stored_crc = stream.read(4)
            if len(stored_crc) != 4 or struct.unpack(">I", stored_crc)[0] != (
                crc & 0xFFFFFFFF
            ):
                return None

            if chunk_type == b"IHDR":
                width, height = struct.unpack(">II", ihdr[:8])
                if width <= 0 or height <= 0:
                    return None
                dimensions = (width, height)
            elif chunk_type == b"IDAT":
                saw_idat = True
            elif chunk_type == b"IEND":
                if dimensions is None or not saw_idat or stream.read(1) != b"":
                    return None
                return dimensions
            chunk_index += 1


def valid_preview_route(value) -> bool:
    if not isinstance(value, str):
        return False
    current = value
    for _ in range(6):
        if (
            not current.startswith("/")
            or current.startswith("//")
            or "//" in current
            or "?" in current
            or "#" in current
            or "\\" in current
            or any(part in {".", ".."} for part in current.split("/"))
            or any(
                character.isspace() or unicodedata.category(character) == "Cc"
                for character in current
            )
            or re.search(r"%(?![0-9A-Fa-f]{2})", current)
        ):
            return False
        try:
            decoded = unquote_to_bytes(current).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        if decoded == current:
            return "%" not in current
        current = decoded
    return False


def preview_files_digest(root: Path, paths: list[str]) -> str | None:
    digest = hashlib.sha256()
    for relative in sorted(paths):
        resolved = resolved_scoped_path(root, relative)
        if resolved is None or not resolved.is_file():
            return None
        name = relative.encode("utf-8")
        try:
            size = resolved.stat().st_size
            digest.update(struct.pack(">Q", len(name)))
            digest.update(name)
            digest.update(struct.pack(">Q", size))
            with resolved.open("rb") as stream:
                while block := stream.read(64 * 1024):
                    digest.update(block)
        except OSError:
            return None
    return "sha256:" + digest.hexdigest()


CSS_CUSTOM_PROPERTY_PATTERN = re.compile(r"(?m)(--[A-Za-z0-9_-]+)\s*:")
CSS_CUSTOM_PROPERTY_USE_PATTERN = re.compile(
    r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,|\))"
)
JSX_STYLE_ATTRIBUTE_START_PATTERN = re.compile(r"\bstyle\s*=\s*\{")
JSX_STYLE_ALIAS_PATTERN = re.compile(
    r"\bstyle\s*=\s*\{\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\}"
)
CONST_OBJECT_PATTERN = re.compile(
    r"\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*(?::\s*[^=;\n]+)?=\s*\{"
)
VARIABLE_BINDING_PATTERN = re.compile(
    r"\b(const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
DESTRUCTURED_VARIABLE_PATTERN = re.compile(
    r"\b(?:const|let|var)\s+(\{[^;]*?\}|\[[^;]*?\])\s*="
)
FUNCTION_DECLARATION_PATTERN = re.compile(
    r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\b"
)
CLASS_DECLARATION_PATTERN = re.compile(
    r"\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)\b"
)
FUNCTION_PARAMETER_PATTERN = re.compile(
    r"\bfunction(?:\s+[A-Za-z_$][A-Za-z0-9_$]*)?\s*"
    r"(?:<[^>{};]*>)?\s*"
    r"\(([^)]*)\)\s*(?::[^\{;]+)?\{"
)
PAREN_ARROW_PARAMETER_PATTERN = re.compile(
    r"\(([^()]*)\)\s*(?::[^=;]+)?=>"
)
SINGLE_ARROW_PARAMETER_PATTERN = re.compile(
    r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*=>"
)
METHOD_PARAMETER_PATTERN = re.compile(
    r"(?m)(?:^|[;{}])\s*"
    r"(?:(?:public|private|protected|static|abstract|override|async|get|set)\s+)*"
    r"[A-Za-z_$][A-Za-z0-9_$]*\s*(?:<[^>{};]*>)?\s*"
    r"\(([^()]*)\)\s*(?::[^\{;]+)?\{"
)
CATCH_BINDING_PATTERN = re.compile(r"\bcatch\s*\(([^)]*)\)\s*\{")
TOKEN_STYLESHEET_SUFFIXES = frozenset({".css", ".less", ".sass", ".scss"})


def css_without_comments(source: str, *, line_comments: bool = False) -> str:
    output = list(source)
    index = 0
    quote = None
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote is not None:
            if current == "\\" and index + 1 < len(source):
                index += 2
                continue
            if current == quote:
                quote = None
            index += 1
            continue
        if current in {"'", '"'}:
            quote = current
            index += 1
            continue
        if current == "/" and following == "*":
            close = source.find("*/", index + 2)
            end = len(source) if close == -1 else close + 2
            _mask_span(output, index, end)
            index = end
            continue
        if line_comments and current == "/" and following == "/":
            end = source.find("\n", index + 2)
            end = len(source) if end == -1 else end
            _mask_span(output, index, end)
            index = end
            continue
        index += 1
    return "".join(output)


def matching_brace(source: str, start: int) -> int | None:
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def top_level_ranges(source: str, start: int, end: int, separator: str) -> list[tuple[int, int]]:
    ranges = []
    range_start = start
    parens = brackets = braces = 0
    for index in range(start, end):
        character = source[index]
        if character == "(":
            parens += 1
        elif character == ")":
            parens = max(0, parens - 1)
        elif character == "[":
            brackets += 1
        elif character == "]":
            brackets = max(0, brackets - 1)
        elif character == "{":
            braces += 1
        elif character == "}":
            braces = max(0, braces - 1)
        elif character == separator and parens == brackets == braces == 0:
            ranges.append((range_start, index))
            range_start = index + 1
    ranges.append((range_start, end))
    return ranges


def top_level_character(source: str, start: int, end: int, target: str) -> int | None:
    for range_start, range_end in top_level_ranges(source, start, end, target):
        if range_end < end:
            return range_end
    return None


def literal_is_style_value(source: str, start: int, literal_start: int) -> bool:
    stack = []
    for index in range(start, literal_start):
        character = source[index]
        if character == "(":
            previous = index - 1
            while previous >= start and source[previous].isspace():
                previous -= 1
            is_call = previous >= start and (
                source[previous].isalnum() or source[previous] in "_$.)]"
            )
            stack.append("call" if is_call else "group")
        elif character == ")":
            if stack and stack[-1] in {"call", "group"}:
                stack.pop()
        elif character == "[":
            stack.append("bracket")
        elif character == "]":
            if stack and stack[-1] == "bracket":
                stack.pop()
        elif character == "{":
            stack.append("object")
        elif character == "}":
            if stack and stack[-1] == "object":
                stack.pop()
    return not any(kind in {"call", "bracket", "object"} for kind in stack)


def object_style_value_literals(
    lexed: "JsLexResult", object_start: int, object_end: int
) -> list[str]:
    values = []
    for property_start, property_end in top_level_ranges(
        lexed.executable, object_start + 1, object_end, ","
    ):
        colon = top_level_character(
            lexed.executable, property_start, property_end, ":"
        )
        if colon is None:
            continue
        value_start = colon + 1
        for literal in lexed.literals:
            if value_start <= literal.start < property_end and literal_is_style_value(
                lexed.executable, value_start, literal.start
            ):
                values.append(literal.value)
    return values


def brace_scope_at(source: str, position: int) -> tuple[int, ...]:
    stack = []
    for index, character in enumerate(source[:position]):
        if character == "{":
            stack.append(index)
        elif character == "}" and stack:
            stack.pop()
    return tuple(stack)


def parameter_binding_names(source: str) -> set[str]:
    source = source.strip()

    def matching_pattern_end(start: int) -> int | None:
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack = []
        for index in range(start, len(source)):
            character = source[index]
            if character in pairs:
                stack.append(pairs[character])
            elif stack and character == stack[-1]:
                stack.pop()
                if not stack:
                    return index
        return None

    if source.startswith("..."):
        return parameter_binding_names(source[3:])
    if source.startswith(("{", "[")):
        close = matching_pattern_end(0)
        if close is None:
            return set()
        names = set()
        for start, end in top_level_ranges(source, 1, close, ","):
            item = source[start:end]
            colon = top_level_character(item, 0, len(item), ":")
            binding = item[colon + 1 :] if colon is not None else item
            names.update(parameter_binding_names(binding))
        return names
    default = top_level_character(source, 0, len(source), "=")
    if default is not None:
        source = source[:default].rstrip()
    annotation = top_level_character(source, 0, len(source), ":")
    if annotation is not None:
        source = source[:annotation].rstrip()
    match = re.match(r"([A-Za-z_$][A-Za-z0-9_$]*)", source)
    return {match.group(1)} if match else set()


def arrow_body_range(source: str, start: int) -> tuple[int, int, tuple[int, ...]]:
    while start < len(source) and source[start].isspace():
        start += 1
    if start < len(source) and source[start] == "{":
        end = matching_brace(source, start)
        end = len(source) if end is None else end
        return start, end, brace_scope_at(source, start + 1)
    return start, statement_end(source, start), brace_scope_at(source, start)


def jsx_style_value_literals(source: str) -> list[str]:
    lexed = lex_js(source)
    values = []
    for match in JSX_STYLE_ATTRIBUTE_START_PATTERN.finditer(lexed.executable):
        inner_start = match.end()
        while inner_start < len(source) and lexed.executable[inner_start].isspace():
            inner_start += 1
        if inner_start >= len(source) or lexed.executable[inner_start] != "{":
            continue
        inner_end = matching_brace(lexed.executable, inner_start)
        if inner_end is None:
            continue
        values.extend(object_style_value_literals(lexed, inner_start, inner_end))

    object_initializers = {}
    for match in CONST_OBJECT_PATTERN.finditer(lexed.executable):
        object_start = match.end() - 1
        object_end = matching_brace(lexed.executable, object_start)
        if object_end is not None:
            object_initializers[(match.group(1), match.start())] = (
                object_start,
                object_end,
            )

    bindings = []
    for match in VARIABLE_BINDING_PATTERN.finditer(lexed.executable):
        name = match.group(2)
        object_range = object_initializers.get((name, match.start()))
        bindings.append(
            (
                name,
                match.start(),
                brace_scope_at(lexed.executable, match.start()),
                object_range,
                False,
                None,
            )
        )

    for match in FUNCTION_PARAMETER_PATTERN.finditer(lexed.executable):
        body_start = match.end() - 1
        parameter_scope = brace_scope_at(lexed.executable, body_start + 1)
        for start, end in top_level_ranges(
            lexed.executable, match.start(1), match.end(1), ","
        ):
            for name in parameter_binding_names(lexed.executable[start:end]):
                bindings.append(
                    (name, match.start(), parameter_scope, None, True, None)
                )

    for pattern in (METHOD_PARAMETER_PATTERN, CATCH_BINDING_PATTERN):
        for match in pattern.finditer(lexed.executable):
            body_start = match.end() - 1
            body_end = matching_brace(lexed.executable, body_start)
            parameter_scope = brace_scope_at(lexed.executable, body_start + 1)
            visibility = (
                (body_start, body_end)
                if body_end is not None
                else (body_start, len(lexed.executable))
            )
            for start, end in top_level_ranges(
                lexed.executable, match.start(1), match.end(1), ","
            ):
                for name in parameter_binding_names(lexed.executable[start:end]):
                    bindings.append(
                        (
                            name,
                            match.start(),
                            parameter_scope,
                            None,
                            True,
                            visibility,
                        )
                    )

    for pattern in (PAREN_ARROW_PARAMETER_PATTERN, SINGLE_ARROW_PARAMETER_PATTERN):
        for match in pattern.finditer(lexed.executable):
            body_start, body_end, parameter_scope = arrow_body_range(
                lexed.executable, match.end()
            )
            for name in parameter_binding_names(match.group(1)):
                bindings.append(
                    (
                        name,
                        match.start(),
                        parameter_scope,
                        None,
                        True,
                        (body_start, body_end),
                    )
                )

    for match in STATIC_FROM_PATTERN.finditer(lexed.executable):
        reference = JsModuleReference("import", "", match.group("clause"))
        for name in import_bindings(reference):
            bindings.append(
                (
                    name,
                    match.start(),
                    brace_scope_at(lexed.executable, match.start()),
                    None,
                    True,
                    None,
                )
            )

    used_declarations = set()
    for alias in JSX_STYLE_ALIAS_PATTERN.finditer(lexed.executable):
        name = alias.group(1)
        use_scope = brace_scope_at(lexed.executable, alias.start())
        candidates = []
        for binding in bindings:
            declared_name, declared_at, declared_scope, _, hoisted, visibility = binding
            if declared_name != name:
                continue
            if visibility is not None and not (
                visibility[0] <= alias.start() < visibility[1]
            ):
                continue
            if use_scope[: len(declared_scope)] != declared_scope:
                continue
            candidates.append(binding)
        if candidates:
            closest_depth = max(len(item[2]) for item in candidates)
            closest = [item for item in candidates if len(item[2]) == closest_depth]
            if len(closest) != 1:
                continue
            selected = closest[0]
            if (
                selected[2] == use_scope
                and selected[1] > alias.start()
                and not selected[4]
            ):
                continue
            if selected[3] is None:
                continue
            used_declarations.add(selected[3])

    for object_start, object_end in used_declarations:
        values.extend(object_style_value_literals(lexed, object_start, object_end))
    return values


def css_custom_properties(paths: list[Path]) -> set[str]:
    values: set[str] = set()
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        suffix = path.suffix.casefold()
        if suffix in TOKEN_STYLESHEET_SUFFIXES:
            source = css_without_comments(source, line_comments=suffix != ".css")
        elif path.suffix.casefold() in {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
        }:
            source = lex_js(source).executable
        values.update(CSS_CUSTOM_PROPERTY_PATTERN.findall(source))
    return values


def css_custom_property_uses(paths: list[Path]) -> set[str]:
    values: set[str] = set()
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        suffix = path.suffix.casefold()
        if suffix in TOKEN_STYLESHEET_SUFFIXES:
            values.update(
                CSS_CUSTOM_PROPERTY_USE_PATTERN.findall(
                    css_without_comments(source, line_comments=suffix != ".css")
                )
            )
        elif suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
            for value in jsx_style_value_literals(source):
                values.update(
                    CSS_CUSTOM_PROPERTY_USE_PATTERN.findall(value)
                )
    return values


def path_is_equal_to_or_below(path: str, protected: str) -> bool:
    candidate = PurePosixPath(path)
    boundary = PurePosixPath(protected)
    return candidate == boundary or boundary in candidate.parents


def offline_esbuild_errors(entry: Path, working_directory: Path) -> list[str]:
    executable = shutil.which("esbuild")
    if executable is None:
        for candidate in (
            working_directory / "node_modules/.bin/esbuild",
            Path(__file__).resolve().parents[2] / "node_modules/.bin/esbuild",
        ):
            if candidate.is_file():
                executable = str(candidate)
                break
    if executable is None:
        return ["offline esbuild executable is required for code-preview validation"]
    try:
        with tempfile.TemporaryDirectory(prefix="design-explorer-esbuild-") as temporary:
            result = subprocess.run(
                [
                    executable,
                    str(entry),
                    "--bundle",
                    "--format=esm",
                    "--platform=browser",
                    "--packages=external",
                    "--jsx=automatic",
                    "--loader:.scss=css",
                    "--loader:.sass=css",
                    "--loader:.less=css",
                    "--log-level=error",
                    f"--outfile={Path(temporary) / 'preview.js'}",
                ],
                cwd=working_directory,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
    except (OSError, subprocess.SubprocessError) as error:
        return [f"offline esbuild compile failed: {type(error).__name__}"]
    if result.returncode == 0:
        return []
    detail = next(
        (line.strip() for line in result.stderr.splitlines() if line.strip()),
        "invalid or unbundleable preview source",
    )
    return [f"offline esbuild compile failed: {detail}"]


def code_preview_errors(
    run_dir: Path, run: dict, item: dict, index: int
) -> list[str]:
    """Validate one reproducible project or standalone direction preview."""
    errors = []
    label = f"mockups[{index}] code preview"

    def string_list(field: str, *, required: bool = True) -> list[str] | None:
        value = item.get(field, [] if not required else None)
        if (
            not isinstance(value, list)
            or (required and not value)
            or any(not isinstance(entry, str) or not entry.strip() for entry in value)
        ):
            requirement = "a non-empty list" if required else "a list"
            field_label = (
                f"mockups[{index}] project code preview"
                if field == "component_sources" and mode == "project"
                else label
            )
            errors.append(f"{field_label} {field} must be {requirement} of strings")
            return None
        normalized = [entry.strip() for entry in value]
        if normalized != value or len(set(normalized)) != len(normalized):
            errors.append(f"{label} {field} must be normalized and unique")
            return None
        return normalized

    mode = item.get("preview_mode")
    source_root = None
    if mode == "project":
        project_path = run.get("project_path")
        if not isinstance(project_path, str) or not Path(project_path).is_absolute():
            errors.append(
                f"{label} project mode requires project_path to be an existing absolute directory"
            )
        else:
            try:
                candidate = Path(project_path).resolve(strict=True)
            except (OSError, RuntimeError):
                candidate = None
            if candidate is None or not candidate.is_dir():
                errors.append(
                    f"{label} project mode requires project_path to be an existing absolute directory"
                )
            else:
                source_root = candidate
    elif mode == "standalone":
        try:
            source_root = Path(run_dir).resolve(strict=True)
        except (OSError, RuntimeError):
            source_root = None
    else:
        errors.append(f"{label} preview_mode must be project or standalone")

    preview_files = string_list("preview_files")
    token_sources = string_list("token_sources")
    component_sources = string_list("component_sources")
    used_tokens = string_list("used_tokens")

    path_lists = (
        ("preview_files", preview_files),
        ("token_sources", token_sources),
        ("component_sources", component_sources),
    )
    for field, values in path_lists:
        if values is not None and any(not valid_artifact_ref(value) for value in values):
            errors.append(f"{label} {field} must use safe relative paths")

    preview_path = item.get("preview_path")
    if not isinstance(preview_path, str) or not valid_artifact_ref(preview_path):
        errors.append(f"{label} preview_path must be a safe relative path")
        preview_path = None
    elif preview_files is not None and preview_path not in preview_files:
        errors.append(f"{label} preview_path must be included in preview_files")

    if not valid_preview_route(item.get("preview_route")):
        errors.append(
            f"{label} preview_route must be a normalized absolute URL path"
        )

    resolved_files = {}
    if source_root is not None and preview_files is not None:
        for relative in preview_files:
            resolved = resolved_scoped_path(source_root, relative)
            if resolved is None or not resolved.is_file():
                errors.append(
                    f"{label} preview file must be contained and existing: {relative}"
                )
            else:
                resolved_files[relative] = resolved

    for field, values in (
        ("token_sources", token_sources),
        ("component_sources", component_sources),
    ):
        if values is not None and preview_files is not None:
            for relative in values:
                if relative not in preview_files:
                    errors.append(
                        f"{label} {field} must be included in preview_files: {relative}"
                    )

    supported_suffixes = ", ".join(sorted(TOKEN_STYLESHEET_SUFFIXES))
    for relative in token_sources or []:
        if PurePosixPath(relative).suffix.casefold() not in TOKEN_STYLESHEET_SUFFIXES:
            errors.append(
                f"{label} token_source must use a supported stylesheet suffix "
                f"({supported_suffixes}): {relative}"
            )

    reachable = set()
    if source_root is not None and preview_files is not None and preview_path is not None:
        absolute_root = source_root
        if mode == "standalone":
            packages = [
                path
                for path in preview_files
                if PurePosixPath(path).name == "package.json"
            ]
            if len(packages) == 1:
                absolute_root = source_root / PurePosixPath(packages[0]).parent
        reachable, closure_errors = dependency_closure(
            source_root,
            preview_files,
            [preview_path],
            absolute_root,
            runtime_only=True,
        )
        errors.extend(f"{label} {error}" for error in closure_errors)

    for singular, values in (
        ("token_source", token_sources),
        ("component_source", component_sources),
    ):
        for relative in values or []:
            if relative not in reachable:
                errors.append(
                    f"{label} {singular} must be reachable from preview_path: {relative}"
                )

    reusable_sources = set(token_sources or []) | set(component_sources or [])
    if mode == "project" and preview_files is not None:
        production_paths = run.get("production_paths")
        if isinstance(production_paths, list) and all(
            isinstance(path, str) and valid_artifact_ref(path)
            for path in production_paths
        ):
            if preview_path is not None and any(
                path_is_equal_to_or_below(preview_path, protected)
                for protected in production_paths
            ):
                errors.append(
                    f"{label} direction-owned preview_path must not be equal to or "
                    f"under production_paths: {preview_path}"
                )
            for relative in preview_files:
                if relative == preview_path or relative in reusable_sources:
                    continue
                if any(
                    path_is_equal_to_or_below(relative, protected)
                    for protected in production_paths
                ):
                    errors.append(
                        f"{label} direction-owned file must not be equal to or under "
                        f"production_paths: {relative}"
                    )

    for relative in component_sources or []:
        if relative in reachable and not component_source_is_runtime_used(
            source_root,
            resolved_files,
            reachable,
            relative,
            absolute_root if source_root is not None else None,
        ):
            errors.append(
                f"{label} component_source must be rendered or called at runtime: {relative}"
            )

    if (
        mode == "standalone"
        and source_root is not None
        and preview_files is not None
        and len(resolved_files) == len(preview_files)
    ):
        errors.extend(_standalone_topology_errors(source_root, preview_files, resolved_files))
        packages = [
            path for path in preview_files if PurePosixPath(path).name == "package.json"
        ]
        if len(packages) == 1:
            package_parent = PurePosixPath(packages[0]).parent
            prefix = "" if str(package_parent) == "." else f"{package_parent}/"
            app_relative = f"{prefix}src/App.tsx"
            main_relative = f"{prefix}src/main.tsx"
            app_path = resolved_files.get(app_relative)
            workspace_root = source_root / package_parent
            if app_path is not None:
                app_reachable, app_errors = dependency_closure(
                    source_root,
                    preview_files,
                    [app_relative],
                    workspace_root,
                    runtime_only=True,
                )
                errors.extend(f"{label} {error}" for error in app_errors)
                if preview_path is not None and preview_path not in app_reachable:
                    errors.append(
                        f"standalone App must route to preview_path: {preview_path}"
                    )
                try:
                    app_source = app_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    app_source = ""
                preview_route = item.get("preview_route")
                if (
                    preview_path is None
                    or not isinstance(preview_route, str)
                    or not standalone_route_maps_preview_component(
                        source_root,
                        app_path,
                        app_source,
                        preview_path,
                        preview_route,
                        workspace_root,
                    )
                ):
                    errors.append(
                        "standalone App route table must map preview_route to its "
                        "imported preview_path component: "
                        f"{preview_route}"
                    )
            compile_entry = resolved_files.get(main_relative)
            if compile_entry is not None:
                errors.extend(offline_esbuild_errors(compile_entry, workspace_root))
    elif mode == "project" and source_root is not None and preview_path is not None:
        compile_entry = resolved_files.get(preview_path)
        if compile_entry is not None:
            errors.extend(offline_esbuild_errors(compile_entry, source_root))
        preview_route = item.get("preview_route")
        if isinstance(preview_route, str) and not project_preview_route_is_bound(
            compile_entry, preview_route
        ):
            errors.append(
                f"project preview entry must bind preview_route: {preview_route}"
            )

    reachable_paths = [
        resolved_files[path] for path in reachable if path in resolved_files
    ]
    token_paths = [
        resolved_files[path]
        for path in token_sources or []
        if path in reachable
        and path in resolved_files
        and PurePosixPath(path).suffix.casefold() in TOKEN_STYLESHEET_SUFFIXES
    ]
    definitions = css_custom_properties(token_paths)
    references = css_custom_property_uses(reachable_paths)
    for token in used_tokens or []:
        if CSS_CUSTOM_PROPERTY_PATTERN.fullmatch(f"{token}:") is None:
            errors.append(f"{label} used_tokens contains an invalid CSS variable: {token}")
            continue
        if token not in definitions:
            errors.append(
                f"{label} used token must be defined by token_sources: {token}"
            )
        if token not in references:
            errors.append(
                f"{label} used token must be referenced by preview dependency set: {token}"
            )

    supporting_refs = string_list("supporting_provider_refs", required=False)
    if supporting_refs is not None:
        for reference in supporting_refs:
            if not _valid_provider_output_ref(reference):
                errors.append(
                    f"{label} supporting_provider_refs must use "
                    "provider:<lowercase-provider>:<safe-artifact-id>"
                )
        attempt_count = item.get("attempt_count")
        if supporting_refs and (
            not isinstance(attempt_count, int)
            or isinstance(attempt_count, bool)
            or attempt_count <= 0
        ):
            errors.append(
                f"{label} supporting_provider_refs require a positive attempt_count"
            )

    expected_digest = None
    if (
        source_root is not None
        and preview_files is not None
        and len(resolved_files) == len(preview_files)
    ):
        expected_digest = preview_files_digest(source_root, preview_files)
    source_digest = item.get("source_digest")
    if (
        not isinstance(source_digest, str)
        or PROMPT_DIGEST_PATTERN.fullmatch(source_digest) is None
        or expected_digest is None
        or source_digest != expected_digest
    ):
        errors.append(f"{label} source_digest must match current preview_files")

    if item.get("status") == "success" and item.get("output_kind") != "local":
        errors.append(f"{label} success requires local output_kind")

    target_viewports = run.get("target_viewports")
    valid_targets = (
        isinstance(target_viewports, list)
        and bool(target_viewports)
        and all(normalized_viewport(viewport) == viewport for viewport in target_viewports)
        and len(set(target_viewports)) == len(target_viewports)
    )
    viewport_checks = item.get("viewport_checks")
    if not isinstance(viewport_checks, dict):
        errors.append(f"{label} viewport_checks must be an object")
        viewport_checks = {}
    if valid_targets and set(viewport_checks) != set(target_viewports):
        errors.append(
            f"{label} viewport_checks keys must exactly match run target_viewports"
        )
    for viewport in target_viewports if valid_targets else []:
        check = viewport_checks.get(viewport)
        check_label = f"{label} viewport_checks[{viewport}]"
        if not isinstance(check, dict):
            errors.append(f"{check_label} must be an object")
            continue
        for name in ("content", "overflow", "accessibility", "interaction"):
            if check.get(name) != "pass":
                errors.append(f"{check_label} status must pass: {name}")
        screenshot_ref = check.get("screenshot_ref")
        if not valid_artifact_ref(screenshot_ref):
            errors.append(f"{check_label} screenshot_ref must be a safe run-relative path")
            continue
        screenshot = resolved_scoped_path(Path(run_dir), screenshot_ref)
        if screenshot is None or not screenshot.is_file():
            errors.append(f"{check_label} screenshot must be an existing file")
            continue
        dimensions = png_dimensions(screenshot)
        if dimensions is None:
            errors.append(f"{check_label} screenshot must be a complete PNG")
            continue
        expected_dimensions = tuple(int(part) for part in viewport.split("x"))
        if dimensions != expected_dimensions:
            errors.append(
                f"{check_label} screenshot dimensions {dimensions[0]}x{dimensions[1]} "
                f"must match {viewport}"
            )
    return errors


LOCAL_SCRIPT_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".css")


@dataclass(frozen=True)
class JsStringLiteral:
    start: int
    end: int
    value: str
    quote: str


@dataclass(frozen=True)
class JsLexResult:
    executable: str
    literals: tuple[JsStringLiteral, ...]


@dataclass(frozen=True)
class JsModuleReference:
    kind: str
    specifier: str
    clause: str = ""
    type_only: bool = False


def _mask_span(output: list[str], start: int, end: int, keep_quotes: bool = False) -> None:
    for index in range(start, end):
        if output[index] not in "\r\n" and not (
            keep_quotes and index in {start, end - 1}
        ):
            output[index] = " "


def _decode_js_string(value: str) -> str | None:
    decoded = []
    index = 0
    escapes = {
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "0": "\0",
    }
    while index < len(value):
        current = value[index]
        if current != "\\":
            decoded.append(current)
            index += 1
            continue
        index += 1
        if index >= len(value):
            return None
        escaped = value[index]
        if escaped in "\r\n":
            if escaped == "\r" and index + 1 < len(value) and value[index + 1] == "\n":
                index += 1
        elif escaped == "x":
            digits = value[index + 1 : index + 3]
            if len(digits) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{2}", digits):
                return None
            decoded.append(chr(int(digits, 16)))
            index += 2
        elif escaped == "u":
            if index + 1 < len(value) and value[index + 1] == "{":
                close = value.find("}", index + 2)
                digits = value[index + 2 : close] if close != -1 else ""
                if not digits or not re.fullmatch(r"[0-9A-Fa-f]{1,6}", digits):
                    return None
                codepoint = int(digits, 16)
                if codepoint > 0x10FFFF:
                    return None
                decoded.append(chr(codepoint))
                index = close
            else:
                digits = value[index + 1 : index + 5]
                if len(digits) != 4 or not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
                    return None
                decoded.append(chr(int(digits, 16)))
                index += 4
        else:
            decoded.append(escapes.get(escaped, escaped))
        index += 1
    return "".join(decoded)


def _slash_starts_regex(output: list[str], index: int) -> bool:
    previous = index - 1
    while previous >= 0 and output[previous].isspace():
        previous -= 1
    if previous < 0:
        return True
    if output[previous] in "([{=,:;!?&|+*%^~<>-":
        return True
    end = previous + 1
    while previous >= 0 and (output[previous].isalnum() or output[previous] in "_$"):
        previous -= 1
    word = "".join(output[previous + 1 : end])
    return word in {
        "case",
        "delete",
        "do",
        "else",
        "in",
        "instanceof",
        "new",
        "return",
        "throw",
        "typeof",
        "void",
        "yield",
        "await",
    }


def lex_js(source: str) -> JsLexResult:
    output = list(source)
    literals = []
    index = 0
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if current == "/" and following in {"/", "*"}:
            end = index + 2
            if following == "/":
                while end < len(source) and source[end] not in "\r\n":
                    end += 1
            else:
                close = source.find("*/", end)
                end = len(source) if close == -1 else close + 2
            _mask_span(output, index, end)
            index = end
            continue
        if current in {"'", '"'}:
            quote = current
            end = index + 1
            closed = False
            while end < len(source):
                if source[end] == "\\" and end + 1 < len(source):
                    end += 2
                    continue
                if source[end] == quote:
                    end += 1
                    closed = True
                    break
                if source[end] in "\r\n":
                    break
                end += 1
            if closed:
                value = _decode_js_string(source[index + 1 : end - 1])
                if value is not None:
                    literals.append(JsStringLiteral(index, end, value, quote))
                _mask_span(output, index, end, keep_quotes=True)
            else:
                _mask_span(output, index, end)
            index = end
            continue
        if current == "`":
            end = index + 1
            closed = False
            has_interpolation = False
            while end < len(source):
                if source[end] == "\\" and end + 1 < len(source):
                    end += 2
                    continue
                if source[end : end + 2] == "${":
                    has_interpolation = True
                if source[end] == "`":
                    end += 1
                    closed = True
                    break
                end += 1
            if closed and not has_interpolation:
                value = _decode_js_string(source[index + 1 : end - 1])
                if value is not None:
                    literals.append(JsStringLiteral(index, end, value, current))
            _mask_span(output, index, end, keep_quotes=closed)
            index = end
            continue
        jsx_closing_tag = (
            current == "/"
            and index > 0
            and output[index - 1] == "<"
            and (following.isalpha() or following in "_$")
        )
        if current == "/" and not jsx_closing_tag and _slash_starts_regex(output, index):
            end = index + 1
            character_class = False
            closed = False
            while end < len(source) and source[end] not in "\r\n":
                if source[end] == "\\" and end + 1 < len(source):
                    end += 2
                    continue
                if source[end] == "[":
                    character_class = True
                elif source[end] == "]":
                    character_class = False
                elif source[end] == "/" and not character_class:
                    end += 1
                    while end < len(source) and source[end].isalpha():
                        end += 1
                    closed = True
                    break
                end += 1
            if closed:
                _mask_span(output, index, end)
                index = end
                continue
        index += 1
    return JsLexResult("".join(output), tuple(literals))


STATIC_FROM_PATTERN = re.compile(
    r"(?m)^[ \t]*import\b(?!\s*\()(?P<clause>[^;]*?)\bfrom\s*(?P<quote>[\"'])"
)
SIDE_EFFECT_IMPORT_PATTERN = re.compile(
    r"(?m)^[ \t]*import\s*(?P<quote>[\"'])"
)
EXPORT_FROM_PATTERN = re.compile(
    r"(?m)^[ \t]*export\b(?P<clause>[^;]*?)\bfrom\s*(?P<quote>[\"'])"
)
DYNAMIC_IMPORT_PATTERN = re.compile(
    r"\bimport\s*\(\s*(?P<quote>[\"'])\s*(?P=quote)\s*\)"
)
REQUIRE_PATTERN = re.compile(
    r"\brequire\s*\(\s*(?P<quote>[\"'])\s*(?P=quote)\s*\)"
)


def statement_end(source: str, start: int) -> int:
    parens = brackets = braces = 0
    for index in range(start, len(source)):
        character = source[index]
        if character == "(":
            parens += 1
        elif character == ")":
            parens = max(0, parens - 1)
        elif character == "[":
            brackets += 1
        elif character == "]":
            brackets = max(0, brackets - 1)
        elif character == "{":
            braces += 1
        elif character == "}":
            braces = max(0, braces - 1)
        elif character == ";" and parens == brackets == braces == 0:
            return index
        elif character == "\n" and parens == brackets == braces == 0:
            next_statement = source[index + 1 :]
            if re.match(
                r"[ \t]*(?:(?:export\b)|(?:import\b(?!\s*\())|"
                r"(?:(?:const|let|var|function|class|type|interface|declare|"
                r"enum|namespace|module)\b))",
                next_statement,
            ):
                return index
    return len(source)


def enclosing_parentheses(source: str, position: int) -> list[int]:
    stack = []
    for index in range(position):
        if source[index] == "(":
            stack.append(index)
        elif source[index] == ")" and stack:
            stack.pop()
    return stack


def matching_parenthesis(source: str, start: int) -> int | None:
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def matching_angle_bracket(source: str, start: int) -> int | None:
    depth = 0
    for index in range(start, len(source)):
        character = source[index]
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
            if depth == 0:
                return index
    return None


def inside_typescript_generic_parameters(source: str, position: int) -> bool:
    declarations = re.compile(
        rf"\b(?:function(?:\s+{IDENTIFIER})?|class\s+{IDENTIFIER}|"
        rf"interface\s+{IDENTIFIER}|type\s+{IDENTIFIER})\s*<"
    )
    for match in declarations.finditer(source, 0, position):
        open_angle = match.end() - 1
        close_angle = matching_angle_bracket(source, open_angle)
        if close_angle is not None and open_angle < position < close_angle:
            return True

    for open_angle in (
        index for index, character in enumerate(source[:position]) if character == "<"
    ):
        close_angle = matching_angle_bracket(source, open_angle)
        if close_angle is None or not (open_angle < position < close_angle):
            continue
        tail = source[close_angle + 1 :]
        parameters = re.match(r"\s*\(", tail)
        if parameters is None:
            continue
        open_paren = close_angle + 1 + parameters.end() - 1
        close_paren = matching_parenthesis(source, open_paren)
        if close_paren is not None and re.match(
            r"\s*(?::[^=]+)?=>", source[close_paren + 1 :]
        ):
            return True
    return False


def inside_typescript_heritage_clause(source: str, position: int) -> bool:
    class_pattern = re.compile(rf"\bclass\s+{IDENTIFIER}")
    for match in class_pattern.finditer(source, 0, position):
        body = source.find("{", match.end())
        if body != -1 and position < body:
            implements = source.find("implements", match.end(), body)
            if implements != -1 and position > implements:
                return True
    interface_pattern = re.compile(rf"\binterface\s+{IDENTIFIER}")
    for match in interface_pattern.finditer(source, 0, position):
        body = source.find("{", match.end())
        if body != -1 and position < body:
            extends = source.find("extends", match.end(), body)
            if extends != -1 and position > extends:
                return True
    return False


def inside_direct_class_body(source: str, position: int) -> bool:
    class_pattern = re.compile(
        r"\bclass\s+[A-Za-z_$][A-Za-z0-9_$]*[^\{]*\{"
    )
    for match in class_pattern.finditer(source, 0, position):
        open_brace = match.end() - 1
        close_brace = matching_brace(source, open_brace)
        if close_brace is None or position >= close_brace:
            continue
        depth = 0
        for character in source[open_brace:position]:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
        if depth == 1:
            return True
    return False


def annotation_prefix_is_type(
    source: str, start: int, position: int, *, stop_at_brace: bool = False
) -> bool:
    parens = brackets = braces = 0
    for index in range(start, position):
        character = source[index]
        if character == "(":
            parens += 1
        elif character == ")":
            parens = max(0, parens - 1)
        elif character == "[":
            brackets += 1
        elif character == "]":
            brackets = max(0, brackets - 1)
        elif character == "{":
            if stop_at_brace and parens == brackets == braces == 0:
                return False
            braces += 1
        elif character == "}":
            braces = max(0, braces - 1)
        elif (
            character == "="
            and parens == brackets == braces == 0
            and source[index + 1 : index + 2] != ">"
        ):
            return False
    return True


def typescript_import_is_type_only(source: str, position: int) -> bool:
    prefix = source[:position]
    if re.search(r"\b(?:as|satisfies)\s*$", prefix):
        return True
    if inside_typescript_generic_parameters(
        source, position
    ) or inside_typescript_heritage_clause(source, position):
        return True

    variable_annotation_pattern = re.compile(
        rf"(?m)(?:^|[;\n])\s*(?:export\s+)?(?:declare\s+)?"
        rf"(?:const|let|var)\s+{IDENTIFIER}(?:\s*[?!])?\s*:"
    )
    for match in variable_annotation_pattern.finditer(source, 0, position):
        if position < statement_end(source, match.end()) and annotation_prefix_is_type(
            source, match.end(), position
        ):
            return True

    return_annotation_pattern = re.compile(
        rf"\bfunction(?:\s+{IDENTIFIER})?\s*\([^)]*\)\s*:"
    )
    for match in return_annotation_pattern.finditer(source, 0, position):
        if annotation_prefix_is_type(
            source, match.end(), position, stop_at_brace=True
        ):
            return True

    property_annotation_pattern = re.compile(
        rf"(?m)(?:^|[;{{\n])\s*"
        rf"(?:(?:public|private|protected|readonly|static|abstract|declare|"
        rf"override)\s+)*{IDENTIFIER}\s*[?!]?\s*:"
    )
    for match in property_annotation_pattern.finditer(source, 0, position):
        if (
            position < statement_end(source, match.end())
            and annotation_prefix_is_type(
                source, match.end(), position, stop_at_brace=True
            )
            and inside_direct_class_body(source, match.end())
        ):
            return True

    type_alias_pattern = re.compile(
        rf"(?m)(?:^|[;\n])\s*(?:export\s+)?type\s+{IDENTIFIER}"
        r"(?:\s*<[^;=]*>)?\s*="
    )
    for match in type_alias_pattern.finditer(source, 0, position):
        if position < statement_end(source, match.end()):
            return True

    interface_pattern = re.compile(
        rf"\binterface\s+{IDENTIFIER}(?:\s+extends\s+[^{{]+)?\s*\{{"
    )
    for match in interface_pattern.finditer(source, 0, position):
        close = matching_brace(source, match.end() - 1)
        if close is not None and position < close:
            return True

    declare_pattern = re.compile(r"(?m)(?:^|[;\n])\s*declare\b")
    for match in declare_pattern.finditer(source, 0, position):
        if position < statement_end(source, match.end()):
            return True

    for open_paren in reversed(enclosing_parentheses(source, position)):
        close_paren = matching_parenthesis(source, open_paren)
        if close_paren is None:
            continue
        before = source[max(0, open_paren - 200) : open_paren]
        after = source[close_paren + 1 : close_paren + 200]
        is_parameters = re.search(
            rf"\bfunction(?:\s+{IDENTIFIER})?\s*$", before
        ) is not None or re.match(r"\s*(?::[^=]+)?=>", after) is not None
        if not is_parameters:
            continue
        segment_start = open_paren + 1
        comma_ranges = top_level_ranges(source, segment_start, position, ",")
        if comma_ranges:
            segment_start = comma_ranges[-1][0]
        colon = top_level_character(source, segment_start, position, ":")
        equals = top_level_character(source, segment_start, position, "=")
        if colon is not None and (equals is None or colon > equals):
            return True
    return False


def js_module_references(source: str) -> tuple[JsLexResult, list[JsModuleReference]]:
    lexed = lex_js(source)
    literals = {literal.start: literal for literal in lexed.literals}
    references = []
    seen = set()
    patterns = (
        ("import", STATIC_FROM_PATTERN),
        ("import", SIDE_EFFECT_IMPORT_PATTERN),
        ("export", EXPORT_FROM_PATTERN),
        ("dynamic", DYNAMIC_IMPORT_PATTERN),
        ("require", REQUIRE_PATTERN),
    )
    for kind, pattern in patterns:
        for match in pattern.finditer(lexed.executable):
            literal = literals.get(match.start("quote"))
            key = (kind, literal.start if literal else -1)
            if literal is None or key in seen:
                continue
            seen.add(key)
            clause = match.groupdict().get("clause") or ""
            type_only = kind == "dynamic" and typescript_import_is_type_only(
                lexed.executable, match.start()
            )
            references.append(
                JsModuleReference(kind, literal.value, clause, type_only)
            )
    return lexed, references


IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"


def import_bindings(reference: JsModuleReference) -> dict[str, str]:
    if reference.kind != "import" or not reference.clause.strip():
        return {}
    clause = reference.clause.strip()
    if re.match(r"^type\b", clause):
        return {}
    bindings = {}
    default = re.match(rf"^({IDENTIFIER})\b", clause)
    if default:
        bindings["default"] = default.group(1)
    namespace = re.search(rf"\*\s+as\s+({IDENTIFIER})", clause)
    if namespace:
        bindings["*"] = namespace.group(1)
    named = re.search(r"\{(.*?)\}", clause, re.S)
    if named:
        for item in named.group(1).split(","):
            item = item.strip()
            if re.fullmatch(
                rf"type\s+{IDENTIFIER}(?:\s+as\s+{IDENTIFIER})?", item
            ):
                continue
            match = re.fullmatch(
                rf"({IDENTIFIER})(?:\s+as\s+({IDENTIFIER}))?", item
            )
            if match:
                bindings[match.group(1)] = match.group(2) or match.group(1)
    return bindings


def is_runtime_import(reference: JsModuleReference) -> bool:
    return reference.kind == "import" and bool(import_bindings(reference))


def is_runtime_export(reference: JsModuleReference) -> bool:
    if reference.kind != "export":
        return False
    clause = reference.clause.strip()
    if re.match(r"^type\b", clause):
        return False
    named = re.fullmatch(r"\{(.*?)\}", clause, re.S)
    if named:
        return any(
            item.strip() and not re.match(r"^type\b", item.strip())
            for item in named.group(1).split(",")
        )
    return True


def imported_locals(
    references: list[JsModuleReference], specifier: str, exported: str | None = None
) -> set[str]:
    result = set()
    for reference in references:
        if reference.kind != "import" or reference.specifier != specifier:
            continue
        bindings = import_bindings(reference)
        if exported is None:
            result.update(bindings.values())
        elif exported in bindings:
            result.add(bindings[exported])
    return result


def binding_is_shadowed_at(
    executable: str, name: str, position: int
) -> bool:
    use_scope = brace_scope_at(executable, position)
    shadows = []

    for match in VARIABLE_BINDING_PATTERN.finditer(executable):
        if match.group(2) == name:
            shadows.append(
                (
                    brace_scope_at(executable, match.start()),
                    None,
                )
            )

    for match in DESTRUCTURED_VARIABLE_PATTERN.finditer(executable):
        if name in parameter_binding_names(match.group(1)):
            shadows.append(
                (
                    brace_scope_at(executable, match.start()),
                    None,
                )
            )

    for pattern in (FUNCTION_DECLARATION_PATTERN, CLASS_DECLARATION_PATTERN):
        for match in pattern.finditer(executable):
            if match.group(1) == name:
                shadows.append(
                    (
                        brace_scope_at(executable, match.start()),
                        None,
                    )
                )

    for pattern in (
        FUNCTION_PARAMETER_PATTERN,
        METHOD_PARAMETER_PATTERN,
        CATCH_BINDING_PATTERN,
    ):
        for match in pattern.finditer(executable):
            if name not in parameter_binding_names(match.group(1)):
                continue
            body_start = match.end() - 1
            body_end = matching_brace(executable, body_start)
            shadows.append(
                (
                    brace_scope_at(executable, body_start + 1),
                    (
                        body_start,
                        body_end if body_end is not None else len(executable),
                    ),
                )
            )

    for pattern in (PAREN_ARROW_PARAMETER_PATTERN, SINGLE_ARROW_PARAMETER_PATTERN):
        for match in pattern.finditer(executable):
            if name not in parameter_binding_names(match.group(1)):
                continue
            body_start, body_end, parameter_scope = arrow_body_range(
                executable, match.end()
            )
            shadows.append((parameter_scope, (body_start, body_end)))

    for declared_scope, visibility in shadows:
        if use_scope[: len(declared_scope)] != declared_scope:
            continue
        if visibility is not None and not (
            visibility[0] <= position < visibility[1]
        ):
            continue
        return True
    return False


def imported_binding_is_rendered_or_called(
    lexed: JsLexResult, local: str, *, namespace: bool
) -> bool:
    escaped = re.escape(local)
    patterns = (
        (
            re.compile(rf"<\s*{escaped}\s*\.[A-Za-z_$][A-Za-z0-9_$]*\b")
            if namespace
            else re.compile(rf"<\s*{escaped}(?=\s|/|>)")
        ),
        (
            re.compile(rf"\b{escaped}\s*\.[A-Za-z_$][A-Za-z0-9_$]*\s*\(")
            if namespace
            else re.compile(rf"\b{escaped}\s*\(")
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(lexed.executable):
            prefix = lexed.executable[max(0, match.start() - 16) : match.start()]
            if re.search(r"\b(?:function|class)\s*$", prefix):
                continue
            if not binding_is_shadowed_at(
                lexed.executable, local, match.start()
            ):
                return True
    return False


def component_source_is_runtime_used(
    root: Path | None,
    resolved_files: dict[str, Path],
    reachable: set[str],
    component_source: str,
    absolute_root: Path | None,
) -> bool:
    if root is None or absolute_root is None:
        return False
    for importer_relative in reachable:
        importer = resolved_files.get(importer_relative)
        if importer is None or importer.suffix.casefold() not in {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
        }:
            continue
        try:
            source = importer.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        lexed, references = js_module_references(source)
        for reference in references:
            bindings = import_bindings(reference)
            if not bindings:
                continue
            resolved, error = resolve_local_dependency(
                root, importer, reference.specifier, absolute_root
            )
            if error is not None or resolved != component_source:
                continue
            for exported, local in bindings.items():
                if imported_binding_is_rendered_or_called(
                    lexed, local, namespace=exported == "*"
                ):
                    return True
    return False


def project_preview_route_is_bound(
    path: Path | None, preview_route: str
) -> bool:
    if path is None or path.suffix.casefold() not in {
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
    }:
        return False
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    lexed = lex_js(source)
    for literal in lexed.literals:
        if literal.value != preview_route:
            continue
        prefix = lexed.executable[: literal.start]
        if re.search(
            r"\b(?:export\s+)?const\s+(?:route|previewRoute)\s*=\s*$",
            prefix,
        ) or re.search(r"\bpath\s*:\s*$", prefix):
            return True
    return False


DEFAULT_APP_FUNCTION_PATTERN = re.compile(
    r"\bexport\s+default\s+function\s+App\s*\(([^)]*)\)\s*"
    r"(?::[^\{;]+)?\{"
)


def scope_declares_name(
    executable: str, name: str, scope: tuple[int, ...]
) -> bool:
    for match in VARIABLE_BINDING_PATTERN.finditer(executable):
        if (
            match.group(2) == name
            and brace_scope_at(executable, match.start()) == scope
        ):
            return True
    for match in DESTRUCTURED_VARIABLE_PATTERN.finditer(executable):
        if (
            name in parameter_binding_names(match.group(1))
            and brace_scope_at(executable, match.start()) == scope
        ):
            return True
    for pattern in (FUNCTION_DECLARATION_PATTERN, CLASS_DECLARATION_PATTERN):
        for match in pattern.finditer(executable):
            if (
                match.group(1) == name
                and brace_scope_at(executable, match.start()) == scope
            ):
                return True
    return False


def route_table_maps_preview_component(
    source: str,
    lexed: JsLexResult,
    object_start: int,
    object_end: int,
    preview_route: str,
    preview_locals: set[str],
) -> bool:
    for property_start, property_end in top_level_ranges(
        lexed.executable, object_start + 1, object_end, ","
    ):
        colon = top_level_character(
            lexed.executable, property_start, property_end, ":"
        )
        if colon is None:
            continue
        keys = [
            literal
            for literal in lexed.literals
            if property_start <= literal.start < colon
        ]
        if (
            len(keys) != 1
            or keys[0].value != preview_route
            or source[property_start:colon].strip()
            != source[keys[0].start : keys[0].end]
        ):
            continue
        value = lexed.executable[colon + 1 : property_end]
        for local in preview_locals:
            match = re.fullmatch(rf"\s*({re.escape(local)})\s*", value)
            if match is not None and not binding_is_shadowed_at(
                lexed.executable, local, colon + 1 + match.start(1)
            ):
                return True
    return False


def app_consumes_route_table(
    lexed: JsLexResult, app_match: re.Match, table: str
) -> bool:
    body_start = app_match.end() - 1
    body_end = matching_brace(lexed.executable, body_start)
    if body_end is None:
        return False
    app_scope = brace_scope_at(lexed.executable, body_start + 1)
    if table in parameter_binding_names(app_match.group(1)) or scope_declares_name(
        lexed.executable, table, app_scope
    ):
        return False

    selector_pattern = re.compile(
        rf"\b(?:const|let|var)\s+({IDENTIFIER})\s*"
        rf"(?::[^=;\n]+)?=\s*{re.escape(table)}\s*\["
    )
    for selector in selector_pattern.finditer(
        lexed.executable, body_start + 1, body_end
    ):
        if brace_scope_at(lexed.executable, selector.start()) != app_scope:
            continue
        selected = re.escape(selector.group(1))
        uses = (
            re.compile(rf"<\s*{selected}(?=\s|/|>)"),
            re.compile(rf"\b{selected}\s*\("),
        )
        for pattern in uses:
            for use in pattern.finditer(
                lexed.executable, selector.end(), body_end
            ):
                if brace_scope_at(lexed.executable, use.start()) == app_scope:
                    return True
    return False


def standalone_route_maps_preview_component(
    root: Path,
    app_path: Path,
    app_source: str,
    preview_path: str,
    preview_route: str,
    absolute_root: Path,
) -> bool:
    _, references = js_module_references(app_source)
    preview_locals = set()
    for reference in references:
        bindings = import_bindings(reference)
        if not bindings:
            continue
        resolved, error = resolve_local_dependency(
            root, app_path, reference.specifier, absolute_root
        )
        if error is None and resolved == preview_path:
            preview_locals.update(bindings.values())
    if not preview_locals:
        return False
    lexed = lex_js(app_source)
    app_matches = [
        match
        for match in DEFAULT_APP_FUNCTION_PATTERN.finditer(lexed.executable)
        if brace_scope_at(lexed.executable, match.start()) == ()
    ]
    if len(app_matches) != 1:
        return False

    route_tables = set()
    for match in CONST_OBJECT_PATTERN.finditer(lexed.executable):
        if brace_scope_at(lexed.executable, match.start()) != ():
            continue
        object_start = match.end() - 1
        object_end = matching_brace(lexed.executable, object_start)
        if object_end is not None and route_table_maps_preview_component(
            app_source,
            lexed,
            object_start,
            object_end,
            preview_route,
            preview_locals,
        ):
            route_tables.add(match.group(1))

    return any(
        app_consumes_route_table(lexed, app_matches[0], table)
        for table in route_tables
    )


CSS_IMPORT_PATTERN = re.compile(
    r"@import\s+(?:url\(\s*)?[\"']?([^\"')\s;]+)", re.IGNORECASE
)
CSS_URL_PATTERN = re.compile(r"url\(\s*[\"']?([^\"')]+)", re.IGNORECASE)
NEW_URL_PATTERN = re.compile(
    r"\bnew\s+URL\s*\(\s*(?P<quote>[\"'])\s*(?P=quote)\s*,"
    r"\s*import\s*\.\s*meta\s*\.\s*url\s*\)"
)
JSX_TAG_PATTERN = re.compile(
    r"<[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)?\b(?P<attrs>[^<>]*?)>",
    re.S,
)
JSX_ASSET_ATTRIBUTE_PATTERN = re.compile(
    r"\b(?P<name>src|href|poster|srcSet)\s*=\s*"
    r"(?P<brace>\{\s*)?(?P<quote>[\"'])\s*(?P=quote)"
    r"(?(brace)\s*\})(?=\s|/|$)",
    re.IGNORECASE,
)
SRCSET_LOCAL_REFERENCE_PATTERN = re.compile(
    r"(?:^|[,\s])(?P<reference>\.{1,2}/[^,\s]+)"
)


def normalize_local_asset_reference(value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith(("#", "//")):
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        return None
    value = value.split("#", 1)[0].split("?", 1)[0].strip()
    if not value:
        return None
    if value.startswith(("./", "../", "/")):
        return value
    return f"./{value}"


def srcset_references(value: str) -> list[str]:
    return list(
        dict.fromkeys(
            match.group("reference")
            for match in SRCSET_LOCAL_REFERENCE_PATTERN.finditer(value)
        )
    )


def js_render_dependencies(lexed: JsLexResult) -> list[str]:
    literals = {literal.start: literal for literal in lexed.literals}
    references = []
    for match in NEW_URL_PATTERN.finditer(lexed.executable):
        literal = literals.get(match.start("quote"))
        if literal is not None:
            normalized = normalize_local_asset_reference(literal.value)
            if normalized is not None:
                references.append(normalized)
    for tag in JSX_TAG_PATTERN.finditer(lexed.executable):
        attrs_start = tag.start("attrs")
        attrs = tag.group("attrs")
        for match in JSX_ASSET_ATTRIBUTE_PATTERN.finditer(attrs):
            literal = literals.get(attrs_start + match.start("quote"))
            if literal is None:
                continue
            if match.group("name").casefold() == "srcset":
                references.extend(srcset_references(literal.value))
            else:
                normalized = normalize_local_asset_reference(literal.value)
                if normalized is not None:
                    references.append(normalized)
    return list(dict.fromkeys(references))


def js_dependencies(source: str) -> list[str]:
    lexed, references = js_module_references(source)
    values = [reference.specifier for reference in references]
    values.extend(js_render_dependencies(lexed))
    return list(dict.fromkeys(values))


def js_runtime_dependencies(source: str) -> list[str]:
    lexed, references = js_module_references(source)
    values = []
    for reference in references:
        if reference.type_only:
            continue
        if reference.kind == "import" and reference.clause.strip():
            if not is_runtime_import(reference):
                continue
        elif reference.kind == "export" and not is_runtime_export(reference):
            continue
        values.append(reference.specifier)
    values.extend(js_render_dependencies(lexed))
    return list(dict.fromkeys(values))


class _HtmlDependencyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources = []
        self.references = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = {name.casefold(): value for name, value in attrs}
        if tag == "script" and (values.get("type") or "").casefold() == "module":
            source = values.get("src")
            if source:
                self.sources.append(source)
                self.references.append(source)
        attribute_names = {
            "img": ("src", "srcset"),
            "source": ("src", "srcset"),
            "video": ("src", "poster"),
            "link": ("href",),
        }.get(tag, ())
        for name in attribute_names:
            value = values.get(name)
            if not value:
                continue
            if name == "srcset":
                self.references.extend(srcset_references(value))
            else:
                normalized = normalize_local_asset_reference(value)
                if normalized is not None:
                    self.references.append(normalized)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)


def parse_html_dependencies(source: str) -> _HtmlDependencyParser:
    parser = _HtmlDependencyParser()
    try:
        parser.feed(source)
        parser.close()
    except Exception:
        return _HtmlDependencyParser()
    return parser


def html_module_sources(source: str) -> list[str]:
    return parse_html_dependencies(source).sources


def local_dependencies(path: Path) -> list[str]:
    suffix = path.suffix.casefold()
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return js_dependencies(source)
    if suffix == ".css":
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        values = CSS_IMPORT_PATTERN.findall(source) + CSS_URL_PATTERN.findall(source)
        normalized = [normalize_local_asset_reference(value) for value in values]
        return list(dict.fromkeys(value for value in normalized if value is not None))
    if suffix in {".html", ".htm"}:
        return list(dict.fromkeys(parse_html_dependencies(source).references))
    return []


def runtime_local_dependencies(path: Path) -> list[str]:
    if path.suffix.casefold() not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return local_dependencies(path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    return js_runtime_dependencies(source)


def _is_local_specifier(specifier: str) -> bool:
    return specifier.startswith(("./", "../", "/")) and not specifier.startswith("//")


def resolve_local_dependency(
    root: Path, importer: Path, specifier: str, absolute_root: Path
) -> tuple[str | None, str | None]:
    if not _is_local_specifier(specifier):
        return None, None
    if any(marker in specifier for marker in ("?", "#", "\x00")) or "\\" in specifier:
        return None, f"unsafe local dependency: {specifier}"
    base = absolute_root / specifier.lstrip("/") if specifier.startswith("/") else importer.parent / specifier
    candidates = [base]
    if not base.suffix:
        candidates.extend(Path(f"{base}{extension}") for extension in LOCAL_SCRIPT_EXTENSIONS)
        candidates.extend(base / f"index{extension}" for extension in LOCAL_SCRIPT_EXTENSIONS)
    root_resolved = root.resolve(strict=True)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        try:
            relative = resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            return None, f"local dependency must be contained in preview root: {specifier}"
        if resolved.is_file():
            return relative, None
    return None, f"missing local dependency: {specifier}"


def dependency_closure(
    root: Path,
    preview_files: list[str],
    roots: list[str],
    absolute_root: Path | None = None,
    runtime_only: bool = False,
) -> tuple[set[str], list[str]]:
    errors = []
    listed = set(preview_files)
    pending = list(dict.fromkeys(roots))
    visited = set()
    reachable = set()
    absolute_root = absolute_root or root
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        if relative not in listed:
            errors.append(f"unlisted local dependency: {relative}")
            continue
        path = resolved_scoped_path(root, relative)
        if path is None or not path.is_file():
            errors.append(f"preview dependency must be contained and existing: {relative}")
            continue
        reachable.add(relative)
        dependencies = (
            runtime_local_dependencies(path) if runtime_only else local_dependencies(path)
        )
        for specifier in dependencies:
            resolved, error = resolve_local_dependency(root, path, specifier, absolute_root)
            if error:
                errors.append(f"{relative}: {error}")
            elif resolved is not None:
                if resolved not in listed:
                    errors.append(f"unlisted local dependency from {relative}: {resolved}")
                else:
                    pending.append(resolved)
    return reachable, errors


def dependency_closure_errors(
    root: Path,
    preview_files: list[str],
    roots: list[str],
    absolute_root: Path | None = None,
) -> list[str]:
    return dependency_closure(root, preview_files, roots, absolute_root)[1]


def _standalone_topology_errors(
    root: Path, preview_files: list[str], resolved_files: dict[str, Path]
) -> list[str]:
    errors = []
    packages = [path for path in preview_files if PurePosixPath(path).name == "package.json"]
    if len(packages) != 1:
        return ["standalone preview_files must include exactly one package.json"]
    package_path = PurePosixPath(packages[0])
    prefix = "" if str(package_path.parent) == "." else f"{package_path.parent}/"
    required = {
        "index": f"{prefix}index.html",
        "vite": f"{prefix}vite.config.ts",
        "tsconfig": f"{prefix}tsconfig.json",
        "main": f"{prefix}src/main.tsx",
        "app": f"{prefix}src/App.tsx",
    }
    missing = [path for path in required.values() if path not in preview_files]
    if missing:
        errors.append(
            "standalone preview_files missing complete Vite React TS files: "
            + ", ".join(missing)
        )
        return errors
    try:
        package = json.loads(resolved_files[packages[0]].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        package = None
    if not isinstance(package, dict):
        errors.append("standalone package.json must be valid JSON object")
    else:
        scripts = package.get("scripts")
        valid_scripts = isinstance(scripts, dict)
        if valid_scripts:
            try:
                dev_tokens = shlex.split(scripts.get("dev", ""))
                build_tokens = shlex.split(scripts.get("build", ""))
            except (TypeError, ValueError):
                dev_tokens = build_tokens = []
            valid_scripts = (
                bool(dev_tokens)
                and dev_tokens[0] == "vite"
                and len(build_tokens) >= 2
                and build_tokens[:2] == ["vite", "build"]
            )
        if not valid_scripts:
            errors.append(
                "standalone package.json scripts must execute vite and vite build"
            )
        dependencies = package.get("dependencies")
        if not isinstance(dependencies, dict) or any(
            not isinstance(dependencies.get(name), str)
            or not dependencies[name].strip()
            for name in ("react", "react-dom")
        ):
            errors.append("standalone package.json requires react and react-dom dependencies")
        dev_dependencies = package.get("devDependencies")
        if not isinstance(dev_dependencies, dict) or any(
            not isinstance(dev_dependencies.get(name), str)
            or not dev_dependencies[name].strip()
            for name in ("vite", "typescript", "@vitejs/plugin-react")
        ):
            errors.append(
                "standalone package.json requires vite, typescript, and @vitejs/plugin-react devDependencies"
            )
    try:
        index = resolved_files[required["index"]].read_text(encoding="utf-8")
        main = resolved_files[required["main"]].read_text(encoding="utf-8")
        app = resolved_files[required["app"]].read_text(encoding="utf-8")
        vite = resolved_files[required["vite"]].read_text(encoding="utf-8")
        tsconfig = json.loads(
            resolved_files[required["tsconfig"]].read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError, UnicodeError):
        errors.append("standalone Vite React TS files must be readable and tsconfig.json valid")
        return errors
    if "/src/main.tsx" not in html_module_sources(index):
        errors.append("standalone index.html must load /src/main.tsx as a module")
    main_lexed, main_references = js_module_references(main)
    react_locals = imported_locals(main_references, "react")
    root_locals = imported_locals(
        main_references, "react-dom/client", "createRoot"
    )
    app_locals = set()
    for app_specifier in ("./App", "./App.tsx"):
        app_locals.update(imported_locals(main_references, app_specifier))
    bound_mount = any(
        re.search(
            rf"\b{re.escape(root_local)}\s*\([^;{{}}]*\)\s*\.\s*render\s*"
            rf"\(\s*<\s*{re.escape(app_local)}\b",
            main_lexed.executable,
            re.S,
        )
        for root_local in root_locals
        for app_local in app_locals
    )
    if not react_locals or not root_locals or not app_locals or not bound_mount:
        errors.append("standalone src/main.tsx must mount the React App with createRoot")
    executable_app = lex_js(app).executable
    if (
        not re.search(r"\bexport\s+(?:default\b|(?:const|function|class)\s+\w+)", executable_app)
        or not re.search(
            r"(?:<>|<[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)?(?:\s|/?>))",
            executable_app,
        )
    ):
        errors.append("standalone src/App.tsx must export and render a JSX component")
    vite_lexed, vite_references = js_module_references(vite)
    config_locals = imported_locals(vite_references, "vite", "defineConfig")
    plugin_locals = imported_locals(vite_references, "@vitejs/plugin-react")
    configured = any(
        re.search(
            rf"\bexport\s+default\s+{re.escape(config_local)}\s*\("
            rf"(?=[\s\S]*?\bplugins\s*:\s*\[[^]]*\b{re.escape(plugin_local)}\s*\()",
            vite_lexed.executable,
        )
        for config_local in config_locals
        for plugin_local in plugin_locals
    )
    if not config_locals or not plugin_locals or not configured:
        errors.append(
            "standalone vite.config.ts must import defineConfig/react and export configured plugins"
        )
    compiler_options = tsconfig.get("compilerOptions") if isinstance(tsconfig, dict) else None
    if (
        not isinstance(compiler_options, dict)
        or not isinstance(compiler_options.get("jsx"), str)
        or not compiler_options["jsx"].strip()
        or not isinstance(compiler_options.get("module"), str)
        or not compiler_options["module"].strip()
    ):
        errors.append("standalone tsconfig.json requires jsx and module compiler settings")
    return errors


def find_secret_errors(value, label: str) -> list[str]:
    errors = []

    def walk(current, path: str) -> None:
        if isinstance(current, dict):
            for key, nested in current.items():
                key_label = str(key)
                normalized = re.sub(r"[^a-z0-9]", "", key_label.casefold())
                nested_path = f"{path}.{key_label}"
                if normalized in SECRET_KEYS or SECRET_KEY_PATTERN.fullmatch(normalized):
                    errors.append(f"{nested_path} has secret-like key")
                walk(nested, nested_path)
        elif isinstance(current, list):
            for index, nested in enumerate(current):
                walk(nested, f"{path}[{index}]")
        elif isinstance(current, str):
            if has_bearer_secret(current) or any(
                pattern.search(current) for pattern in SECRET_VALUE_PATTERNS
            ):
                errors.append(f"{path} has secret-like value")

    walk(value, label)
    return errors


def axis_value(item, axis):
    axes = item.get("axes")
    value = axes.get(axis) if isinstance(axes, dict) else None
    return value.strip().casefold() if isinstance(value, str) else None


def validate_direction_derivation(
    item: dict,
    index: int,
    previous_items: dict[str, dict],
    errors: list[str],
) -> None:
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in {"primary", "derived"}:
        errors.append(f"directions[{index}] kind must be primary or derived")
        return
    if kind == "primary":
        for field in ("derived_from_ids", "combined_properties"):
            if field in item:
                errors.append(f"directions[{index}] primary must omit {field}")
        return

    sources = item.get("derived_from_ids")
    valid_sources = (
        isinstance(sources, list)
        and bool(sources)
        and all(isinstance(source, str) and source.strip() for source in sources)
        and len(set(sources)) == len(sources)
    )
    source_ids = set(sources) if valid_sources else None
    if not valid_sources:
        errors.append(
            f"directions[{index}] derived_from_ids must be a non-empty list of unique non-empty strings"
        )
    else:
        unavailable = source_ids - set(previous_items)
        if unavailable:
            errors.append(
                f"directions[{index}] derived_from_ids must refer only to previously declared direction IDs: {', '.join(sorted(unavailable))}"
            )

    properties = item.get("combined_properties")
    if not isinstance(properties, dict) or not properties:
        errors.append(
            f"directions[{index}] combined_properties must be a non-empty object"
        )
        return
    contributing_sources = set()
    for key, source in properties.items():
        if key not in AXES:
            errors.append(
                f"directions[{index}] combined_properties has unsupported key: {key}"
            )
        if not isinstance(source, str) or not source.strip():
            errors.append(
                f"directions[{index}] combined_properties {key} must name a non-empty source ID"
            )
        elif source_ids is not None and source not in source_ids:
            errors.append(
                f"directions[{index}] combined_properties {key} source is not in derived_from_ids: {source}"
            )
        elif key in AXES and source in previous_items:
            contributing_sources.add(source)
            if axis_value(item, key) != axis_value(previous_items[source], key):
                errors.append(
                    f"directions[{index}] derived axis {key} must match source {source}"
                )
    if source_ids is not None:
        for source in sorted(source_ids - contributing_sources):
            errors.append(
                f"directions[{index}] source must contribute at least one axis: {source}"
            )


def validate_sources(items, label: str, errors: list[str]) -> None:
    if not isinstance(items, list) or not items:
        errors.append(f"{label} must be a non-empty list")
        return
    identifiers = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        for field in ("id", "title", "source_url", "source_type"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{label}[{index}] missing {field}")
        identifier = item.get("id")
        if isinstance(identifier, str) and identifier.strip():
            if identifier in identifiers:
                errors.append(f"duplicate {label} id: {identifier}")
            identifiers.add(identifier)
        if not valid_url(item.get("source_url")):
            errors.append(f"{label}[{index}] source_url must be http(s)")


def validate_research(run_dir: Path) -> list[str]:
    errors = []
    references = read_json(run_dir, "references.json", errors)
    evidence = read_json(run_dir, "evidence.json", errors)
    if references is not None:
        errors.extend(find_secret_errors(references, "references"))
        validate_sources(references, "references", errors)
        for index, item in enumerate(references if isinstance(references, list) else []):
            if not isinstance(item, dict):
                continue
            for field in ("captured_at", "relevance"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"references[{index}] missing {field}")
            if "capture_path" in item and not valid_artifact_ref(item["capture_path"]):
                errors.append(f"references[{index}] capture_path must be a safe relative path")
            observations = item.get("observations", {})
            if not isinstance(observations, dict):
                errors.append(f"references[{index}] observations must be an object")
                observations = {}
            missing = [axis for axis in AXES if not observations.get(axis)]
            if missing:
                errors.append(f"references[{index}] missing observations: {', '.join(missing)}")
    if evidence is not None:
        errors.extend(find_secret_errors(evidence, "evidence"))
        validate_sources(evidence, "evidence", errors)
        for index, item in enumerate(evidence if isinstance(evidence, list) else []):
            if not isinstance(item, dict):
                continue
            source_type = item.get("source_type")
            if not isinstance(source_type, str) or source_type not in {
                "official",
                "research",
                "observed",
            }:
                errors.append(f"evidence[{index}] has unsupported source_type")
            for field in (
                "problem",
                "publisher_or_author",
                "summary",
                "application",
                "limitations",
            ):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"evidence[{index}] missing {field}")
    return errors


def validate_directions(run_dir: Path) -> list[str]:
    errors = []
    evidence = read_json(run_dir, "evidence.json", errors)
    directions = read_json(run_dir, "directions.json", errors)
    if evidence is not None:
        errors.extend(find_secret_errors(evidence, "evidence"))
    if directions is not None:
        errors.extend(find_secret_errors(directions, "directions"))
    if evidence is not None and not isinstance(evidence, list):
        errors.append("evidence.json must be a list")
        evidence = []
    evidence_ids = set()
    evidence_types = {}
    for index, item in enumerate(evidence or []):
        if not isinstance(item, dict):
            errors.append(f"evidence[{index}] must be an object")
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"evidence[{index}] missing id")
        elif identifier in evidence_ids:
            errors.append(f"duplicate evidence id: {identifier}")
        else:
            evidence_ids.add(identifier)
            evidence_types[identifier] = item.get("source_type")
    if not isinstance(directions, list) or len(directions) < 5:
        errors.append("directions must contain at least five items")
        return errors
    ids = set()
    previous_items = {}
    for index, item in enumerate(directions):
        if not isinstance(item, dict):
            errors.append(f"directions[{index}] must be an object")
            continue
        for field in (
            "id",
            "name",
            "concept",
            "ux_problem",
            "evidence_application",
            "tradeoffs",
            "implementation_difficulty",
            "implementation_risks",
        ):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"directions[{index}] missing {field}")
        identifier = item.get("id")
        if isinstance(identifier, str) and identifier.strip():
            if identifier in ids:
                errors.append(f"duplicate direction id: {identifier}")
            ids.add(identifier)
        validate_direction_derivation(item, index, previous_items, errors)
        axes = item.get("axes", {})
        if not isinstance(axes, dict):
            errors.append(f"directions[{index}] axes must be an object")
            axes = {}
        missing_axes = [axis for axis in AXES if axis not in axes]
        if missing_axes:
            errors.append(f"directions[{index}] missing axes: {', '.join(missing_axes)}")
        for axis in AXES:
            if axis in axes and (
                not isinstance(axes[axis], str) or not axes[axis].strip()
            ):
                errors.append(
                    f"directions[{index}] axis {axis} must be a non-empty string"
                )
        links = item.get("evidence_ids", [])
        if (
            not isinstance(links, list)
            or not links
            or any(not isinstance(link, str) or not link.strip() for link in links)
        ):
            errors.append(
                f"directions[{index}] evidence_ids must be a non-empty list of non-empty strings"
            )
        elif set(links) - evidence_ids:
            errors.append(f"directions[{index}] has missing or unknown evidence_ids")
        elif not any(evidence_types.get(link) == "official" for link in links):
            errors.append(
                f"directions[{index}] must link at least one official evidence item"
            )
        if "baseline_exceptions" not in item:
            errors.append(f"directions[{index}] missing baseline_exceptions")
        else:
            exceptions = item["baseline_exceptions"]
            if not isinstance(exceptions, list):
                errors.append(
                    f"directions[{index}] baseline_exceptions must be a list"
                )
            else:
                for exception_index, exception in enumerate(exceptions):
                    if not isinstance(exception, dict):
                        errors.append(
                            f"directions[{index}] baseline_exceptions[{exception_index}] must be an object"
                        )
                        continue
                    for field in ("constraint", "justification"):
                        value = exception.get(field)
                        if not isinstance(value, str) or not value.strip():
                            errors.append(
                                f"directions[{index}] baseline_exceptions[{exception_index}] missing {field}"
                            )
        if isinstance(identifier, str) and identifier.strip() and identifier not in previous_items:
            previous_items[identifier] = item
    valid_directions = [item for item in directions if isinstance(item, dict)]
    for left, right in combinations(valid_directions, 2):
        difference = sum(axis_value(left, axis) != axis_value(right, axis) for axis in AXES)
        if difference < 3:
            errors.append(f"{left.get('id')} and {right.get('id')} differ on fewer than three axes")
    return errors


def approved_direction_ids(run, errors: list[str]):
    values = run.get("approved_direction_ids")
    if not isinstance(values, list) or not values:
        errors.append("run.json approved_direction_ids must be a non-empty list")
        return None
    string_values = [
        value for value in values if isinstance(value, str) and value.strip()
    ]
    invalid = len(string_values) != len(values)
    duplicate = len(set(string_values)) != len(string_values)
    if invalid:
        errors.append("run.json approved_direction_ids must contain non-empty strings")
    if duplicate:
        errors.append("run.json approved_direction_ids must contain unique values")
    return None if invalid or duplicate else set(string_values)


def positive_integer_field(run: dict, field: str, errors: list[str]):
    value = run.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"run.json {field} must be a positive integer")
        return None
    return value


def validate_budget_approval(
    run: dict,
    budget: int | None,
    max_attempts: int | None,
    errors: list[str],
) -> None:
    if budget is None or max_attempts is None:
        return
    expanded = budget > 5 or max_attempts > 2
    approval = run.get("budget_expansion_approved_at")
    if expanded and not valid_rfc3339(approval):
        errors.append(
            "run.json expanded budget requires valid budget_expansion_approved_at"
        )
    elif not expanded and "budget_expansion_approved_at" in run:
        errors.append(
            "run.json budget_expansion_approved_at requires an expanded budget"
        )


def _valid_provider_output_ref(value) -> bool:
    return (
        isinstance(value, str)
        and PROVIDER_OUTPUT_PATTERN.fullmatch(value) is not None
        and not find_secret_errors(value, "provider-output")
    )


def _mockup_manifest_errors(
    run_dir: Path,
    *,
    require_success: bool,
    allow_initial_pending: bool,
) -> list[str]:
    errors = []
    run = read_json(run_dir, "run.json", errors)
    manifest = read_json(run_dir, "mockup-manifest.json", errors)
    if run is None or manifest is None:
        return errors
    if not isinstance(run, dict):
        errors.append("run.json must be an object")
        return errors
    if not isinstance(manifest, dict) or not isinstance(manifest.get("mockups"), list):
        errors.append("mockup-manifest.json must contain a mockups list")
        return errors
    errors.extend(find_secret_errors(run, "run"))
    errors.extend(find_secret_errors(manifest, "mockup-manifest"))
    legacy_generation_keys = sorted(LEGACY_GENERATION_ACCOUNTING_KEYS.intersection(run))
    if legacy_generation_keys:
        errors.append(
            "run.json legacy generation accounting keys are forbidden; migrate generation "
            "accounting to mockup-manifest.json: " + ", ".join(legacy_generation_keys)
        )
    approved = approved_direction_ids(run, errors)
    budget = positive_integer_field(run, "generation_budget", errors)
    max_attempts = positive_integer_field(run, "max_attempts_per_direction", errors)
    validate_budget_approval(run, budget, max_attempts, errors)
    target_viewports = normalized_string_list(
        run.get("target_viewports"), "run.json target_viewports", errors
    )
    if manifest.get("schema_version") != 1:
        errors.append("mockup-manifest.json schema_version must be 1")
    attempts_used = manifest.get("generation_attempts_used")
    if (
        not isinstance(attempts_used, int)
        or isinstance(attempts_used, bool)
        or attempts_used < 0
    ):
        errors.append(
            "mockup-manifest generation_attempts_used must be a non-negative integer"
        )
        attempts_used = None
    authorized_at = manifest.get("last_generation_authorized_at")
    authorized_direction = manifest.get("last_generation_direction_id")
    if (authorized_at is None) != (authorized_direction is None):
        errors.append("mockup-manifest generation audit fields must both be null or populated")
    if attempts_used is not None and (attempts_used == 0) != (authorized_at is None):
        errors.append("mockup-manifest generation use and audit fields must agree")
    if authorized_at is not None and not valid_rfc3339(authorized_at):
        errors.append("mockup-manifest last_generation_authorized_at must be RFC3339")
    if authorized_direction is not None and (
        not isinstance(authorized_direction, str) or not authorized_direction.strip()
    ):
        errors.append("mockup-manifest last_generation_direction_id must be nonblank")
    elif (
        authorized_direction is not None
        and approved is not None
        and authorized_direction not in approved
    ):
        errors.append("mockup-manifest audit must name an approved direction")
    if (
        attempts_used is not None
        and approved is not None
        and max_attempts is not None
        and attempts_used > len(approved) * max_attempts
    ):
        errors.append("mockup-manifest generation attempts exceed authorization ceiling")
    mockups = manifest["mockups"]
    if budget is not None and len(mockups) > budget:
        errors.append(
            f"mockup-manifest mockups length {len(mockups)} exceeds generation_budget {budget}"
        )
    successful = set()
    seen_direction_ids = set()
    seen_viewports = set()
    attempt_total = 0
    attempt_counts_by_direction = {}
    code_preview_paths = {}
    code_preview_routes = {}
    code_preview_screenshots = {}
    code_preview_topologies = {}
    for index, item in enumerate(mockups):
        if not isinstance(item, dict):
            errors.append(f"mockups[{index}] must be an object")
            continue
        direction_id = item.get("direction_id")
        direction_is_approved = False
        if not isinstance(direction_id, str) or not direction_id.strip():
            errors.append(
                f"mockups[{index}] direction_id must be a non-empty string"
            )
        elif approved is not None:
            if direction_id not in approved:
                errors.append(
                    f"mockups[{index}] direction_id is not approved: {direction_id}"
                )
            else:
                direction_is_approved = True
        if isinstance(direction_id, str) and direction_id.strip():
            if direction_id in seen_direction_ids:
                errors.append(f"duplicate current direction_id: {direction_id}")
            seen_direction_ids.add(direction_id)
        status = item.get("status")
        if not isinstance(status, str) or status not in ALLOWED_MOCKUP_STATUSES:
            errors.append(
                f"mockups[{index}] status must be pending, success, or failed"
            )
        artifact_kind = item.get("artifact_kind")
        is_code_preview = artifact_kind == "code-preview"
        invalid_artifact_kind = "artifact_kind" in item and not is_code_preview
        if invalid_artifact_kind:
            errors.append(
                f"mockups[{index}] artifact_kind must be code-preview when present"
            )
        attempt_count = item.get("attempt_count")
        valid_attempt = isinstance(attempt_count, int) and not isinstance(
            attempt_count, bool
        )
        pending_zero = status == "pending" and attempt_count == 0 and allow_initial_pending
        successful_code_preview_zero = (
            is_code_preview and status == "success" and attempt_count == 0
        )
        if not valid_attempt or (
            attempt_count <= 0
            and not pending_zero
            and not successful_code_preview_zero
        ):
            errors.append(
                f"mockups[{index}] attempt_count must be positive, or zero for initial pending authorization"
            )
        elif max_attempts is not None and attempt_count > max_attempts:
            errors.append(
                f"mockups[{index}] attempt_count exceeds max_attempts_per_direction"
            )
        if valid_attempt and attempt_count >= 0:
            attempt_total += attempt_count
            if isinstance(direction_id, str) and direction_id.strip():
                attempt_counts_by_direction[direction_id] = attempt_count
        viewport = item.get("viewport")
        normalized = normalized_viewport(viewport)
        if normalized is None:
            errors.append(f"mockups[{index}] viewport must use WIDTHxHEIGHT")
        else:
            seen_viewports.add(normalized)
            if target_viewports is not None and normalized not in target_viewports:
                errors.append(
                    f"mockups[{index}] viewport must be one of locked target_viewports"
                )
        prompt_ref = item.get("prompt_ref")
        prompt_path = resolved_scoped_path(run_dir, prompt_ref)
        if prompt_path is None or not prompt_path.is_file():
            errors.append(
                f"mockups[{index}] prompt_ref must be a safe run-relative existing file"
            )
        prompt_digest = item.get("prompt_digest")
        if not isinstance(prompt_digest, str) or not PROMPT_DIGEST_PATTERN.fullmatch(
            prompt_digest
        ):
            errors.append(
                f"mockups[{index}] prompt_digest must be sha256 plus 64 lowercase hex"
            )
        elif prompt_path is not None and prompt_path.is_file():
            actual = "sha256:" + hashlib.sha256(prompt_path.read_bytes()).hexdigest()
            if prompt_digest != actual:
                errors.append(
                    f"mockups[{index}] prompt_digest must match exact prompt bytes"
                )
        output_kind = item.get("output_kind")
        output_ref = item.get("output_ref")
        output_valid = True
        if output_kind is None and output_ref is None and status in {"pending", "failed"}:
            pass
        elif output_kind == "local":
            output_path = resolved_scoped_path(run_dir, output_ref)
            if output_path is None or not output_path.is_file():
                errors.append(
                    f"mockups[{index}] local output_ref must be a safe run-relative existing file"
                )
                output_valid = False
            else:
                dimensions = png_dimensions(output_path)
                if dimensions is None:
                    errors.append(f"mockups[{index}] local output must be a complete PNG")
                    output_valid = False
                elif normalized is not None and dimensions != tuple(
                    int(part) for part in normalized.split("x")
                ):
                    errors.append(
                        f"mockups[{index}] local output dimensions "
                        f"{dimensions[0]}x{dimensions[1]} must match {normalized}"
                    )
                    output_valid = False
        elif output_kind == "provider":
            if not _valid_provider_output_ref(output_ref):
                errors.append(
                    f"mockups[{index}] provider output_ref must use "
                    "provider:<lowercase-provider>:<safe-artifact-id>"
                )
                output_valid = False
        else:
            errors.append(
                f"mockups[{index}] output_kind must be local or provider when output_ref is present"
            )
            output_valid = False
        entry_code_preview_errors = []
        if is_code_preview:
            entry_code_preview_errors = code_preview_errors(
                run_dir, run, item, index
            )
            errors.extend(entry_code_preview_errors)
            if direction_is_approved:
                preview_path = item.get("preview_path")
                if isinstance(preview_path, str):
                    if preview_path in code_preview_paths:
                        errors.append(
                            "code preview preview_path must be unique across directions"
                        )
                    else:
                        code_preview_paths[preview_path] = direction_id
                preview_route = item.get("preview_route")
                if isinstance(preview_route, str):
                    if preview_route in code_preview_routes:
                        errors.append(
                            "code preview preview_route must be unique across directions"
                        )
                    else:
                        code_preview_routes[preview_route] = direction_id
                checks = item.get("viewport_checks")
                if isinstance(checks, dict):
                    for check_viewport, check in checks.items():
                        screenshot_ref = (
                            check.get("screenshot_ref")
                            if isinstance(check, dict)
                            else None
                        )
                        key = (check_viewport, screenshot_ref)
                        if isinstance(screenshot_ref, str):
                            if key in code_preview_screenshots:
                                errors.append(
                                    "code preview screenshot_ref must be unique for "
                                    f"viewport {check_viewport} across directions"
                                )
                            else:
                                code_preview_screenshots[key] = direction_id
                preview_files = item.get("preview_files")
                token_sources = item.get("token_sources")
                component_sources = item.get("component_sources")
                if (
                    isinstance(preview_path, str)
                    and isinstance(preview_files, list)
                    and isinstance(token_sources, list)
                    and isinstance(component_sources, list)
                ):
                    reusable = set(token_sources) | set(component_sources)
                    direction_owned = [
                        path for path in preview_files if path not in reusable
                    ]
                    if item.get("preview_mode") == "standalone":
                        isolated = [
                            path
                            for path in direction_owned
                            if direction_id in PurePosixPath(path).parts
                            or PurePosixPath(path).stem == direction_id
                        ]
                        if isolated:
                            direction_owned = isolated
                    topology = tuple(sorted(direction_owned))
                    if topology in code_preview_topologies:
                        errors.append(
                            "code preview direction-owned topology must be unique across directions"
                        )
                    else:
                        code_preview_topologies[topology] = direction_id
        if status == "success":
            fields_are_valid = True
            if output_kind not in {"local", "provider"}:
                errors.append(f"mockups[{index}] success requires output_kind")
                fields_are_valid = False
            if not isinstance(output_ref, str) or not output_ref.strip():
                errors.append(f"mockups[{index}] success requires output_ref")
                fields_are_valid = False
            if normalized is None:
                fields_are_valid = False
            if not isinstance(prompt_digest, str) or not PROMPT_DIGEST_PATTERN.fullmatch(
                prompt_digest
            ):
                fields_are_valid = False
            if prompt_path is None or not prompt_path.is_file() or not output_valid:
                fields_are_valid = False
            if entry_code_preview_errors:
                fields_are_valid = False
            if invalid_artifact_kind:
                fields_are_valid = False
            if direction_is_approved and fields_are_valid:
                successful.add(direction_id)
    if len(seen_viewports) > 1:
        errors.append("mockups must share exactly one viewport across directions")
    if approved is not None:
        missing_entries = approved - seen_direction_ids
        extra_entries = seen_direction_ids - approved
        if missing_entries:
            errors.append(
                f"missing current mockup entries for: {', '.join(sorted(missing_entries))}"
            )
        if extra_entries:
            errors.append(
                f"unapproved current mockup entries for: {', '.join(sorted(extra_entries))}"
            )
        if len(mockups) != len(approved):
            errors.append("mockup manifest must contain exactly one entry per approved direction")
    if attempts_used is not None and attempt_total != attempts_used:
        errors.append(
            "mockup attempt_count total must equal manifest generation_attempts_used"
        )
    if (
        attempts_used is not None
        and attempts_used > 0
        and authorized_direction is not None
        and attempt_counts_by_direction.get(authorized_direction, 0) <= 0
    ):
        errors.append(
            "mockup-manifest audit direction must have a positive attempt_count"
        )
    if require_success:
        missing = approved - successful if approved is not None else set()
        if missing:
            errors.append(f"missing successful mockups for: {', '.join(sorted(missing))}")
    return errors


def validate_mockup_manifest_for_generation(run_dir: Path) -> list[str]:
    return _mockup_manifest_errors(
        Path(run_dir), require_success=False, allow_initial_pending=True
    )


def validate_mockups(run_dir: Path) -> list[str]:
    return _mockup_manifest_errors(
        Path(run_dir), require_success=True, allow_initial_pending=False
    )


def validate_implementation(run_dir: Path) -> list[str]:
    errors = []
    run = read_json(run_dir, "run.json", errors)
    implementation = read_json(run_dir, "implementation.json", errors)
    if run is None or implementation is None:
        return errors
    if not isinstance(run, dict) or not isinstance(implementation, dict):
        errors.append("run.json and implementation.json must be objects")
        return errors
    errors.extend(find_secret_errors(run, "run"))
    errors.extend(find_secret_errors(implementation, "implementation"))
    approved = approved_direction_ids(run, errors)
    selected_direction_id = implementation.get("selected_direction_id")
    if (
        not isinstance(selected_direction_id, str)
        or not selected_direction_id.strip()
    ):
        errors.append(
            "implementation selected_direction_id must be a non-empty string"
        )
    elif approved is not None and selected_direction_id not in approved:
        errors.append("implementation selected direction is not approved")
    if selected_direction_id != run.get("selected_direction_id"):
        errors.append("implementation selected direction does not match run.json")
    mode = implementation.get("mode")
    if not isinstance(mode, str) or mode not in {"project", "standalone"}:
        errors.append("implementation mode must be project or standalone")

    target_viewports = normalized_string_list(
        run.get("target_viewports"), "run target_viewports", errors
    )
    if target_viewports is not None and any(
        normalized_viewport(viewport) != viewport for viewport in target_viewports
    ):
        errors.append("run target_viewports must contain normalized WIDTHxHEIGHT values")
        target_viewports = None
    required_content = normalized_string_list(
        run.get("required_content"), "run required_content", errors
    )
    required_interactions = normalized_string_list(
        run.get("required_interactions"), "run required_interactions", errors
    )
    production_paths = normalized_string_list(
        run.get("production_paths"), "run production_paths", errors
    )
    if production_paths is not None and any(
        not valid_artifact_ref(path) for path in production_paths
    ):
        errors.append("run production_paths must be safe project-relative paths")
        production_paths = None

    preview_path = implementation.get("preview_path")
    if not isinstance(preview_path, str) or not preview_path.strip():
        errors.append("implementation preview_path is required")
        preview_path = None
    elif not valid_artifact_ref(preview_path):
        errors.append("implementation preview_path must be a safe project-relative path")
        preview_path = None

    preview_files = normalized_string_list(
        implementation.get("preview_files"), "implementation preview_files", errors
    )
    if preview_files is not None:
        if not preview_files:
            errors.append("implementation preview_files must be non-empty")
        elif any(not valid_artifact_ref(path) for path in preview_files):
            errors.append("implementation preview_files must be safe project-relative paths")
            preview_files = None
    if preview_path is not None and preview_files is not None and preview_path not in preview_files:
        errors.append("implementation preview_path must be included in preview_files")

    preview_route = implementation.get("preview_route")
    if not valid_preview_route(preview_route):
        errors.append("implementation preview_route must be a normalized absolute URL path")

    project_root = None
    project_path = run.get("project_path")
    if mode == "project":
        if not isinstance(project_path, str) or not Path(project_path).is_absolute():
            errors.append("project mode requires project_path to be an existing absolute directory")
        else:
            try:
                candidate = Path(project_path).resolve(strict=True)
            except (OSError, RuntimeError):
                candidate = None
            if candidate is None or not candidate.is_dir():
                errors.append("project mode requires project_path to be an existing absolute directory")
            else:
                project_root = candidate
    elif mode == "standalone":
        if production_paths:
            errors.append("standalone mode requires production_paths to be empty")
        project_root = Path(run_dir)

    resolved_previews = []
    if project_root is not None and preview_files is not None:
        for path in preview_files:
            resolved = resolved_scoped_path(project_root, path)
            if resolved is None or not resolved.is_file():
                errors.append(f"implementation preview file must be an existing file: {path}")
            else:
                resolved_previews.append((path, resolved))
    resolved_preview_map = dict(resolved_previews)
    expected_source_digest = None
    if project_root is not None and preview_files is not None and len(
        resolved_previews
    ) == len(preview_files):
        expected_source_digest = preview_files_digest(project_root, preview_files)

    resolved_production = []
    if mode == "project" and project_root is not None and production_paths is not None:
        for path in production_paths:
            resolved = resolved_scoped_path(project_root, path)
            if resolved is None or not resolved.exists():
                errors.append(f"production_path must exist in project: {path}")
            else:
                resolved_production.append((path, resolved))
        for preview_name, preview in resolved_previews:
            for production_name, production in resolved_production:
                try:
                    preview.relative_to(production)
                except ValueError:
                    continue
                errors.append(
                    f"preview file {preview_name} overlaps production_path {production_name}"
                )

    if mode == "project" and project_root is not None and preview_files is not None:
        route_paths = {}
        for field in ("route_registry_path", "route_consumer_path"):
            value = implementation.get(field)
            if not isinstance(value, str) or not valid_artifact_ref(value):
                errors.append(f"project implementation {field} must be a safe project-relative path")
            elif value not in preview_files:
                errors.append(f"project implementation {field} must be included in preview_files")
            elif value not in resolved_preview_map:
                errors.append(f"project implementation {field} must be an existing contained file")
            else:
                route_paths[field] = value

        component_path = None
        registry_path = route_paths.get("route_registry_path")
        consumer_path = route_paths.get("route_consumer_path")
        if registry_path is not None and isinstance(preview_route, str):
            try:
                registry = json.loads(
                    resolved_preview_map[registry_path].read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError, UnicodeError):
                registry = None
            entry = registry.get(preview_route) if isinstance(registry, dict) else None
            if not isinstance(entry, dict) or set(entry) != {"component_path", "shell_id"}:
                errors.append(
                    "project route registry must map preview_route to component_path and shell_id"
                )
            else:
                component_path = entry.get("component_path")
                shell_id = entry.get("shell_id")
                if (
                    not isinstance(component_path, str)
                    or not valid_artifact_ref(component_path)
                    or component_path not in preview_files
                    or component_path not in resolved_preview_map
                ):
                    errors.append(
                        "project route registry component_path must be an exact included existing preview file"
                    )
                    component_path = None
                if not isinstance(shell_id, str) or not shell_id.strip():
                    errors.append("project route registry shell_id must be nonblank")

        if consumer_path is not None and registry_path is not None and component_path is not None:
            try:
                consumer_source = resolved_preview_map[consumer_path].read_text(
                    encoding="utf-8"
                )
            except (OSError, UnicodeError):
                consumer_source = ""
            _, consumer_references = js_module_references(consumer_source)
            imported = set()
            for reference in consumer_references:
                if not is_runtime_import(reference):
                    continue
                resolved, error = resolve_local_dependency(
                    project_root,
                    resolved_preview_map[consumer_path],
                    reference.specifier,
                    project_root,
                )
                if error:
                    errors.append(f"route consumer import error: {error}")
                elif resolved is not None:
                    imported.add(resolved)
            if registry_path not in imported or component_path not in imported:
                errors.append(
                    "project route consumer must have real static imports with runtime bindings of registry and component"
                )

        closure_roots = [value for value in (preview_path, consumer_path, component_path) if value]
        errors.extend(
            dependency_closure_errors(project_root, preview_files, closure_roots)
        )

    if mode == "standalone" and preview_files is not None and len(
        resolved_previews
    ) == len(preview_files):
        errors.extend(
            _standalone_topology_errors(
                Path(run_dir), preview_files, resolved_preview_map
            )
        )
        packages = [
            path for path in preview_files if PurePosixPath(path).name == "package.json"
        ]
        if len(packages) == 1:
            package_parent = PurePosixPath(packages[0]).parent
            prefix = "" if str(package_parent) == "." else f"{package_parent}/"
            standalone_roots = [
                value
                for value in (
                    preview_path,
                    f"{prefix}index.html",
                    f"{prefix}vite.config.ts",
                    f"{prefix}src/main.tsx",
                    f"{prefix}src/App.tsx",
                )
                if value
            ]
            errors.extend(
                dependency_closure_errors(
                    Path(run_dir),
                    preview_files,
                    standalone_roots,
                    Path(run_dir) / package_parent,
                )
            )

    verification = implementation.get("verification", {})
    if not isinstance(verification, dict):
        errors.append("implementation verification must be an object")
        verification = {}
    rendered_viewports = verification.get("rendered_viewports")
    normalized_rendered = normalized_string_list(
        rendered_viewports, "implementation rendered_viewports", errors
    )
    if normalized_rendered is not None:
        if not normalized_rendered or any(
            normalized_viewport(viewport) != viewport for viewport in normalized_rendered
        ):
            errors.append("implementation rendered_viewports must use normalized WIDTHxHEIGHT")
        elif target_viewports is not None and normalized_rendered != target_viewports:
            errors.append("implementation rendered_viewports must exactly match run target_viewports")
    checks = verification.get("checks", {})
    if not isinstance(checks, dict):
        errors.append("implementation checks must be an object")
        checks = {}
    for name in ("content", "overflow", "accessibility"):
        if checks.get(name) != "pass":
            errors.append(f"implementation check must pass: {name}")

    viewport_checks = verification.get("viewport_checks")
    if not isinstance(viewport_checks, dict):
        errors.append("implementation viewport_checks must be an object")
        viewport_checks = {}
    if target_viewports is not None and set(viewport_checks) != set(target_viewports):
        errors.append("implementation viewport_checks keys must exactly match run target_viewports")
    for viewport in target_viewports or []:
        check = viewport_checks.get(viewport)
        label = f"implementation viewport_checks[{viewport}]"
        if not isinstance(check, dict):
            errors.append(f"{label} must be an object")
            continue
        for name in ("content", "overflow", "accessibility", "interaction"):
            if check.get(name) != "pass":
                errors.append(f"{label} status must pass: {name}")
        for field, required in (
            ("required_content", required_content),
            ("required_interactions", required_interactions),
        ):
            item_checks = check.get(field)
            if not isinstance(item_checks, dict):
                errors.append(f"{label} {field} must be an object")
            elif required is not None:
                if set(item_checks) != set(required):
                    errors.append(f"{label} {field} keys must exactly match run requirements")
                for item in required:
                    if item_checks.get(item) != "pass":
                        errors.append(f"{label} {field} must pass: {item}")
        screenshot_ref = check.get("screenshot_ref")
        source_digest = check.get("source_digest")
        if (
            not isinstance(source_digest, str)
            or not PROMPT_DIGEST_PATTERN.fullmatch(source_digest)
            or expected_source_digest is None
            or source_digest != expected_source_digest
        ):
            errors.append(f"{label} source_digest must match current preview_files")
        if not valid_artifact_ref(screenshot_ref):
            errors.append(f"{label} screenshot_ref must be a safe run-relative path")
            continue
        screenshot = resolved_scoped_path(Path(run_dir), screenshot_ref)
        if screenshot is None or not screenshot.is_file():
            errors.append(f"{label} screenshot must be an existing file")
            continue
        dimensions = png_dimensions(screenshot)
        if dimensions is None:
            errors.append(f"{label} screenshot must be a PNG with IHDR")
            continue
        expected_dimensions = tuple(int(part) for part in viewport.split("x"))
        if dimensions != expected_dimensions:
            errors.append(
                f"{label} screenshot dimensions {dimensions[0]}x{dimensions[1]} "
                f"must match {viewport}"
            )
    return errors


def validate_phase(run_dir: Path, phase: str) -> list[str]:
    run_dir = Path(run_dir)
    if phase == "research":
        return validate_research(run_dir)
    if phase == "directions":
        return validate_directions(run_dir)
    if phase == "mockups":
        return validate_mockups(run_dir)
    if phase == "implementation":
        return validate_implementation(run_dir)
    if phase == "all":
        return (
            validate_research(run_dir)
            + validate_directions(run_dir)
            + validate_mockups(run_dir)
            + validate_implementation(run_dir)
        )
    raise ValueError(f"unknown phase: {phase}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("research", "directions", "mockups", "implementation", "all"),
    )
    args = parser.parse_args()
    try:
        errors = validate_phase(Path(args.run), args.phase)
    except (ValueError, json.JSONDecodeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"{args.phase} artifacts are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
