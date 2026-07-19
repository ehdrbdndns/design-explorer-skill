#!/usr/bin/env python3
import argparse
import json
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


def valid_url(value) -> bool:
    parsed = urlparse(value if isinstance(value, str) else "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_sources(items, label: str, errors: list[str]) -> None:
    if not isinstance(items, list) or not items:
        errors.append(f"{label} must be a non-empty list")
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        for field in ("id", "title", "source_url", "source_type"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{label}[{index}] missing {field}")
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
            missing = [axis for axis in AXES if not observations.get(axis)]
            if missing:
                errors.append(f"references[{index}] missing observations: {', '.join(missing)}")
    if evidence is not None:
        validate_sources(evidence, "evidence", errors)
        for index, item in enumerate(evidence if isinstance(evidence, list) else []):
            if not isinstance(item, dict):
                continue
            if item.get("source_type") not in {"official", "research", "observed"}:
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
    evidence_ids = {item.get("id") for item in evidence or [] if isinstance(item, dict)}
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
        if identifier in ids:
            errors.append(f"duplicate direction id: {identifier}")
        ids.add(identifier)
        axes = item.get("axes", {})
        missing_axes = [axis for axis in AXES if not axes.get(axis)]
        if missing_axes:
            errors.append(f"directions[{index}] missing axes: {', '.join(missing_axes)}")
        links = item.get("evidence_ids", [])
        if not links or set(links) - evidence_ids:
            errors.append(f"directions[{index}] has missing or unknown evidence_ids")
    valid_directions = [item for item in directions if isinstance(item, dict)]
    for left, right in combinations(valid_directions, 2):
        difference = sum(left.get("axes", {}).get(axis) != right.get("axes", {}).get(axis) for axis in AXES)
        if difference < 3:
            errors.append(f"{left.get('id')} and {right.get('id')} differ on fewer than three axes")
    return errors


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
    successful = set()
    for index, item in enumerate(manifest["mockups"]):
        if not isinstance(item, dict):
            errors.append(f"mockups[{index}] must be an object")
            continue
        if item.get("status") == "success":
            successful.add(item.get("direction_id"))
            for field in ("viewport", "prompt_digest", "output_ref"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"mockups[{index}] missing {field}")
    missing = set(run.get("approved_direction_ids", [])) - successful
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
    if implementation.get("selected_direction_id") != run.get("selected_direction_id"):
        errors.append("implementation selected direction does not match run.json")
    if implementation.get("mode") not in {"project", "standalone"}:
        errors.append("implementation mode must be project or standalone")
    if not isinstance(implementation.get("preview_path"), str) or not implementation["preview_path"].strip():
        errors.append("implementation preview_path is required")
    verification = implementation.get("verification", {})
    if not verification.get("rendered_viewports"):
        errors.append("implementation rendered_viewports is required")
    checks = verification.get("checks", {})
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
