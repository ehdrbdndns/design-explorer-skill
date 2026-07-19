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


def load_run(run_dir: Path) -> dict:
    try:
        manifest = json.loads(
            (Path(run_dir) / "run.json").read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"invalid run.json: {error}") from None
    validate_run_manifest(manifest)
    validate_state_artifacts(Path(run_dir), manifest)
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
    _validate_target(run_dir, target, manifest)
    timestamp = now or utc_now()
    if not valid_rfc3339(timestamp):
        raise ValueError("transition timestamp must be RFC3339")

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
    _validate_target(run_dir, "mockups_generated", manifest)

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

    timestamp = now or utc_now()
    if not valid_rfc3339(timestamp):
        raise ValueError("revision timestamp must be RFC3339")
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
    validate_run_manifest(manifest)
    manifest_path.replace(archive_path)
    try:
        _validate_phases(
            run_dir, STATE_VALIDATION_PHASES["directions_pending_approval"]
        )
        write_json_atomic(run_dir / "run.json", manifest)
    except Exception:
        archive_path.replace(manifest_path)
        raise
    return manifest


def _read_mockup_manifest(run_dir: Path) -> dict:
    try:
        value = json.loads((run_dir / "mockup-manifest.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"invalid mockup-manifest.json: {error}") from None
    if not isinstance(value, dict) or not isinstance(value.get("mockups"), list):
        raise ValueError("invalid mockup-manifest.json: mockups must be a list")
    return value


def _generation_preflight(run_dir: Path, direction_id: str) -> tuple[dict, dict, dict]:
    if not isinstance(direction_id, str) or not direction_id.strip():
        raise ValueError("generation requires a non-empty direction_id")
    manifest = load_run(run_dir)
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
                    "generation authorization is already in progress"
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
                    "generation authorization is already in progress"
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
                    "generation authorization is already in progress"
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
            if GENERATION_TEMP_PATTERN.fullmatch(name) is None:
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


def authorize_generation(
    run_dir: Path, direction_id: str, now: str | None = None
) -> dict:
    run_dir = Path(run_dir)
    timestamp = now or utc_now()
    if not valid_rfc3339(timestamp):
        raise ValueError("generation authorization timestamp must be RFC3339")
    owner = _acquire_generation_lock(run_dir, timestamp)
    try:
        _manifest, mockup_manifest, entry = _generation_preflight(
            owner.run_dir, direction_id
        )
        _assert_generation_run_identity(owner)
        prior_data, _prior_metadata = _read_run_file(
            owner, "mockup-manifest.json"
        )
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
