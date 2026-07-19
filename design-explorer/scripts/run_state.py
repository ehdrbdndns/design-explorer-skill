#!/usr/bin/env python3
import argparse
import importlib.util
import json
import re
import sys
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
SCHEMA_VERSION = 2
DEFAULT_GENERATION_BUDGET = 5
DEFAULT_MAX_ATTEMPTS_PER_DIRECTION = 2
RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
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
VALIDATION_PHASES = {
    "research_complete": "research",
    "directions_pending_approval": "directions",
    "directions_approved": "directions",
    "mockups_generated": "mockups",
    "implementation_selected": "mockups",
    "prototype_ready": "implementation",
    "integrated": "implementation",
}
NONBLANK_FILES = {
    "brief_ready": ("brief.md",),
    "research_complete": ("design-evidence.md", "reference-board.md"),
    "directions_pending_approval": ("mood-directions.md",),
    "directions_approved": ("mood-directions.md",),
}
_VALIDATOR = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def valid_rfc3339(value) -> bool:
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


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
        "schema_version": SCHEMA_VERSION,
        "run_id": identifier,
        "slug": slug,
        "state": "initialized",
        "created_at": timestamp,
        "updated_at": timestamp,
        "project_path": project_path,
        "approved_direction_ids": [],
        "selected_direction_id": None,
        "revision_count": 0,
        "generation_budget": DEFAULT_GENERATION_BUDGET,
        "max_attempts_per_direction": DEFAULT_MAX_ATTEMPTS_PER_DIRECTION,
    }
    write_json_atomic(run_dir / "run.json", manifest)
    return run_dir


def load_run(run_dir: Path) -> dict:
    try:
        manifest = json.loads(
            (Path(run_dir) / "run.json").read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"invalid run.json: {error}") from None
    validate_run_manifest(manifest)
    return manifest


def _positive_integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_run_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("invalid run.json: manifest must be an object")
    version = manifest.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported run schema {version!r}; migrate or initialize a schema v{SCHEMA_VERSION} run"
        )
    required = (
        "run_id",
        "slug",
        "state",
        "created_at",
        "updated_at",
        "project_path",
        "approved_direction_ids",
        "selected_direction_id",
        "revision_count",
        "generation_budget",
        "max_attempts_per_direction",
    )
    for key in required:
        if key not in manifest:
            raise ValueError(f"invalid run.json: missing required key: {key}")
    for key in ("run_id", "slug", "created_at", "updated_at"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ValueError(f"invalid run.json: {key} must be a non-empty string")
    if manifest["state"] not in STATES:
        raise ValueError("invalid run.json: state is unsupported")
    if manifest["project_path"] is not None and not isinstance(
        manifest["project_path"], str
    ):
        raise ValueError("invalid run.json: project_path must be a string or null")
    approved = manifest["approved_direction_ids"]
    if (
        not isinstance(approved, list)
        or any(not isinstance(value, str) or not value.strip() for value in approved)
        or len(set(approved)) != len(approved)
    ):
        raise ValueError(
            "invalid run.json: approved_direction_ids must be unique non-empty strings"
        )
    selected = manifest["selected_direction_id"]
    if selected is not None and (
        not isinstance(selected, str) or not selected.strip()
    ):
        raise ValueError(
            "invalid run.json: selected_direction_id must be a non-empty string or null"
        )
    revision_count = manifest["revision_count"]
    if (
        not isinstance(revision_count, int)
        or isinstance(revision_count, bool)
        or revision_count < 0
    ):
        raise ValueError("invalid run.json: revision_count must be a non-negative integer")
    for key in ("generation_budget", "max_attempts_per_direction"):
        if not _positive_integer(manifest[key]):
            raise ValueError(f"invalid run.json: {key} must be a positive integer")
    expanded = (
        manifest["generation_budget"] > DEFAULT_GENERATION_BUDGET
        or manifest["max_attempts_per_direction"]
        > DEFAULT_MAX_ATTEMPTS_PER_DIRECTION
    )
    budget_approval = manifest.get("budget_expansion_approved_at")
    if expanded and not valid_rfc3339(budget_approval):
        raise ValueError(
            "invalid run.json: expanded budget requires valid budget_expansion_approved_at"
        )
    if not expanded and "budget_expansion_approved_at" in manifest:
        raise ValueError(
            "invalid run.json: budget_expansion_approved_at requires an expanded budget"
        )
    if "integration_approved_at" in manifest and (
        not isinstance(manifest["integration_approved_at"], str)
        or not manifest["integration_approved_at"].strip()
    ):
        raise ValueError(
            "invalid run.json: integration_approved_at must be a non-empty string"
        )


def _require_files(run_dir: Path, target: str) -> None:
    missing = [name for name in REQUIRED_FILES.get(target, ()) if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"{target} requires {', '.join(missing)}")


def _validator_module():
    global _VALIDATOR
    if _VALIDATOR is not None:
        return _VALIDATOR
    path = Path(__file__).with_name("validate_run.py")
    spec = importlib.util.spec_from_file_location("design_explorer_validate_run", path)
    if spec is None or spec.loader is None:
        raise ValueError("unable to load artifact validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _VALIDATOR = module
    return module


def _validate_target(run_dir: Path, target: str) -> None:
    _require_files(run_dir, target)
    for name in NONBLANK_FILES.get(target, ()):
        try:
            value = (run_dir / name).read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError(f"{target} validation failed: {error}") from None
        if not value.strip():
            phase = "brief" if target == "brief_ready" else target
            raise ValueError(f"{phase} validation failed: {name} must be nonblank")
        secret_errors = _validator_module().find_secret_errors(value, name)
        if secret_errors:
            phase = "brief" if target == "brief_ready" else target
            raise ValueError(f"{phase} validation failed: {secret_errors[0]}")
    phase = VALIDATION_PHASES.get(target)
    if phase is not None:
        errors = _validator_module().validate_phase(run_dir, phase)
        if errors:
            summary = "; ".join(errors[:5])
            if len(errors) > 5:
                summary += f"; plus {len(errors) - 5} more"
            raise ValueError(f"{phase} validation failed: {summary}")


def transition_run(
    run_dir: Path,
    target: str,
    approved_direction_ids: list[str] | None = None,
    selected_direction_id: str | None = None,
    now: str | None = None,
    integration_approved: bool = False,
    generation_budget: int | None = None,
    max_attempts_per_direction: int | None = None,
    budget_expansion_approved: bool = False,
) -> dict:
    run_dir = Path(run_dir)
    manifest = load_run(run_dir)
    current = manifest["state"]
    if NEXT_STATE.get(current) != target:
        raise ValueError(f"illegal transition: {current} -> {target}")
    _validate_target(run_dir, target)
    timestamp = now or utc_now()

    if target == "integrated":
        if not integration_approved:
            raise ValueError("integrated requires explicit integration approval")
        manifest["integration_approved_at"] = timestamp

    if target == "directions_approved":
        if not approved_direction_ids:
            raise ValueError("directions_approved requires explicit approved_direction_ids")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in approved_direction_ids
        ) or len(set(approved_direction_ids)) != len(approved_direction_ids):
            raise ValueError(
                "approved_direction_ids must be unique non-empty strings"
            )
        directions = json.loads((run_dir / "directions.json").read_text(encoding="utf-8"))
        known = {item["id"] for item in directions}
        unknown = set(approved_direction_ids) - known
        if unknown:
            raise ValueError(f"unknown direction: {', '.join(sorted(unknown))}")
        effective_budget = (
            manifest["generation_budget"]
            if generation_budget is None
            else generation_budget
        )
        effective_attempts = (
            manifest["max_attempts_per_direction"]
            if max_attempts_per_direction is None
            else max_attempts_per_direction
        )
        if not _positive_integer(effective_budget):
            raise ValueError("generation_budget must be a positive integer")
        if not _positive_integer(effective_attempts):
            raise ValueError("max_attempts_per_direction must be a positive integer")
        expanded = (
            effective_budget > DEFAULT_GENERATION_BUDGET
            or effective_attempts > DEFAULT_MAX_ATTEMPTS_PER_DIRECTION
        )
        if expanded and not budget_expansion_approved:
            raise ValueError("expanded budget requires explicit budget expansion approval")
        if expanded and not valid_rfc3339(timestamp):
            raise ValueError(
                "expanded budget requires a valid RFC3339 approval timestamp"
            )
        if len(approved_direction_ids) > effective_budget:
            raise ValueError(
                f"approved directions exceed generation budget {effective_budget}"
            )
        manifest["approved_direction_ids"] = list(approved_direction_ids)
        manifest["generation_budget"] = effective_budget
        manifest["max_attempts_per_direction"] = effective_attempts
        if expanded:
            manifest["budget_expansion_approved_at"] = timestamp

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
    _validate_target(run_dir, "mockups_generated")

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
    manifest["generation_budget"] = DEFAULT_GENERATION_BUDGET
    manifest["max_attempts_per_direction"] = DEFAULT_MAX_ATTEMPTS_PER_DIRECTION
    manifest.pop("budget_expansion_approved_at", None)
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
    transition_parser.add_argument("--generation-budget", type=int)
    transition_parser.add_argument("--max-attempts-per-direction", type=int)
    transition_parser.add_argument("--approve-budget-expansion", action="store_true")
    revise_parser = subparsers.add_parser("revise")
    revise_parser.add_argument("--run", required=True)
    revise_parser.add_argument("--reason", required=True)
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run", required=True)
    args = parser.parse_args()

    try:
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
                generation_budget=args.generation_budget,
                max_attempts_per_direction=args.max_attempts_per_direction,
                budget_expansion_approved=args.approve_budget_expansion,
            )
            print(json.dumps(manifest, indent=2))
        elif args.command == "revise":
            manifest = revise_run(Path(args.run), args.reason)
            print(json.dumps(manifest, indent=2))
        else:
            print(json.dumps(load_run(Path(args.run)), indent=2))
    except (ValueError, json.JSONDecodeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
