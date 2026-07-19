#!/usr/bin/env python3
import argparse
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
import re
import stat
import sys
import uuid
from dataclasses import dataclass
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
GENERATION_TEMP_PATTERN = re.compile(
    r"\.mockup-manifest\.json\.generation-([0-9a-f]{32})\.tmp"
)
REVISION_MARKER_NAME = ".revision-transaction.json"
REVISION_TEMP_PATTERN = re.compile(
    r"\.(?:revision-transaction|run\.json\.(?:revision|transition))-([0-9a-f]{32})\.tmp"
)
REVISION_ARCHIVE_PATTERN = re.compile(r"mockup-manifest\.revision-([1-9]\d*)\.json")
RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)
LEGACY_GENERATION_ACCOUNTING_KEYS = frozenset(
    {
        "generation_attempts_used",
        "last_generation_authorized_at",
        "last_generation_direction_id",
        "last_generation_authorized_direction_id",
    }
)
VIEWPORT_PATTERN = re.compile(r"[1-9]\d{0,4}x[1-9]\d{0,4}")
MAX_VIEWPORT_DIMENSION = 10_000
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
STATE_VALIDATION_PHASES = {
    "initialized": (),
    "brief_ready": (),
    "research_complete": ("research",),
    "directions_pending_approval": ("research", "directions"),
    "directions_approved": ("research", "directions"),
    "mockups_generated": ("research", "directions", "mockups"),
    "implementation_selected": ("research", "directions", "mockups"),
    "prototype_ready": ("research", "directions", "mockups", "implementation"),
    "integrated": ("research", "directions", "mockups", "implementation"),
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


def _normalize_project_path(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("project_path must be a nonblank string or null")
    return str(Path(value).expanduser().resolve(strict=False))


def _brief_constraints(manifest: dict) -> dict:
    return {
        "project_path": manifest["project_path"],
        "target_viewports": manifest["target_viewports"],
        "required_content": manifest["required_content"],
        "required_interactions": manifest["required_interactions"],
        "production_paths": manifest["production_paths"],
    }


def _constraints_digest(constraints: dict) -> str:
    canonical = json.dumps(
        constraints, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _normalize_strings(values: list[str] | None, label: str) -> list[str]:
    if values is not None and not isinstance(values, list):
        raise ValueError(f"{label} must be a list")
    normalized = []
    for value in values or []:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} items must be nonblank strings")
        item = value.strip()
        if item not in normalized:
            normalized.append(item)
    return normalized


def _normalize_viewports(values: list[str] | None) -> list[str]:
    viewports = _normalize_strings(values, "target_viewports")
    for viewport in viewports:
        if not VIEWPORT_PATTERN.fullmatch(viewport):
            raise ValueError("viewport must use WIDTHxHEIGHT with positive integers")
        width, height = (int(part) for part in viewport.split("x"))
        if width > MAX_VIEWPORT_DIMENSION or height > MAX_VIEWPORT_DIMENSION:
            raise ValueError("viewport dimensions must not exceed 10000")
    return viewports


def _normalize_production_paths(values: list[str] | None) -> list[str]:
    paths = _normalize_strings(values, "production_paths")
    for value in paths:
        path = Path(value)
        if (
            path.is_absolute()
            or "\\" in value
            or "\x00" in value
            or ":" in value
            or "@" in value
            or value.startswith("~")
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("production_path must be a safe project-relative path")
    return paths


def init_run(
    root: Path,
    slug: str,
    project_path: str | None = None,
    now: str | None = None,
    run_id: str | None = None,
    target_viewports: list[str] | None = None,
    required_content: list[str] | None = None,
    required_interactions: list[str] | None = None,
    production_paths: list[str] | None = None,
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
    normalized_viewports = _normalize_viewports(target_viewports)
    normalized_content = _normalize_strings(required_content, "required_content")
    normalized_interactions = _normalize_strings(
        required_interactions, "required_interactions"
    )
    normalized_production_paths = _normalize_production_paths(production_paths)
    normalized_project_path = _normalize_project_path(project_path)
    if not valid_rfc3339(timestamp):
        raise ValueError("initialization timestamp must be RFC3339")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": identifier,
        "slug": slug,
        "state": "initialized",
        "created_at": timestamp,
        "updated_at": timestamp,
        "project_path": normalized_project_path,
        "approved_direction_ids": [],
        "selected_direction_id": None,
        "revision_count": 0,
        "generation_budget": DEFAULT_GENERATION_BUDGET,
        "max_attempts_per_direction": DEFAULT_MAX_ATTEMPTS_PER_DIRECTION,
        "target_viewports": normalized_viewports,
        "required_content": normalized_content,
        "required_interactions": normalized_interactions,
        "production_paths": normalized_production_paths,
    }
    validate_run_manifest(manifest)
    run_dir = Path(root).expanduser() / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json_atomic(run_dir / "run.json", manifest)
    return run_dir


def _load_run_without_recovery(run_dir: Path) -> dict:
    try:
        manifest = json.loads(
            (Path(run_dir) / "run.json").read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"invalid run.json: {error}") from None
    validate_run_manifest(manifest)
    validate_state_artifacts(Path(run_dir), manifest)
    return manifest


def load_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    if os.path.lexists(run_dir / REVISION_MARKER_NAME):
        owner = _acquire_generation_lock(run_dir, utc_now())
        try:
            _recover_revision_transaction(owner)
        finally:
            _release_generation_lock(owner)
    return _load_run_without_recovery(run_dir)


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
    legacy_generation_keys = sorted(
        LEGACY_GENERATION_ACCOUNTING_KEYS.intersection(manifest)
    )
    if legacy_generation_keys:
        raise ValueError(
            "invalid run.json: legacy generation accounting keys are forbidden; "
            "migrate generation accounting to mockup-manifest.json: "
            + ", ".join(legacy_generation_keys)
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
        "target_viewports",
        "required_content",
        "required_interactions",
        "production_paths",
    )
    for key in required:
        if key not in manifest:
            raise ValueError(f"invalid run.json: missing required key: {key}")
    for key in ("run_id", "slug", "created_at", "updated_at"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ValueError(f"invalid run.json: {key} must be a non-empty string")
    for key in ("created_at", "updated_at"):
        if not valid_rfc3339(manifest[key]):
            raise ValueError(f"invalid run.json: {key} must be RFC3339")
    if manifest["state"] not in STATES:
        raise ValueError("invalid run.json: state is unsupported")
    if manifest["project_path"] is not None and not isinstance(
        manifest["project_path"], str
    ):
        raise ValueError("invalid run.json: project_path must be a string or null")
    if manifest["project_path"] is not None and _normalize_project_path(
        manifest["project_path"]
    ) != manifest["project_path"]:
        raise ValueError("invalid run.json: project_path must be normalized and absolute")
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
    normalized_viewports = _normalize_viewports(manifest["target_viewports"])
    if normalized_viewports != manifest["target_viewports"]:
        raise ValueError("invalid run.json: target_viewports must be normalized and unique")
    for key in ("required_content", "required_interactions"):
        normalized = _normalize_strings(manifest[key], key)
        if normalized != manifest[key]:
            raise ValueError(f"invalid run.json: {key} must be normalized and unique")
    normalized_paths = _normalize_production_paths(manifest["production_paths"])
    if normalized_paths != manifest["production_paths"]:
        raise ValueError("invalid run.json: production_paths must be normalized and unique")
    if manifest["state"] != "initialized":
        if not manifest["target_viewports"]:
            raise ValueError("invalid run.json: target_viewports must not be empty after initialization")
        if not manifest["required_content"]:
            raise ValueError("invalid run.json: required_content must not be empty after initialization")
        for key in (
            "brief_constraints",
            "brief_constraints_digest",
            "brief_locked_at",
        ):
            if key not in manifest:
                raise ValueError(f"invalid run.json: missing required key: {key}")
        constraints = manifest["brief_constraints"]
        if not isinstance(constraints, dict) or set(constraints) != set(
            _brief_constraints(manifest)
        ):
            raise ValueError("invalid run.json: brief constraints snapshot is malformed")
        if constraints != _brief_constraints(manifest):
            raise ValueError("invalid run.json: current fields differ from brief constraints")
        if manifest["brief_constraints_digest"] != _constraints_digest(constraints):
            raise ValueError("invalid run.json: brief_constraints_digest does not match snapshot")
        if not valid_rfc3339(manifest["brief_locked_at"]):
            raise ValueError("invalid run.json: brief_locked_at must be RFC3339")
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
    for key in ("integration_approved_at", "last_revision_at"):
        if key in manifest and not valid_rfc3339(manifest[key]):
            raise ValueError(f"invalid run.json: {key} must be RFC3339")


def validate_state_artifacts(run_dir: Path, manifest: dict) -> None:
    state = manifest["state"]
    if state == "brief_ready":
        _validate_brief(run_dir, manifest)
    phases = STATE_VALIDATION_PHASES[state]
    for phase in phases:
        _validate_phases(run_dir, (phase,))
        if phase == "directions":
            _validate_approved_directions(run_dir, manifest)
    mockup_manifest_path = run_dir / "mockup-manifest.json"
    try:
        mockup_metadata = mockup_manifest_path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(f"invalid mockup-manifest.json: {error}") from None
    if not stat.S_ISREG(mockup_metadata.st_mode):
        raise ValueError(
            "invalid mockup-manifest.json: ledger must be a regular file"
        )
    errors = _validator_module().validate_mockup_manifest_for_generation(run_dir)
    if errors:
        raise ValueError(
            f"generation manifest validation failed: {'; '.join(errors[:5])}"
        )


def _validate_approved_directions(run_dir: Path, manifest: dict) -> None:
    if STATES.index(manifest["state"]) < STATES.index("directions_approved"):
        return
    approved = manifest["approved_direction_ids"]
    if not approved:
        raise ValueError(
            "invalid run.json: approved_direction_ids must be non-empty after approval"
        )
    if len(approved) > manifest["generation_budget"]:
        raise ValueError("invalid run.json: approved_direction_ids exceed generation_budget")
    errors = _validator_module().validate_phase(run_dir, "directions")
    if errors:
        raise ValueError(f"invalid current directions artifact: {'; '.join(errors[:5])}")
    try:
        directions = json.loads((run_dir / "directions.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"invalid current directions artifact: {error}") from None
    known = {item["id"] for item in directions}
    unknown = set(approved) - known
    if unknown:
        raise ValueError(
            "invalid run.json: approved IDs are not in current directions: "
            + ", ".join(sorted(unknown))
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


def _validate_brief(run_dir: Path, manifest: dict) -> None:
    _require_files(run_dir, "brief_ready")
    if not manifest["target_viewports"]:
        raise ValueError("brief validation failed: target_viewports must not be empty")
    if not manifest["required_content"]:
        raise ValueError("brief validation failed: required_content must not be empty")
    try:
        brief = (run_dir / "brief.md").read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"brief validation failed: {error}") from None
    if not brief.strip():
        raise ValueError("brief validation failed: brief.md must be nonblank")
    secret_errors = _validator_module().find_secret_errors(brief, "brief.md")
    if secret_errors:
        raise ValueError(f"brief validation failed: {secret_errors[0]}")
    if not manifest["required_interactions"] and not re.search(
        r"(?im)^interactive requirements\s*:\s*none\s*$", brief
    ):
        raise ValueError(
            "brief validation failed: empty required_interactions requires "
            "explicit interactive requirements: none"
        )


def _validate_phases(run_dir: Path, phases: tuple[str, ...]) -> None:
    phase_files = {
        "research": REQUIRED_FILES["research_complete"],
        "directions": REQUIRED_FILES["directions_pending_approval"],
        "mockups": REQUIRED_FILES["mockups_generated"],
        "implementation": REQUIRED_FILES["prototype_ready"],
    }
    phase_nonblank = {
        "research": ("design-evidence.md", "reference-board.md"),
        "directions": ("mood-directions.md",),
    }
    for phase in phases:
        missing = [name for name in phase_files[phase] if not (run_dir / name).is_file()]
        if missing:
            raise ValueError(f"{phase} validation failed: missing {', '.join(missing)}")
        for name in phase_nonblank.get(phase, ()):
            try:
                value = (run_dir / name).read_text(encoding="utf-8")
            except OSError as error:
                raise ValueError(f"{phase} validation failed: {error}") from None
            if not value.strip():
                raise ValueError(f"{phase} validation failed: {name} must be nonblank")
            secret_errors = _validator_module().find_secret_errors(value, name)
            if secret_errors:
                raise ValueError(f"{phase} validation failed: {secret_errors[0]}")
        errors = _validator_module().validate_phase(run_dir, phase)
        if errors:
            summary = "; ".join(errors[:5])
            if len(errors) > 5:
                summary += f"; plus {len(errors) - 5} more"
            raise ValueError(f"{phase} validation failed: {summary}")


def _validate_target(run_dir: Path, target: str, manifest: dict) -> None:
    if target == "brief_ready":
        _validate_brief(run_dir, manifest)
    else:
        _validate_phases(run_dir, STATE_VALIDATION_PHASES[target])


def _transition_run_locked(
    owner,
    target: str,
    timestamp: str,
    approved_direction_ids: list[str] | None = None,
    selected_direction_id: str | None = None,
    integration_approved: bool = False,
    generation_budget: int | None = None,
    max_attempts_per_direction: int | None = None,
    budget_expansion_approved: bool = False,
) -> dict:
    run_dir = owner.run_dir
    manifest = _load_run_without_recovery(run_dir)
    current = manifest["state"]
    if NEXT_STATE.get(current) != target:
        raise ValueError(f"illegal transition: {current} -> {target}")
    _validate_target(run_dir, target, manifest)
    if target == "integrated":
        if not integration_approved:
            raise ValueError("integrated requires explicit integration approval")
        manifest["integration_approved_at"] = timestamp

    if target == "brief_ready":
        constraints = _brief_constraints(manifest)
        manifest["brief_constraints"] = constraints
        manifest["brief_constraints_digest"] = _constraints_digest(constraints)
        manifest["brief_locked_at"] = timestamp

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
        else:
            manifest.pop("budget_expansion_approved_at", None)

    if target == "implementation_selected":
        if selected_direction_id not in manifest["approved_direction_ids"]:
            raise ValueError("selected_direction_id must be an approved direction")
        manifest["selected_direction_id"] = selected_direction_id

    manifest["state"] = target
    manifest["updated_at"] = timestamp
    validate_run_manifest(manifest)
    _publish_transition_run(owner, manifest)
    return manifest


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
    timestamp = now or utc_now()
    if not valid_rfc3339(timestamp):
        raise ValueError("transition timestamp must be RFC3339")
    owner = _acquire_generation_lock(Path(run_dir), timestamp)
    try:
        _recover_revision_transaction(owner)
        return _transition_run_locked(
            owner,
            target,
            approved_direction_ids=approved_direction_ids,
            selected_direction_id=selected_direction_id,
            timestamp=timestamp,
            integration_approved=integration_approved,
            generation_budget=generation_budget,
            max_attempts_per_direction=max_attempts_per_direction,
            budget_expansion_approved=budget_expansion_approved,
        )
    finally:
        _release_generation_lock(owner)


def _revise_run_locked(owner, reason: str, timestamp: str) -> dict:
    manifest = _load_run_without_recovery(owner.run_dir)
    current = manifest["state"]
    if current != "mockups_generated":
        raise ValueError(f"illegal revision from {current}")
    _validate_target(owner.run_dir, "mockups_generated", manifest)

    revision_count = manifest.get("revision_count", 0)
    if (
        not isinstance(revision_count, int)
        or isinstance(revision_count, bool)
        or revision_count < 0
    ):
        raise ValueError("run.json revision_count must be a non-negative integer")
    revision_count += 1
    archive_name = f"mockup-manifest.revision-{revision_count}.json"
    if _named_path_exists(owner.descriptor, archive_name):
        raise ValueError(f"revision archive already exists: {archive_name}")

    ledger_data, ledger_metadata = _read_run_file(owner, "mockup-manifest.json")
    if ledger_metadata.st_nlink != 1:
        raise ValueError("current mockup manifest must have exactly one link")
    revised = _revision_target_manifest(manifest, reason.strip(), timestamp)
    transaction = {
        "schema_version": 1,
        "transaction_id": owner.transaction_id,
        "archive_name": archive_name,
        "ledger_digest": "sha256:" + hashlib.sha256(ledger_data).hexdigest(),
        "ledger_device": ledger_metadata.st_dev,
        "ledger_inode": ledger_metadata.st_ino,
        "old_run": manifest,
        "new_run": revised,
    }
    try:
        _publish_revision_marker(owner, transaction)
        active = _load_revision_transaction(owner)
        _archive_revision_ledger(owner, active)
        _validate_phases(
            owner.run_dir,
            STATE_VALIDATION_PHASES["directions_pending_approval"],
        )
        _publish_revision_run(owner, active)
        _remove_revision_marker(owner, active)
    except BaseException:
        _recover_revision_transaction(owner)
        raise
    return revised


def revise_run(
    run_dir: Path,
    reason: str,
    now: str | None = None,
) -> dict:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("revision requires a non-empty reason")
    timestamp = now or utc_now()
    if not valid_rfc3339(timestamp):
        raise ValueError("revision timestamp must be RFC3339")
    owner = _acquire_generation_lock(Path(run_dir), timestamp)
    try:
        _recover_revision_transaction(owner)
        return _revise_run_locked(owner, reason, timestamp)
    finally:
        _release_generation_lock(owner)


def _read_mockup_manifest(run_dir: Path) -> dict:
    try:
        value = json.loads((run_dir / "mockup-manifest.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"invalid mockup-manifest.json: {error}") from None
    if not isinstance(value, dict) or not isinstance(value.get("mockups"), list):
        raise ValueError("invalid mockup-manifest.json: mockups must be a list")
    return value


def _generation_preflight_for_manifest(
    run_dir: Path, direction_id: str, manifest: dict
) -> tuple[dict, dict, dict]:
    if not isinstance(direction_id, str) or not direction_id.strip():
        raise ValueError("generation requires a non-empty direction_id")
    if manifest["state"] != "directions_approved":
        raise ValueError("generation requires state directions_approved")
    if direction_id not in manifest["approved_direction_ids"]:
        raise ValueError("generation direction must be explicitly approved")
    errors = _validator_module().validate_mockup_manifest_for_generation(run_dir)
    if errors:
        raise ValueError(f"generation manifest validation failed: {'; '.join(errors[:5])}")
    mockup_manifest = _read_mockup_manifest(run_dir)
    entry = next(
        item
        for item in mockup_manifest["mockups"]
        if item.get("direction_id") == direction_id
    )
    status = entry["status"]
    attempt_count = entry["attempt_count"]
    if status == "success":
        raise ValueError("generation is already successful for this direction")
    if status == "pending" and attempt_count != 0:
        raise ValueError("generation already has an active reserved attempt")
    if status == "failed" and attempt_count >= manifest["max_attempts_per_direction"]:
        raise ValueError("generation attempt limit exhausted for this direction")
    if status not in {"pending", "failed"}:
        raise ValueError("generation status is not reservable")
    total_ceiling = (
        len(manifest["approved_direction_ids"])
        * manifest["max_attempts_per_direction"]
    )
    if mockup_manifest["generation_attempts_used"] >= total_ceiling:
        raise ValueError("generation total attempt authorization ceiling exhausted")
    return manifest, mockup_manifest, entry


def _generation_preflight(run_dir: Path, direction_id: str) -> tuple[dict, dict, dict]:
    run_dir = Path(run_dir)
    return _generation_preflight_for_manifest(
        run_dir, direction_id, load_run(run_dir)
    )


def _generation_preflight_locked(owner, direction_id: str) -> tuple[dict, dict, dict]:
    return _generation_preflight_for_manifest(
        owner.run_dir,
        direction_id,
        _load_run_without_recovery(owner.run_dir),
    )


def image_generation_allowed(run_dir: Path, direction_id: str) -> bool:
    try:
        run_dir = Path(run_dir)
        if _generation_lock_is_held(run_dir):
            return False
        _generation_preflight(run_dir, direction_id)
        return True
    except (ValueError, json.JSONDecodeError, OSError, TypeError):
        return False


def _replace_path(
    source: str | Path,
    destination: str | Path,
    *,
    src_dir_fd: int | None = None,
    dst_dir_fd: int | None = None,
) -> None:
    os.replace(
        source,
        destination,
        src_dir_fd=src_dir_fd,
        dst_dir_fd=dst_dir_fd,
    )


def _fsync_generation_file(descriptor: int) -> None:
    os.fsync(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if (
            not isinstance(written, int)
            or isinstance(written, bool)
            or written <= 0
            or written > len(remaining)
        ):
            raise OSError("generation file write made no progress")
        remaining = remaining[written:]


def _read_all(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


@dataclass(frozen=True)
class _StagedGenerationManifest:
    descriptor: int
    name: str
    data: bytes
    value: dict
    device: int
    inode: int


def _validate_generation_bytes(data: bytes, expected_value: dict, label: str) -> None:
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON: {error}") from None
    if parsed != expected_value:
        raise ValueError(f"{label} does not match reservation")


def _fsync_directory_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
            raise


def _named_inode_matches(directory_fd: int, name: str, device: int, inode: int) -> bool:
    try:
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (named.st_dev, named.st_ino) == (device, inode)


def _stage_generation_manifest(
    owner,
    value: dict,
    transaction_id: str,
    exact_data: bytes | None = None,
) -> _StagedGenerationManifest:
    name = f".mockup-manifest.json.generation-{transaction_id}.tmp"
    data = (
        (json.dumps(value, indent=2) + "\n").encode("utf-8")
        if exact_data is None
        else exact_data
    )
    descriptor = os.open(
        name,
        os.O_CREAT
        | os.O_EXCL
        | os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=owner.descriptor,
    )
    initial = os.fstat(descriptor)
    try:
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise ValueError("staged generation manifest must be a private regular file")
        _write_all(descriptor, data)
        _fsync_generation_file(descriptor)
        staged_data = _read_all(descriptor)
        if staged_data != data:
            raise ValueError("staged generation manifest bytes do not match reservation")
        _validate_generation_bytes(staged_data, value, "staged generation manifest")
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino)
            or not _named_inode_matches(
                owner.descriptor, name, current.st_dev, current.st_ino
            )
        ):
            raise ValueError("staged generation manifest inode changed")
        return _StagedGenerationManifest(
            descriptor,
            name,
            data,
            value,
            current.st_dev,
            current.st_ino,
        )
    except BaseException:
        if _named_inode_matches(
            owner.descriptor, name, initial.st_dev, initial.st_ino
        ):
            try:
                os.unlink(name, dir_fd=owner.descriptor)
            except FileNotFoundError:
                pass
        os.close(descriptor)
        raise


def _close_staged_generation_manifest(owner, stage: _StagedGenerationManifest) -> None:
    try:
        if _named_inode_matches(
            owner.descriptor, stage.name, stage.device, stage.inode
        ):
            try:
                os.unlink(stage.name, dir_fd=owner.descriptor)
            except FileNotFoundError:
                pass
    finally:
        os.close(stage.descriptor)


def _read_run_file(owner, name: str) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=owner.descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{name} must be a regular file")
        return _read_all(descriptor), metadata
    finally:
        os.close(descriptor)


def _published_stage_matches(owner, stage: _StagedGenerationManifest) -> bool:
    try:
        data, metadata = _read_run_file(owner, "mockup-manifest.json")
    except (OSError, ValueError):
        return False
    if (
        (metadata.st_dev, metadata.st_ino) != (stage.device, stage.inode)
        or metadata.st_nlink != 1
        or data != stage.data
    ):
        return False
    try:
        _validate_generation_bytes(
            data, stage.value, "published generation manifest"
        )
    except ValueError:
        return False
    return True


def _target_bytes_match(owner, expected: bytes) -> bool:
    try:
        data, _metadata = _read_run_file(owner, "mockup-manifest.json")
    except (OSError, ValueError):
        return False
    return data == expected


def _restore_generation_manifest(owner, prior_data: bytes) -> None:
    try:
        prior_value = json.loads(prior_data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"prior generation manifest is invalid: {error}") from None
    rollback = _stage_generation_manifest(
        owner, prior_value, uuid.uuid4().hex, exact_data=prior_data
    )
    try:
        if not _named_inode_matches(
            owner.descriptor, rollback.name, rollback.device, rollback.inode
        ):
            raise ValueError("rollback generation manifest inode changed")
        _replace_path(
            rollback.name,
            "mockup-manifest.json",
            src_dir_fd=owner.descriptor,
            dst_dir_fd=owner.descriptor,
        )
        _fsync_directory_descriptor(owner.descriptor)
        if not _published_stage_matches(owner, rollback):
            raise ValueError("failed to restore prior generation manifest")
    finally:
        _close_staged_generation_manifest(owner, rollback)


def _write_mockup_manifest_atomic(
    owner, value: dict, prior_data: bytes
) -> None:
    stage = _stage_generation_manifest(owner, value, owner.transaction_id)
    try:
        if not _named_inode_matches(
            owner.descriptor, stage.name, stage.device, stage.inode
        ):
            raise ValueError("staged generation manifest inode changed before publish")
        try:
            _replace_path(
                stage.name,
                "mockup-manifest.json",
                src_dir_fd=owner.descriptor,
                dst_dir_fd=owner.descriptor,
            )
            _fsync_directory_descriptor(owner.descriptor)
        except BaseException:
            if _published_stage_matches(owner, stage):
                raise
            if not _target_bytes_match(owner, prior_data):
                _restore_generation_manifest(owner, prior_data)
            raise
        if not _published_stage_matches(owner, stage):
            if not _target_bytes_match(owner, prior_data):
                _restore_generation_manifest(owner, prior_data)
            raise ValueError(
                "published generation manifest does not match staged reservation"
            )
        try:
            _assert_generation_run_identity(owner)
        except BaseException:
            _restore_generation_manifest(owner, prior_data)
            raise
    finally:
        _close_staged_generation_manifest(owner, stage)


@dataclass(frozen=True)
class _GenerationLock:
    descriptor: int
    transaction_id: str
    run_dir: Path
    device: int
    inode: int
    runs_root_descriptor: int
    runs_root: Path
    runs_root_device: int
    runs_root_inode: int
    account_home_descriptor: int
    account_home: Path
    account_home_device: int
    account_home_inode: int


def _open_trusted_directory(path: Path, label: str) -> tuple[int, os.stat_result]:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        named = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError(
                f"{label} must be an owned, non-writable-by-others stable directory"
            )
        return descriptor, metadata
    except BaseException:
        os.close(descriptor)
        raise


def _account_home_mutex_directory() -> tuple[int, Path, os.stat_result]:
    uid = os.getuid()
    try:
        home_value = pwd.getpwuid(uid).pw_dir
    except (KeyError, OSError) as error:
        raise ValueError(
            f"stable account-home generation mutex unavailable: {error}"
        ) from None
    if not isinstance(home_value, str) or not home_value:
        raise ValueError(
            "stable account-home generation mutex unavailable: passwd home is empty"
        )
    account_home = Path(home_value)
    if not account_home.is_absolute():
        raise ValueError(
            "stable account-home generation mutex unavailable: passwd home must be absolute"
        )
    try:
        resolved_home = account_home.resolve(strict=True)
    except OSError as error:
        raise ValueError(
            f"stable account-home generation mutex unavailable: {error}"
        ) from None
    if resolved_home != account_home:
        raise ValueError(
            "stable account-home generation mutex unavailable: passwd home must be a real path"
        )

    parent = account_home.parent
    try:
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ValueError(
            f"stable account-home generation mutex unavailable: cannot open parent: {error}"
        ) from None
    try:
        parent_metadata = os.fstat(parent_descriptor)
        parent_named = parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != 0
            or stat.S_IMODE(parent_metadata.st_mode) & 0o022
            or (parent_metadata.st_dev, parent_metadata.st_ino)
            != (parent_named.st_dev, parent_named.st_ino)
        ):
            raise ValueError(
                "stable account-home generation mutex unavailable: parent must be "
                "root-owned and not group- or world-writable"
            )
    except OSError as error:
        raise ValueError(
            f"stable account-home generation mutex unavailable: invalid parent: {error}"
        ) from None
    finally:
        os.close(parent_descriptor)

    try:
        descriptor = os.open(
            account_home,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ValueError(
            f"stable account-home generation mutex unavailable: cannot open home: {error}"
        ) from None
    try:
        metadata = os.fstat(descriptor)
        named = account_home.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != uid
            or (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError(
                "stable account-home generation mutex unavailable: passwd home "
                "must be an owned stable directory"
            )
        return descriptor, account_home, metadata
    except BaseException:
        os.close(descriptor)
        raise


def _assert_generation_account_home_identity(owner: _GenerationLock) -> None:
    metadata = os.fstat(owner.account_home_descriptor)
    try:
        named = owner.account_home.stat(follow_symlinks=False)
    except (FileNotFoundError, OSError):
        raise ValueError("generation account home path was replaced") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or (metadata.st_dev, metadata.st_ino)
        != (owner.account_home_device, owner.account_home_inode)
        or (named.st_dev, named.st_ino)
        != (owner.account_home_device, owner.account_home_inode)
    ):
        raise ValueError("generation account home path was replaced")


def _assert_generation_runs_root_identity(owner: _GenerationLock) -> None:
    _assert_generation_account_home_identity(owner)
    metadata = os.fstat(owner.runs_root_descriptor)
    try:
        named = owner.runs_root.stat(follow_symlinks=False)
    except (FileNotFoundError, OSError):
        raise ValueError("generation runs root path was replaced") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or (metadata.st_dev, metadata.st_ino)
        != (owner.runs_root_device, owner.runs_root_inode)
        or (named.st_dev, named.st_ino)
        != (owner.runs_root_device, owner.runs_root_inode)
    ):
        raise ValueError("generation runs root path was replaced")


def _assert_generation_run_identity(owner: _GenerationLock) -> None:
    _assert_generation_runs_root_identity(owner)
    descriptor_stat = os.fstat(owner.descriptor)
    try:
        path_stat = owner.run_dir.stat(follow_symlinks=False)
    except (FileNotFoundError, OSError):
        raise ValueError("generation run directory path was replaced") from None
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or (descriptor_stat.st_dev, descriptor_stat.st_ino)
        != (owner.device, owner.inode)
        or (path_stat.st_dev, path_stat.st_ino) != (owner.device, owner.inode)
    ):
        raise ValueError("generation run directory path was replaced")


def _unlock_generation_lock(owner: _GenerationLock) -> None:
    try:
        fcntl.flock(owner.descriptor, fcntl.LOCK_UN)
    finally:
        try:
            fcntl.flock(owner.runs_root_descriptor, fcntl.LOCK_UN)
        finally:
            fcntl.flock(owner.account_home_descriptor, fcntl.LOCK_UN)


def _generation_lock_is_held(run_dir: Path) -> bool:
    home_descriptor, _account_home, _home_metadata = (
        _account_home_mutex_directory()
    )
    try:
        try:
            fcntl.flock(home_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                return True
            raise
        resolved = Path(run_dir).expanduser().resolve(strict=True)
        runs_root = resolved.parent.resolve(strict=True)
        root_descriptor, _root_metadata = _open_trusted_directory(
            runs_root, "generation runs root"
        )
        try:
            try:
                fcntl.flock(root_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN}:
                    return True
                raise
            descriptor, _metadata = _open_trusted_directory(
                resolved, "generation run path"
            )
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN}:
                        return True
                    raise
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                return False
            finally:
                os.close(descriptor)
        finally:
            try:
                fcntl.flock(root_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(root_descriptor)
    finally:
        try:
            fcntl.flock(home_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(home_descriptor)


def _acquire_generation_lock(run_dir: Path, timestamp: str) -> _GenerationLock:
    home_descriptor, account_home, home_stat = _account_home_mutex_directory()
    root_descriptor = None
    descriptor = None
    try:
        try:
            fcntl.flock(home_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise ValueError(
                    "run mutation is already in progress"
                ) from None
            raise
        resolved = Path(run_dir).expanduser().resolve(strict=True)
        runs_root = resolved.parent.resolve(strict=True)
        root_descriptor, root_stat = _open_trusted_directory(
            runs_root, "generation runs root"
        )
        try:
            fcntl.flock(root_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise ValueError(
                    "run mutation is already in progress"
                ) from None
            raise
        descriptor, descriptor_stat = _open_trusted_directory(
            resolved, "generation run path"
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise ValueError(
                    "run mutation is already in progress"
                ) from None
            raise
        transaction_id = uuid.uuid4().hex
        owner = _GenerationLock(
            descriptor,
            transaction_id,
            resolved,
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
            root_descriptor,
            runs_root,
            root_stat.st_dev,
            root_stat.st_ino,
            home_descriptor,
            account_home,
            home_stat.st_dev,
            home_stat.st_ino,
        )
        _assert_generation_run_identity(owner)
        removed_temp = False
        for name in os.listdir(descriptor):
            if (
                GENERATION_TEMP_PATTERN.fullmatch(name) is None
                and REVISION_TEMP_PATTERN.fullmatch(name) is None
            ):
                continue
            try:
                os.unlink(name, dir_fd=descriptor)
                removed_temp = True
            except FileNotFoundError:
                pass
        if removed_temp:
            _fsync_directory_descriptor(descriptor)
        return owner
    except BaseException:
        try:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            if root_descriptor is not None:
                try:
                    fcntl.flock(root_descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(root_descriptor)
            try:
                fcntl.flock(home_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(home_descriptor)
        raise


def _release_generation_lock(owner: _GenerationLock) -> None:
    try:
        _unlock_generation_lock(owner)
    finally:
        try:
            os.close(owner.descriptor)
        finally:
            try:
                os.close(owner.runs_root_descriptor)
            finally:
                os.close(owner.account_home_descriptor)


@dataclass(frozen=True)
class _StagedRevisionJson:
    descriptor: int
    name: str
    data: bytes
    value: dict
    device: int
    inode: int


@dataclass(frozen=True)
class _RevisionTransaction:
    marker_device: int
    marker_inode: int
    transaction_id: str
    archive_name: str
    ledger_digest: str
    ledger_device: int
    ledger_inode: int
    old_run: dict
    new_run: dict


def _named_path_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _revision_target_manifest(manifest: dict, reason: str, timestamp: str) -> dict:
    revised = json.loads(json.dumps(manifest))
    revised["state"] = "directions_pending_approval"
    revised["revision_count"] += 1
    revised["last_revision_reason"] = reason
    revised["last_revision_at"] = timestamp
    revised["approved_direction_ids"] = []
    revised["selected_direction_id"] = None
    revised["generation_budget"] = DEFAULT_GENERATION_BUDGET
    revised["max_attempts_per_direction"] = DEFAULT_MAX_ATTEMPTS_PER_DIRECTION
    revised.pop("budget_expansion_approved_at", None)
    revised["updated_at"] = timestamp
    validate_run_manifest(revised)
    return revised


def _stage_revision_json(
    owner: _GenerationLock, prefix: str, value: dict
) -> _StagedRevisionJson:
    name = f".{prefix}-{owner.transaction_id}.tmp"
    data = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(
        name,
        os.O_CREAT
        | os.O_EXCL
        | os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=owner.descriptor,
    )
    initial = os.fstat(descriptor)
    try:
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise ValueError("staged revision JSON must be a private regular file")
        _write_all(descriptor, data)
        _fsync_generation_file(descriptor)
        staged_data = _read_all(descriptor)
        try:
            staged_value = json.loads(staged_data)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(f"staged revision JSON is invalid: {error}") from None
        current = os.fstat(descriptor)
        if (
            staged_data != data
            or staged_value != value
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino)
            or not _named_inode_matches(
                owner.descriptor, name, current.st_dev, current.st_ino
            )
        ):
            raise ValueError("staged revision JSON changed before publication")
        return _StagedRevisionJson(
            descriptor,
            name,
            data,
            value,
            current.st_dev,
            current.st_ino,
        )
    except BaseException:
        if _named_inode_matches(
            owner.descriptor, name, initial.st_dev, initial.st_ino
        ):
            os.unlink(name, dir_fd=owner.descriptor)
        os.close(descriptor)
        raise


def _close_staged_revision_json(
    owner: _GenerationLock, stage: _StagedRevisionJson
) -> None:
    try:
        if _named_inode_matches(
            owner.descriptor, stage.name, stage.device, stage.inode
        ):
            os.unlink(stage.name, dir_fd=owner.descriptor)
    finally:
        os.close(stage.descriptor)


def _read_named_json(
    owner: _GenerationLock, name: str
) -> tuple[dict, bytes, os.stat_result]:
    data, metadata = _read_run_file(owner, name)
    if metadata.st_nlink != 1:
        raise ValueError(f"{name} must have exactly one link")
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"invalid {name}: {error}") from None
    if not isinstance(value, dict):
        raise ValueError(f"invalid {name}: JSON value must be an object")
    return value, data, metadata


def _validate_revision_transaction(
    value: dict, metadata: os.stat_result
) -> _RevisionTransaction:
    required = {
        "schema_version",
        "transaction_id",
        "archive_name",
        "ledger_digest",
        "ledger_device",
        "ledger_inode",
        "old_run",
        "new_run",
    }
    if set(value) != required or value.get("schema_version") != 1:
        raise ValueError("invalid revision transaction marker schema")
    transaction_id = value.get("transaction_id")
    if not isinstance(transaction_id, str) or re.fullmatch(
        r"[0-9a-f]{32}", transaction_id
    ) is None:
        raise ValueError("invalid revision transaction ID")
    old_run = value.get("old_run")
    new_run = value.get("new_run")
    validate_run_manifest(old_run)
    validate_run_manifest(new_run)
    if old_run["state"] != "mockups_generated":
        raise ValueError("revision transaction old state must be mockups_generated")
    reason = new_run.get("last_revision_reason")
    timestamp = new_run.get("last_revision_at")
    if not isinstance(reason, str) or not reason.strip() or not valid_rfc3339(timestamp):
        raise ValueError("revision transaction audit fields are invalid")
    expected_new = _revision_target_manifest(old_run, reason, timestamp)
    if new_run != expected_new:
        raise ValueError("revision transaction target manifest is invalid")
    archive_name = value.get("archive_name")
    expected_archive = f"mockup-manifest.revision-{new_run['revision_count']}.json"
    if (
        not isinstance(archive_name, str)
        or REVISION_ARCHIVE_PATTERN.fullmatch(archive_name) is None
        or archive_name != expected_archive
    ):
        raise ValueError("revision transaction archive path is invalid")
    ledger_digest = value.get("ledger_digest")
    if not isinstance(ledger_digest, str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", ledger_digest
    ) is None:
        raise ValueError("revision transaction ledger digest is invalid")
    ledger_device = value.get("ledger_device")
    ledger_inode = value.get("ledger_inode")
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item <= 0
        for item in (ledger_device, ledger_inode)
    ):
        raise ValueError("revision transaction ledger inode is invalid")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
    ):
        raise ValueError("revision transaction marker must be a private regular file")
    return _RevisionTransaction(
        metadata.st_dev,
        metadata.st_ino,
        transaction_id,
        archive_name,
        ledger_digest,
        ledger_device,
        ledger_inode,
        old_run,
        new_run,
    )


def _load_revision_transaction(owner: _GenerationLock) -> _RevisionTransaction:
    value, _data, metadata = _read_named_json(owner, REVISION_MARKER_NAME)
    return _validate_revision_transaction(value, metadata)


def _publish_revision_marker(owner: _GenerationLock, value: dict) -> None:
    if _named_path_exists(owner.descriptor, REVISION_MARKER_NAME):
        raise ValueError("a revision transaction is already pending")
    stage = _stage_revision_json(owner, "revision-transaction", value)
    try:
        os.link(
            stage.name,
            REVISION_MARKER_NAME,
            src_dir_fd=owner.descriptor,
            dst_dir_fd=owner.descriptor,
            follow_symlinks=False,
        )
        _fsync_directory_descriptor(owner.descriptor)
        if not _named_inode_matches(
            owner.descriptor, REVISION_MARKER_NAME, stage.device, stage.inode
        ):
            raise ValueError("revision transaction marker inode changed")
        os.unlink(stage.name, dir_fd=owner.descriptor)
        _fsync_directory_descriptor(owner.descriptor)
        _load_revision_transaction(owner)
    finally:
        _close_staged_revision_json(owner, stage)


def _ledger_transaction_metadata(
    owner: _GenerationLock,
    transaction: _RevisionTransaction,
    name: str,
) -> os.stat_result | None:
    if not _named_path_exists(owner.descriptor, name):
        return None
    data, metadata = _read_run_file(owner, name)
    if (
        metadata.st_dev != transaction.ledger_device
        or metadata.st_ino != transaction.ledger_inode
        or metadata.st_nlink not in {1, 2}
        or "sha256:" + hashlib.sha256(data).hexdigest()
        != transaction.ledger_digest
    ):
        raise ValueError(f"revision ledger identity mismatch: {name}")
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"invalid revision ledger {name}: {error}") from None
    if not isinstance(value, dict) or not isinstance(value.get("mockups"), list):
        raise ValueError(f"invalid revision ledger {name}")
    return metadata


def _archive_revision_ledger(
    owner: _GenerationLock, transaction: _RevisionTransaction
) -> None:
    current = _ledger_transaction_metadata(
        owner, transaction, "mockup-manifest.json"
    )
    if current is None or current.st_nlink != 1:
        raise ValueError("current revision ledger is unavailable")
    if _named_path_exists(owner.descriptor, transaction.archive_name):
        raise ValueError(
            f"revision archive already exists: {transaction.archive_name}"
        )
    os.link(
        "mockup-manifest.json",
        transaction.archive_name,
        src_dir_fd=owner.descriptor,
        dst_dir_fd=owner.descriptor,
        follow_symlinks=False,
    )
    _fsync_directory_descriptor(owner.descriptor)
    linked_current = _ledger_transaction_metadata(
        owner, transaction, "mockup-manifest.json"
    )
    linked_archive = _ledger_transaction_metadata(
        owner, transaction, transaction.archive_name
    )
    if (
        linked_current is None
        or linked_archive is None
        or linked_current.st_nlink != 2
        or linked_archive.st_nlink != 2
    ):
        raise ValueError("revision archive link was not published safely")
    os.unlink("mockup-manifest.json", dir_fd=owner.descriptor)
    _fsync_directory_descriptor(owner.descriptor)
    archived = _ledger_transaction_metadata(
        owner, transaction, transaction.archive_name
    )
    if archived is None or archived.st_nlink != 1:
        raise ValueError("revision archive move did not complete")


def _publish_run_manifest(
    owner: _GenerationLock, value: dict, prefix: str, label: str
) -> None:
    _assert_generation_run_identity(owner)
    stage = _stage_revision_json(owner, prefix, value)
    try:
        _replace_path(
            stage.name,
            "run.json",
            src_dir_fd=owner.descriptor,
            dst_dir_fd=owner.descriptor,
        )
        _fsync_directory_descriptor(owner.descriptor)
        value, data, metadata = _read_named_json(owner, "run.json")
        if (
            value != stage.value
            or data != stage.data
            or (metadata.st_dev, metadata.st_ino) != (stage.device, stage.inode)
        ):
            raise ValueError(f"published {label} run manifest did not match stage")
        _assert_generation_run_identity(owner)
    finally:
        _close_staged_revision_json(owner, stage)


def _publish_revision_run(
    owner: _GenerationLock, transaction: _RevisionTransaction
) -> None:
    _publish_run_manifest(
        owner, transaction.new_run, "run.json.revision", "revision"
    )


def _publish_transition_run(owner: _GenerationLock, value: dict) -> None:
    _publish_run_manifest(owner, value, "run.json.transition", "transition")


def _remove_revision_marker(
    owner: _GenerationLock, transaction: _RevisionTransaction
) -> None:
    if not _named_inode_matches(
        owner.descriptor,
        REVISION_MARKER_NAME,
        transaction.marker_device,
        transaction.marker_inode,
    ):
        raise ValueError("revision transaction marker path was replaced")
    os.unlink(REVISION_MARKER_NAME, dir_fd=owner.descriptor)
    _fsync_directory_descriptor(owner.descriptor)


def _restore_revision_ledger(
    owner: _GenerationLock,
    transaction: _RevisionTransaction,
    current: os.stat_result | None,
    archive: os.stat_result | None,
) -> None:
    if current is not None and archive is None:
        if current.st_nlink != 1:
            raise ValueError("current revision ledger link count is invalid")
        return
    if current is not None and archive is not None:
        if current.st_nlink != 2 or archive.st_nlink != 2:
            raise ValueError("split revision ledger links are invalid")
        os.unlink(transaction.archive_name, dir_fd=owner.descriptor)
        _fsync_directory_descriptor(owner.descriptor)
    elif current is None and archive is not None:
        if archive.st_nlink != 1:
            raise ValueError("revision archive link count is invalid")
        os.link(
            transaction.archive_name,
            "mockup-manifest.json",
            src_dir_fd=owner.descriptor,
            dst_dir_fd=owner.descriptor,
            follow_symlinks=False,
        )
        _fsync_directory_descriptor(owner.descriptor)
        restored_archive = _ledger_transaction_metadata(
            owner, transaction, transaction.archive_name
        )
        restored_current = _ledger_transaction_metadata(
            owner, transaction, "mockup-manifest.json"
        )
        if (
            restored_archive is None
            or restored_current is None
            or restored_archive.st_nlink != 2
            or restored_current.st_nlink != 2
        ):
            raise ValueError("revision ledger rollback link failed")
        os.unlink(transaction.archive_name, dir_fd=owner.descriptor)
        _fsync_directory_descriptor(owner.descriptor)
    else:
        raise ValueError("revision transaction lost both ledger names")
    restored = _ledger_transaction_metadata(
        owner, transaction, "mockup-manifest.json"
    )
    if restored is None or restored.st_nlink != 1:
        raise ValueError("revision ledger rollback did not complete")


def _recover_revision_transaction(owner: _GenerationLock) -> bool:
    if not _named_path_exists(owner.descriptor, REVISION_MARKER_NAME):
        return False
    transaction = _load_revision_transaction(owner)
    actual_run, _run_data, _run_metadata = _read_named_json(owner, "run.json")
    current = _ledger_transaction_metadata(
        owner, transaction, "mockup-manifest.json"
    )
    archive = _ledger_transaction_metadata(
        owner, transaction, transaction.archive_name
    )
    if actual_run == transaction.old_run:
        _restore_revision_ledger(owner, transaction, current, archive)
        _remove_revision_marker(owner, transaction)
        return True
    if actual_run == transaction.new_run:
        if current is not None or archive is None or archive.st_nlink != 1:
            raise ValueError("committed revision ledger/archive state is invalid")
        _remove_revision_marker(owner, transaction)
        return True
    raise ValueError("run.json does not match either revision transaction state")


def _authorize_generation_locked(
    owner: _GenerationLock, direction_id: str, timestamp: str
) -> dict:
    _manifest, mockup_manifest, entry = _generation_preflight_locked(
        owner, direction_id
    )
    _assert_generation_run_identity(owner)
    prior_data, _prior_metadata = _read_run_file(owner, "mockup-manifest.json")
    try:
        prior_value = json.loads(prior_data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"invalid prior generation manifest: {error}") from None
    if prior_value != mockup_manifest:
        raise ValueError("generation manifest changed during authorization")
    entry["attempt_count"] += 1
    entry["status"] = "pending"
    for key in ("failure", "output_kind", "output_ref"):
        entry.pop(key, None)
    mockup_manifest["generation_attempts_used"] += 1
    mockup_manifest["last_generation_authorized_at"] = timestamp
    mockup_manifest["last_generation_direction_id"] = direction_id
    _assert_generation_run_identity(owner)
    _write_mockup_manifest_atomic(owner, mockup_manifest, prior_data)
    return {
        "direction_id": direction_id,
        "attempt_count": entry["attempt_count"],
        "generation_attempts_used": mockup_manifest["generation_attempts_used"],
        "authorized_at": timestamp,
    }


def authorize_generation(
    run_dir: Path, direction_id: str, now: str | None = None
) -> dict:
    timestamp = now or utc_now()
    if not valid_rfc3339(timestamp):
        raise ValueError("generation authorization timestamp must be RFC3339")
    owner = _acquire_generation_lock(Path(run_dir), timestamp)
    try:
        _recover_revision_transaction(owner)
        return _authorize_generation_locked(owner, direction_id, timestamp)
    finally:
        _release_generation_lock(owner)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--root", default="~/.codex/design-explorer/runs")
    init_parser.add_argument("--slug", required=True)
    init_parser.add_argument("--project-path")
    init_parser.add_argument("--viewport", action="append")
    init_parser.add_argument("--required-content", action="append")
    init_parser.add_argument("--required-interaction", action="append")
    init_parser.add_argument("--production-path", action="append")
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
    generation_parser = subparsers.add_parser("can-generate")
    generation_parser.add_argument("--run", required=True)
    generation_parser.add_argument("--direction")
    authorization_parser = subparsers.add_parser("authorize-generation")
    authorization_parser.add_argument("--run", required=True)
    authorization_parser.add_argument("--direction")
    args = parser.parse_args()

    try:
        if args.command == "init":
            run_dir = init_run(
                Path(args.root),
                args.slug,
                args.project_path,
                target_viewports=args.viewport,
                required_content=args.required_content,
                required_interactions=args.required_interaction,
                production_paths=args.production_path,
            )
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
        elif args.command == "can-generate":
            allowed = image_generation_allowed(Path(args.run), args.direction)
            print("true" if allowed else "false")
            return 0 if allowed else 1
        elif args.command == "authorize-generation":
            reservation = authorize_generation(Path(args.run), args.direction)
            print(json.dumps(reservation, indent=2))
        else:
            print(json.dumps(load_run(Path(args.run)), indent=2))
    except (ValueError, json.JSONDecodeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
