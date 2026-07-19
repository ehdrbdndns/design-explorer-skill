#!/usr/bin/env python3
import argparse
import hashlib
import ipaddress
import json
import re
import shlex
import struct
import sys
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
            while end < len(source):
                if source[end] == "\\" and end + 1 < len(source):
                    end += 2
                    continue
                if source[end] == "`":
                    end += 1
                    break
                end += 1
            _mask_span(output, index, end)
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
    r"(?m)^[ \t]*export\b[^;]*?\bfrom\s*(?P<quote>[\"'])"
)
DYNAMIC_IMPORT_PATTERN = re.compile(
    r"\bimport\s*\(\s*(?P<quote>[\"'])\s*(?P=quote)\s*\)"
)
REQUIRE_PATTERN = re.compile(
    r"\brequire\s*\(\s*(?P<quote>[\"'])\s*(?P=quote)\s*\)"
)


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
            references.append(JsModuleReference(kind, literal.value, clause))
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


def dependency_closure_errors(
    root: Path,
    preview_files: list[str],
    roots: list[str],
    absolute_root: Path | None = None,
) -> list[str]:
    errors = []
    listed = set(preview_files)
    pending = list(dict.fromkeys(roots))
    visited = set()
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
        for specifier in local_dependencies(path):
            resolved, error = resolve_local_dependency(root, path, specifier, absolute_root)
            if error:
                errors.append(f"{relative}: {error}")
            elif resolved is not None:
                if resolved not in listed:
                    errors.append(f"unlisted local dependency from {relative}: {resolved}")
                else:
                    pending.append(resolved)
    return errors


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


def validate_mockups(run_dir: Path) -> list[str]:
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
    approved = approved_direction_ids(run, errors)
    budget = positive_integer_field(run, "generation_budget", errors)
    max_attempts = positive_integer_field(run, "max_attempts_per_direction", errors)
    validate_budget_approval(run, budget, max_attempts, errors)
    mockups = manifest["mockups"]
    if budget is not None and len(mockups) > budget:
        errors.append(
            f"mockup-manifest mockups length {len(mockups)} exceeds generation_budget {budget}"
        )
    successful = set()
    seen_direction_ids = set()
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
        attempt_count = item.get("attempt_count")
        if (
            not isinstance(attempt_count, int)
            or isinstance(attempt_count, bool)
            or attempt_count <= 0
        ):
            errors.append(f"mockups[{index}] attempt_count must be a positive integer")
        elif max_attempts is not None and attempt_count > max_attempts:
            errors.append(
                f"mockups[{index}] attempt_count exceeds max_attempts_per_direction"
            )
        output_ref = item.get("output_ref")
        output_ref_is_valid = True
        if output_ref is not None and not valid_artifact_ref(
            output_ref, allow_provider_hint=True
        ):
            errors.append(f"mockups[{index}] output_ref must be a safe artifact reference")
            output_ref_is_valid = False
        viewport = item.get("viewport")
        if not isinstance(viewport, str) or not VIEWPORT_PATTERN.fullmatch(viewport):
            errors.append(f"mockups[{index}] viewport must use WIDTHxHEIGHT")
        prompt_digest = item.get("prompt_digest")
        if not isinstance(prompt_digest, str) or not PROMPT_DIGEST_PATTERN.fullmatch(
            prompt_digest
        ):
            errors.append(
                f"mockups[{index}] prompt_digest must be sha256 plus 64 lowercase hex"
            )
        if status == "success":
            fields_are_valid = True
            if not isinstance(output_ref, str) or not output_ref.strip():
                errors.append(f"mockups[{index}] missing output_ref")
                fields_are_valid = False
            if not isinstance(viewport, str) or not VIEWPORT_PATTERN.fullmatch(viewport):
                fields_are_valid = False
            if not isinstance(prompt_digest, str) or not PROMPT_DIGEST_PATTERN.fullmatch(
                prompt_digest
            ):
                fields_are_valid = False
            if not output_ref_is_valid:
                fields_are_valid = False
            if direction_is_approved and fields_are_valid:
                successful.add(direction_id)
    missing = approved - successful if approved is not None else set()
    if missing:
        errors.append(f"missing successful mockups for: {', '.join(sorted(missing))}")
    return errors


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
