#!/usr/bin/env python3
import argparse
import ipaddress
import json
import re
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse


AXES = ("layout", "typography", "palette", "density", "imagery", "interaction")


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
    return (
        parsed.scheme in {"http", "https"}
        and hostname is not None
        and valid_hostname(hostname)
    )


def axis_value(item, axis):
    axes = item.get("axes")
    value = axes.get(axis) if isinstance(axes, dict) else None
    return value.strip().casefold() if isinstance(value, str) else None


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
        validate_sources(references, "references", errors)
        for index, item in enumerate(references if isinstance(references, list) else []):
            if not isinstance(item, dict):
                continue
            for field in ("captured_at", "relevance"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"references[{index}] missing {field}")
            observations = item.get("observations", {})
            if not isinstance(observations, dict):
                errors.append(f"references[{index}] observations must be an object")
                observations = {}
            missing = [axis for axis in AXES if not observations.get(axis)]
            if missing:
                errors.append(f"references[{index}] missing observations: {', '.join(missing)}")
    if evidence is not None:
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
    if evidence is not None and not isinstance(evidence, list):
        errors.append("evidence.json must be a list")
        evidence = []
    evidence_ids = set()
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
    if not isinstance(directions, list) or len(directions) < 5:
        errors.append("directions must contain at least five items")
        return errors
    ids = set()
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
    approved = approved_direction_ids(run, errors)
    successful = set()
    for index, item in enumerate(manifest["mockups"]):
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
        if item.get("status") == "success":
            fields_are_valid = True
            for field in ("viewport", "prompt_digest", "output_ref"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"mockups[{index}] missing {field}")
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
    errors = validate_phase(Path(args.run), args.phase)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"{args.phase} artifacts are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
