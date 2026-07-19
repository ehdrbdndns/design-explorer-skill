#!/usr/bin/env python3
import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


STATES = (
    "initialized",
    "brief_ready",
    "research_complete",
    "directions_pending_approval",
    "directions_approved",
    "mockups_generated",
    "implementation_selected",
    "prototype_ready",
    "integrated",
)
NEXT_STATE = dict(zip(STATES, STATES[1:]))
REQUIRED_FILES = {
    "brief_ready": ("brief.md",),
    "research_complete": (
        "references.json",
        "evidence.json",
        "design-evidence.md",
        "reference-board.md",
    ),
    "directions_pending_approval": ("directions.json", "mood-directions.md"),
    "mockups_generated": ("mockup-manifest.json",),
    "prototype_ready": ("implementation.json",),
    "integrated": ("implementation.json",),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def init_run(root: Path, slug: str, project_path=None, now=None, run_id=None) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("slug must use lowercase letters, digits, and hyphens")
    timestamp = now or utc_now()
    identifier = run_id or f"{timestamp[:10].replace('-', '')}-{slug}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(root).expanduser() / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "run_id": identifier,
        "slug": slug,
        "state": "initialized",
        "created_at": timestamp,
        "updated_at": timestamp,
        "project_path": project_path,
        "approved_direction_ids": [],
        "selected_direction_id": None,
    }
    write_json_atomic(run_dir / "run.json", manifest)
    return run_dir


def load_run(run_dir: Path) -> dict:
    return json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))


def _require_files(run_dir: Path, target: str) -> None:
    missing = [name for name in REQUIRED_FILES.get(target, ()) if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"{target} requires {', '.join(missing)}")


def transition_run(
    run_dir: Path,
    target: str,
    approved_direction_ids=None,
    selected_direction_id=None,
    now=None,
) -> dict:
    run_dir = Path(run_dir)
    manifest = load_run(run_dir)
    current = manifest["state"]
    if NEXT_STATE.get(current) != target:
        raise ValueError(f"illegal transition: {current} -> {target}")
    _require_files(run_dir, target)

    if target == "directions_approved":
        if not approved_direction_ids:
            raise ValueError("directions_approved requires explicit approved_direction_ids")
        directions = json.loads((run_dir / "directions.json").read_text(encoding="utf-8"))
        known = {item["id"] for item in directions}
        unknown = set(approved_direction_ids) - known
        if unknown:
            raise ValueError(f"unknown direction: {', '.join(sorted(unknown))}")
        manifest["approved_direction_ids"] = list(dict.fromkeys(approved_direction_ids))

    if target == "mockups_generated":
        mockups = json.loads((run_dir / "mockup-manifest.json").read_text(encoding="utf-8"))["mockups"]
        successful = {item["direction_id"] for item in mockups if item["status"] == "success"}
        missing = set(manifest["approved_direction_ids"]) - successful
        if missing:
            raise ValueError(f"missing successful mockup: {', '.join(sorted(missing))}")

    if target == "implementation_selected":
        if selected_direction_id not in manifest["approved_direction_ids"]:
            raise ValueError("selected_direction_id must be an approved direction")
        manifest["selected_direction_id"] = selected_direction_id

    manifest["state"] = target
    manifest["updated_at"] = now or utc_now()
    write_json_atomic(run_dir / "run.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--root", default="~/.codex/design-explorer/runs")
    init_parser.add_argument("--slug", required=True)
    init_parser.add_argument("--project-path")
    transition_parser = subparsers.add_parser("transition")
    transition_parser.add_argument("--run", required=True)
    transition_parser.add_argument("--to", required=True, choices=STATES[1:])
    transition_parser.add_argument("--approved-direction", action="append")
    transition_parser.add_argument("--selected-direction")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run", required=True)
    args = parser.parse_args()

    if args.command == "init":
        run_dir = init_run(Path(args.root), args.slug, args.project_path)
        print(run_dir)
    elif args.command == "transition":
        manifest = transition_run(
            Path(args.run),
            args.to,
            approved_direction_ids=args.approved_direction,
            selected_direction_id=args.selected_direction,
        )
        print(json.dumps(manifest, indent=2))
    else:
        print(json.dumps(load_run(Path(args.run)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
