#!/usr/bin/env python3
import argparse
import ipaddress
import json
import re
import sys
from itertools import combinations
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


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
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


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
        character.isspace() for character in value
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
    if normalized_hostname == "localhost" or normalized_hostname.endswith(
        (".localhost", ".local")
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
        elif isinstance(current, str) and any(
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
        if status not in ALLOWED_MOCKUP_STATUSES:
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
        if status == "success":
            fields_are_valid = True
            for field in ("viewport", "prompt_digest", "output_ref"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"mockups[{index}] missing {field}")
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
    if not isinstance(implementation.get("preview_path"), str) or not implementation["preview_path"].strip():
        errors.append("implementation preview_path is required")
    elif not valid_artifact_ref(implementation["preview_path"]):
        errors.append("implementation preview_path must be a safe relative path")
    verification = implementation.get("verification", {})
    if not isinstance(verification, dict):
        errors.append("implementation verification must be an object")
        verification = {}
    rendered_viewports = verification.get("rendered_viewports")
    if (
        not isinstance(rendered_viewports, list)
        or not rendered_viewports
        or any(
            not isinstance(viewport, str) or not viewport.strip()
            for viewport in rendered_viewports
        )
    ):
        errors.append(
            "implementation rendered_viewports must be a non-empty list of non-empty strings"
        )
    checks = verification.get("checks", {})
    if not isinstance(checks, dict):
        errors.append("implementation checks must be an object")
        checks = {}
    for name in ("content", "overflow", "accessibility"):
        if checks.get(name) != "pass":
            errors.append(f"implementation check must pass: {name}")
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
