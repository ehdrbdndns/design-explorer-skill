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


def init_run(
    root: Path,
    slug: str,
    project_path: str | None = None,
    now: str | None = None,
    run_id: str | None = None,
) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("slug must use lowercase letters, digits, and hyphens")
    if run_id is not None and (
        Path(run_id).is_absolute()
        or run_id in {".", ".."}
        or "/" in run_id
        or "\\" in run_id
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id)
    ):
        raise ValueError("run_id must be a safe path component")
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
        "revision_count": 0,
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
    approved_direction_ids: list[str] | None = None,
    selected_direction_id: str | None = None,
    now: str | None = None,
    integration_approved: bool = False,
) -> dict:
    run_dir = Path(run_dir)
    manifest = load_run(run_dir)
    current = manifest["state"]
    if NEXT_STATE.get(current) != target:
        raise ValueError(f"illegal transition: {current} -> {target}")
    _require_files(run_dir, target)
    timestamp = now or utc_now()

    if target == "integrated":
        if not integration_approved:
            raise ValueError("integrated requires explicit integration approval")
        manifest["integration_approved_at"] = timestamp

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
    manifest["updated_at"] = timestamp
    write_json_atomic(run_dir / "run.json", manifest)
    return manifest


def revise_run(
    run_dir: Path,
    reason: str,
    now: str | None = None,
) -> dict:
    run_dir = Path(run_dir)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("revision requires a non-empty reason")
    manifest = load_run(run_dir)
    current = manifest["state"]
    if current != "mockups_generated":
        raise ValueError(f"illegal revision from {current}")
    _require_files(run_dir, "mockups_generated")

    revision_count = manifest.get("revision_count", 0)
    if (
        not isinstance(revision_count, int)
        or isinstance(revision_count, bool)
        or revision_count < 0
    ):
        raise ValueError("run.json revision_count must be a non-negative integer")
    revision_count += 1
    archive_path = run_dir / f"mockup-manifest.revision-{revision_count}.json"
    if archive_path.exists():
        raise ValueError(f"revision archive already exists: {archive_path.name}")
    manifest_path = run_dir / "mockup-manifest.json"
    manifest_path.replace(archive_path)

    timestamp = now or utc_now()
    manifest["state"] = "directions_pending_approval"
    manifest["revision_count"] = revision_count
    manifest["last_revision_reason"] = reason.strip()
    manifest["last_revision_at"] = timestamp
    manifest["approved_direction_ids"] = []
    manifest["selected_direction_id"] = None
    manifest["updated_at"] = timestamp
    try:
        write_json_atomic(run_dir / "run.json", manifest)
    except Exception:
        archive_path.replace(manifest_path)
        raise
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
    transition_parser.add_argument("--approve-integration", action="store_true")
    revise_parser = subparsers.add_parser("revise")
    revise_parser.add_argument("--run", required=True)
    revise_parser.add_argument("--reason", required=True)
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
            integration_approved=args.approve_integration,
        )
        print(json.dumps(manifest, indent=2))
    elif args.command == "revise":
        manifest = revise_run(Path(args.run), args.reason)
        print(json.dumps(manifest, indent=2))
    else:
        print(json.dumps(load_run(Path(args.run)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
