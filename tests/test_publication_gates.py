import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from design_explorer_import import load_script_module
from test_preview_evidence import write_png
from test_run_state import direction, evidence, reference


run_state = load_script_module(
    "run_state_publication_gates", "design-explorer/scripts/run_state.py"
)
validator = load_script_module(
    "validate_run_publication_gates", "design-explorer/scripts/validate_run.py"
)


class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = run_state.init_run(
            self.root,
            "checkout",
            run_id="publication-run",
            now="2026-07-20T00:00:00Z",
            target_viewports=["390x844", "1280x800"],
            required_content=["Order summary"],
            required_interactions=["Edit order"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, name, value):
        (self.run / name).write_text(json.dumps(value), encoding="utf-8")

    def assert_generation_lock_released(self):
        self.assertFalse(run_state._generation_lock_is_held(self.run))

    def write_research(self):
        self.write_json("references.json", [reference()])
        self.write_json("evidence.json", [evidence()])
        (self.run / "design-evidence.md").write_text("# Evidence", encoding="utf-8")
        (self.run / "reference-board.md").write_text("# Board", encoding="utf-8")

    def write_directions(self):
        self.write_json(
            "directions.json", [direction(f"d-{index}", index) for index in range(5)]
        )
        (self.run / "mood-directions.md").write_text("# Directions", encoding="utf-8")

    def advance_to_approved(self, approved=("d-0",)):
        (self.run / "brief.md").write_text("# Brief", encoding="utf-8")
        run_state.transition_run(self.run, "brief_ready")
        self.write_research()
        run_state.transition_run(self.run, "research_complete")
        self.write_directions()
        run_state.transition_run(self.run, "directions_pending_approval")
        run_state.transition_run(
            self.run, "directions_approved", approved_direction_ids=list(approved)
        )

    def prompt(self, direction_id):
        path = self.run / "prompts" / f"{direction_id}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"full-screen prompt for {direction_id}\n", encoding="utf-8")
        return path

    def entry(self, direction_id="d-0", status="pending", attempts=0, **extra):
        prompt = self.prompt(direction_id)
        value = {
            "direction_id": direction_id,
            "status": status,
            "viewport": "390x844",
            "prompt_ref": prompt.relative_to(self.run).as_posix(),
            "prompt_digest": "sha256:" + hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "attempt_count": attempts,
        }
        value.update(extra)
        return value

    def write_pending_manifest(self, approved=("d-0",)):
        self.write_json(
            "mockup-manifest.json",
            self.ledger([self.entry(identifier) for identifier in approved]),
        )

    def ledger(self, entries, used=None, authorized_at=None, direction_id=None):
        used = sum(item.get("attempt_count", 0) for item in entries) if used is None else used
        if used and authorized_at is None:
            authorized_at = "2026-07-20T00:00:30Z"
        if used and direction_id is None:
            direction_id = entries[-1]["direction_id"]
        return {
            "schema_version": 1,
            "generation_attempts_used": used,
            "last_generation_authorized_at": authorized_at,
            "last_generation_direction_id": direction_id,
            "mockups": entries,
        }

    def test_mockups_enforce_comparable_viewport_prompt_and_typed_outputs(self):
        self.advance_to_approved(("d-0", "d-1"))
        first = self.entry("d-0", "success", 1, output_kind="local", output_ref="mockups/d-0.png")
        second = self.entry("d-1", "success", 1, output_kind="local", output_ref="mockups/d-1.png")
        write_png(self.run / "mockups" / "d-0.png", 390, 844)
        write_png(self.run / "mockups" / "d-1.png", 390, 844)
        self.write_json("mockup-manifest.json", self.ledger([first, second]))
        self.assertEqual(validator.validate_phase(self.run, "mockups"), [])

        cases = (
            ({**second, "viewport": "1280x800"}, "share exactly one viewport"),
            ({**second, "viewport": "391x844"}, "locked target_viewports"),
            ({**second, "prompt_digest": "sha256:" + "0" * 64}, "match exact prompt bytes"),
            ({**second, "prompt_ref": "prompts/missing.txt"}, "existing file"),
            ({**second, "output_ref": "mockups/missing.png"}, "existing file"),
            ({**second, "output_ref": "../escape.png"}, "safe run-relative"),
        )
        for changed, expected in cases:
            with self.subTest(expected=expected):
                self.write_json("mockup-manifest.json", self.ledger([first, changed]))
                errors = validator.validate_phase(self.run, "mockups")
                self.assertTrue(any(expected in error for error in errors), errors)

        write_png(self.run / "mockups" / "wrong.png", 1280, 800)
        self.write_json(
            "mockup-manifest.json",
            self.ledger([first, {**second, "output_ref": "mockups/wrong.png"}]),
        )
        self.assertTrue(
            any("dimensions" in error for error in validator.validate_phase(self.run, "mockups"))
        )

    def test_provider_output_contract_is_strict_and_host_owned(self):
        self.advance_to_approved()
        valid = self.entry(
            status="success",
            attempts=1,
            output_kind="provider",
            output_ref="provider:openai:artifact_abc-123",
        )
        self.write_json("mockup-manifest.json", self.ledger([valid]))
        self.assertEqual(validator.validate_phase(self.run, "mockups"), [])
        for output_ref in (
            "openai:artifact_abc-123",
            "provider:OpenAI:artifact",
            "provider:openai:https://example.com/a",
            "provider:openai:user@artifact",
            "provider:openai:sk-proj-abcdefghijklmnopqrstuvwxyz",
        ):
            with self.subTest(output_ref=output_ref):
                self.write_json(
                    "mockup-manifest.json", self.ledger([{**valid, "output_ref": output_ref}])
                )
                self.assertTrue(validator.validate_phase(self.run, "mockups"))

    def test_authorization_reserves_attempt_before_provider_and_denies_exhaustion(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        provider_calls = []

        self.assertTrue(run_state.image_generation_allowed(self.run, "d-0"))
        run_state.authorize_generation(self.run, "d-0", now="2026-07-20T00:01:00Z")
        provider_calls.append("first")
        manifest = json.loads((self.run / "mockup-manifest.json").read_text())
        manifest["mockups"][0]["status"] = "failed"
        manifest["mockups"][0]["failure"] = "transient"
        self.write_json("mockup-manifest.json", manifest)

        self.assertTrue(run_state.image_generation_allowed(self.run, "d-0"))
        run_state.authorize_generation(self.run, "d-0", now="2026-07-20T00:02:00Z")
        provider_calls.append("retry")
        manifest = json.loads((self.run / "mockup-manifest.json").read_text())
        manifest["mockups"][0]["status"] = "failed"
        self.write_json("mockup-manifest.json", manifest)

        self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))
        before_run = (self.run / "run.json").read_bytes()
        before_mockups = (self.run / "mockup-manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "attempt"):
            run_state.authorize_generation(self.run, "d-0")
        self.assertEqual(provider_calls, ["first", "retry"])
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_mockups)

    def test_authorization_denies_success_unknown_bad_accounting_and_held_lock(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        self.assertFalse(run_state.image_generation_allowed(self.run, "unknown"))
        owner = run_state._acquire_generation_lock(
            self.run, "2026-07-20T00:00:00Z"
        )
        before_run = (self.run / "run.json").read_bytes()
        before_mockups = (self.run / "mockup-manifest.json").read_bytes()
        try:
            with self.assertRaisesRegex(ValueError, "already in progress"):
                run_state.authorize_generation(self.run, "d-0")
            self.assertEqual((self.run / "run.json").read_bytes(), before_run)
            self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_mockups)
        finally:
            run_state._release_generation_lock(owner)

        bad = self.ledger([self.entry()], used=1)
        bad["mockups"][0]["attempt_count"] = 0
        self.write_json("mockup-manifest.json", bad)
        self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))

        entry = self.entry(
            status="success",
            attempts=1,
            output_kind="provider",
            output_ref="provider:openai:artifact-1",
        )
        self.write_json("mockup-manifest.json", self.ledger([entry]))
        self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))

    def test_generation_audit_fields_and_ceiling_are_manifest_invariants(self):
        self.advance_to_approved()
        pending = self.entry()
        base = self.ledger([pending])
        cases = (
            ({**base, "schema_version": 2}, "schema_version must be 1"),
            ({**base, "generation_attempts_used": 1}, "audit fields"),
            (
                {
                    **base,
                    "last_generation_authorized_at": "2026-07-20",
                    "last_generation_direction_id": "d-0",
                },
                "generation use and audit fields",
            ),
            (
                {
                    **base,
                    "generation_attempts_used": 1,
                    "last_generation_authorized_at": "not-a-timestamp",
                    "last_generation_direction_id": "d-0",
                },
                "must be RFC3339",
            ),
            (
                {
                    **base,
                    "generation_attempts_used": 1,
                    "last_generation_authorized_at": "2026-07-20T00:01:00Z",
                    "last_generation_direction_id": "d-0",
                },
                "attempt_count total",
            ),
            (
                {
                    **base,
                    "generation_attempts_used": 1,
                    "last_generation_authorized_at": "2026-07-20T00:01:00Z",
                    "last_generation_direction_id": "unknown",
                },
                "approved direction",
            ),
            (
                {
                    **base,
                    "generation_attempts_used": 3,
                    "last_generation_authorized_at": "2026-07-20T00:01:00Z",
                    "last_generation_direction_id": "d-0",
                },
                "authorization ceiling",
            ),
        )
        for changed, expected in cases:
            with self.subTest(expected=expected):
                self.write_json("mockup-manifest.json", changed)
                errors = validator.validate_mockup_manifest_for_generation(self.run)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_single_manifest_reservation_keeps_run_bytes_unchanged(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        reserved = run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual(reserved["generation_attempts_used"], 1)
        self.assertEqual(
            json.loads((self.run / "mockup-manifest.json").read_text())[
                "generation_attempts_used"
            ],
            1,
        )
        self.assert_generation_lock_released()
        self.assertEqual(list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), [])

    def test_generation_failure_injection_preserves_valid_single_ledger(self):
        cases = (
            ("_write_all", OSError("stage failed"), False),
            ("_write_all", KeyboardInterrupt(), False),
            ("_fsync_generation_file", OSError("fsync failed"), False),
            ("_replace_path", OSError("replace failed"), False),
        )
        for helper, failure, expect_new in cases:
            with self.subTest(helper=helper, failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as temp:
                    self.root = Path(temp)
                    self.run = run_state.init_run(
                        self.root,
                        "failure",
                        run_id=f"failure-{helper.strip('_').replace('_', '-')}-{type(failure).__name__.lower()}",
                        target_viewports=["390x844"],
                        required_content=["Order summary"],
                        required_interactions=["Edit order"],
                    )
                    self.advance_to_approved()
                    self.write_pending_manifest()
                    before_run = (self.run / "run.json").read_bytes()
                    before_ledger = (self.run / "mockup-manifest.json").read_bytes()
                    with mock.patch.object(run_state, helper, side_effect=failure):
                        with self.assertRaises(type(failure)):
                            run_state.authorize_generation(self.run, "d-0")
                    self.assertEqual((self.run / "run.json").read_bytes(), before_run)
                    self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_ledger)
                    self.assert_generation_lock_released()
                    self.assertEqual(
                        list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), []
                    )

    def test_partial_staging_interrupt_removes_transaction_temp(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        before_ledger = (self.run / "mockup-manifest.json").read_bytes()

        real_write = os.write

        def write_part_then_interrupt(descriptor, data):
            real_write(descriptor, data[:17])
            raise KeyboardInterrupt()

        with mock.patch.object(
            run_state, "_write_all", side_effect=write_part_then_interrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_ledger)
        self.assert_generation_lock_released()
        self.assertEqual(list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), [])

    def test_generation_temp_completes_real_short_writes_and_rejects_corruption(self):
        staged_value = {"schema_version": 1, "mockups": []}
        expected_data = (json.dumps(staged_value, indent=2) + "\n").encode()
        real_write = os.write
        writes = []

        def short_write(descriptor, data):
            writes.append(len(data))
            return real_write(descriptor, data[:3])

        owner = run_state._acquire_generation_lock(
            self.run, "2026-07-20T00:00:00Z"
        )
        try:
            with mock.patch.object(run_state.os, "write", side_effect=short_write):
                stage = run_state._stage_generation_manifest(
                    owner, staged_value, "a" * 32
                )
            self.assertEqual(run_state._read_all(stage.descriptor), expected_data)
            self.assertGreater(len(writes), 1)
            run_state._close_staged_generation_manifest(owner, stage)

            for index, invalid_result in enumerate((None, 0, -1), start=1):
                with self.subTest(invalid_write_result=invalid_result):
                    transaction_id = f"{index:032x}"
                    with mock.patch.object(
                        run_state.os, "write", return_value=invalid_result
                    ), mock.patch.object(run_state, "_fsync_generation_file") as fsync:
                        with self.assertRaisesRegex(OSError, "made no progress"):
                            run_state._stage_generation_manifest(
                                owner, staged_value, transaction_id
                            )
                        fsync.assert_not_called()
                    self.assertFalse(
                        (
                            self.run
                            / f".mockup-manifest.json.generation-{transaction_id}.tmp"
                        ).exists()
                    )
        finally:
            run_state._release_generation_lock(owner)

        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        before_ledger = (self.run / "mockup-manifest.json").read_bytes()

        real_read_all = run_state._read_all
        read_count = 0

        def corrupt_staged_read(descriptor):
            nonlocal read_count
            read_count += 1
            if read_count == 2:
                return b"{corrupt"
            return real_read_all(descriptor)

        with mock.patch.object(
            run_state, "_read_all", side_effect=corrupt_staged_read
        ):
            with self.assertRaisesRegex(ValueError, "staged generation manifest"):
                run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_ledger)
        self.assertEqual(list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), [])

    def test_directory_lock_uses_stable_run_inode(self):
        inode = self.run.stat().st_ino
        owner = run_state._acquire_generation_lock(
            self.run, "2026-07-20T00:01:00Z"
        )
        self.assertEqual(owner.inode, inode)
        with self.assertRaisesRegex(ValueError, "already in progress"):
            run_state._acquire_generation_lock(
                self.run, "2026-07-20T00:02:00Z"
            )
        run_state._release_generation_lock(owner)
        next_owner = run_state._acquire_generation_lock(
            self.run, "2026-07-20T00:02:00Z"
        )
        self.assertEqual(next_owner.inode, inode)
        run_state._release_generation_lock(next_owner)
        self.assertFalse((self.run / ".generation.lock").exists())

    def test_run_directory_path_replacement_is_detected_without_mutation(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        before_ledger = (self.run / "mockup-manifest.json").read_bytes()
        moved = self.root / "held-run-directory"
        marker = self.run / "replacement-marker"
        real_preflight = run_state._generation_preflight

        def replace_path_after_preflight(run_dir, direction_id):
            result = real_preflight(run_dir, direction_id)
            self.run.rename(moved)
            self.run.mkdir()
            marker.write_text("untouched", encoding="utf-8")
            return result

        try:
            with mock.patch.object(
                run_state,
                "_generation_preflight",
                side_effect=replace_path_after_preflight,
            ):
                with self.assertRaisesRegex(
                    ValueError, "run directory path was replaced"
                ):
                    run_state.authorize_generation(self.run, "d-0")
            self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")
            self.assertEqual((moved / "run.json").read_bytes(), before_run)
            self.assertEqual(
                (moved / "mockup-manifest.json").read_bytes(), before_ledger
            )
        finally:
            if marker.exists():
                marker.unlink()
            if self.run.exists():
                self.run.rmdir()
            if moved.exists():
                moved.rename(self.run)

    def test_interrupt_after_replace_leaves_valid_new_ledger_and_no_residue(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        real_replace = run_state._replace_path

        def replace_then_interrupt(source, destination, **kwargs):
            real_replace(source, destination, **kwargs)
            raise KeyboardInterrupt()

        with mock.patch.object(run_state, "_replace_path", side_effect=replace_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        ledger = json.loads((self.run / "mockup-manifest.json").read_text())
        self.assertEqual(validator.validate_mockup_manifest_for_generation(self.run), [])
        self.assertEqual(ledger["generation_attempts_used"], 1)
        self.assert_generation_lock_released()
        self.assertEqual(list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), [])

    def test_legacy_lock_metadata_is_ignored_and_never_rewritten(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        lock = self.run / ".generation.lock"
        transaction_id = "b" * 32
        stale_temp = self.run / f".mockup-manifest.json.generation-{transaction_id}.tmp"
        stale_temp.write_text("partial", encoding="utf-8")
        lock.write_text(
            json.dumps(
                {
                    "pid": 99_999_999,
                    "created_at": "2026-07-20T00:00:00Z",
                    "transaction_id": transaction_id,
                }
            ),
            encoding="utf-8",
        )
        diagnostic = lock.read_bytes()
        inode = lock.stat().st_ino
        run_state.authorize_generation(self.run, "d-0")
        self.assertFalse(stale_temp.exists())
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(lock.read_bytes(), diagnostic)
        self.assert_generation_lock_released()

        ledger = json.loads((self.run / "mockup-manifest.json").read_text())
        ledger["mockups"][0]["status"] = "failed"
        self.write_json("mockup-manifest.json", ledger)
        lock.write_text("{malformed", encoding="utf-8")
        malformed = lock.read_bytes()
        orphan = self.run / f".mockup-manifest.json.generation-{'c' * 32}.tmp"
        orphan.write_text("orphan", encoding="utf-8")
        run_state.authorize_generation(self.run, "d-0")
        self.assertFalse(orphan.exists())
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(lock.read_bytes(), malformed)
        self.assert_generation_lock_released()

    def test_cross_process_flock_blocks_and_sigkill_releases_owner(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        child_code = """
import fcntl, os, sys, time
path = sys.argv[1]
fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
fcntl.flock(fd, fcntl.LOCK_EX)
print("locked", flush=True)
time.sleep(60)
"""
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.run)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            before_run = (self.run / "run.json").read_bytes()
            before_ledger = (self.run / "mockup-manifest.json").read_bytes()
            self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))
            with self.assertRaisesRegex(ValueError, "already in progress"):
                run_state.authorize_generation(self.run, "d-0")
            self.assertEqual((self.run / "run.json").read_bytes(), before_run)
            self.assertEqual(
                (self.run / "mockup-manifest.json").read_bytes(), before_ledger
            )
            inode = self.run.stat().st_ino
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        run_state.authorize_generation(self.run, "d-0")
        self.assertEqual(self.run.stat().st_ino, inode)
        self.assert_generation_lock_released()

    def test_recreated_legacy_lock_path_cannot_admit_second_process(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        owner = run_state._acquire_generation_lock(
            self.run, "2026-07-20T00:00:00Z"
        )
        legacy_lock = self.run / ".generation.lock"
        legacy_lock.unlink(missing_ok=True)
        legacy_lock.write_text("attacker replacement", encoding="utf-8")
        alias = self.root / "publication-run-alias"
        alias.symlink_to(self.run, target_is_directory=True)
        before_run = (self.run / "run.json").read_bytes()
        before_ledger = (self.run / "mockup-manifest.json").read_bytes()
        try:
            contender = subprocess.run(
                [
                    sys.executable,
                    run_state.__file__,
                    "authorize-generation",
                    "--run",
                    str(alias),
                    "--direction",
                    "d-0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(contender.returncode, 0, contender.stdout)
            self.assertIn("already in progress", contender.stderr)
            self.assertEqual((self.run / "run.json").read_bytes(), before_run)
            self.assertEqual(
                (self.run / "mockup-manifest.json").read_bytes(), before_ledger
            )
        finally:
            run_state._release_generation_lock(owner)
        run_state.authorize_generation(self.run, "d-0")
        ledger = json.loads((self.run / "mockup-manifest.json").read_text())
        self.assertEqual(ledger["generation_attempts_used"], 1)
        self.assertEqual(ledger["mockups"][0]["attempt_count"], 1)

    def test_temp_swap_at_replace_boundary_rolls_back_old_valid_ledger(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        before_ledger = (self.run / "mockup-manifest.json").read_bytes()
        real_replace = run_state._replace_path
        attack_pending = True

        def corrupt_then_replace(source, destination, **kwargs):
            nonlocal attack_pending
            if attack_pending:
                attack_pending = False
                if kwargs.get("src_dir_fd") is None:
                    Path(source).write_bytes(b"{corrupt")
                else:
                    os.unlink(source, dir_fd=kwargs["src_dir_fd"])
                    descriptor = os.open(
                        source,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                        dir_fd=kwargs["src_dir_fd"],
                    )
                    try:
                        os.write(descriptor, b"{corrupt")
                    finally:
                        os.close(descriptor)
            return real_replace(source, destination, **kwargs)

        with mock.patch.object(
            run_state, "_replace_path", side_effect=corrupt_then_replace
        ):
            with self.assertRaisesRegex(ValueError, "published generation manifest"):
                run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_ledger)
        self.assertEqual(validator.validate_mockup_manifest_for_generation(self.run), [])
        self.assert_generation_lock_released()

    def test_legacy_lock_hardlink_never_modifies_victim(self):
        victim = self.root / "lock-victim.txt"
        victim.write_bytes(b"do not modify this inode")
        legacy_lock = self.run / ".generation.lock"
        os.link(victim, legacy_lock)
        before = victim.read_bytes()
        owner = run_state._acquire_generation_lock(
            self.run, "2026-07-20T00:00:00Z"
        )
        try:
            self.assertEqual(victim.read_bytes(), before)
            self.assertEqual(legacy_lock.read_bytes(), before)
        finally:
            run_state._release_generation_lock(owner)

    def test_cli_requires_direction_and_authorizes_concisely(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        missing = subprocess.run(
            [sys.executable, run_state.__file__, "can-generate", "--run", str(self.run)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(missing.returncode, 0)
        missing_authorization = subprocess.run(
            [
                sys.executable,
                run_state.__file__,
                "authorize-generation",
                "--run",
                str(self.run),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(missing_authorization.returncode, 1)
        self.assertNotIn("Traceback", missing_authorization.stderr)
        allowed = subprocess.run(
            [
                sys.executable,
                run_state.__file__,
                "can-generate",
                "--run",
                str(self.run),
                "--direction",
                "d-0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual((allowed.returncode, allowed.stdout.strip()), (0, "true"))
        authorized = subprocess.run(
            [
                sys.executable,
                run_state.__file__,
                "authorize-generation",
                "--run",
                str(self.run),
                "--direction",
                "d-0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stderr)
        self.assertNotIn("Traceback", authorized.stderr)

    def test_later_states_fail_closed_when_upstream_evidence_is_tampered(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before = (self.run / "run.json").read_bytes()
        evidence_items = json.loads((self.run / "evidence.json").read_text())
        evidence_items[0]["source_url"] = "not-a-url"
        evidence_items[0]["publisher_or_author"] = " "
        self.write_json("evidence.json", evidence_items)
        with self.assertRaisesRegex(ValueError, "research validation failed"):
            run_state.load_run(self.run)
        self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))
        with self.assertRaisesRegex(ValueError, "research validation failed"):
            run_state.authorize_generation(self.run, "d-0")
        self.assert_generation_lock_released()
        self.assertEqual((self.run / "run.json").read_bytes(), before)

    def test_legacy_dual_accounting_run_is_rejected_before_authorization(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        run_path = self.run / "run.json"
        current = json.loads(run_path.read_text(encoding="utf-8"))
        legacy_cases = (
            {
                "generation_attempts_used": 0,
                "last_generation_authorized_at": None,
                "last_generation_authorized_direction_id": None,
            },
            {"last_generation_direction_id": None},
        )
        for legacy_fields in legacy_cases:
            with self.subTest(legacy_fields=tuple(legacy_fields)):
                run_path.write_text(
                    json.dumps({**current, **legacy_fields}), encoding="utf-8"
                )
                before = run_path.read_bytes()
                self.assertTrue(
                    any(
                        "legacy generation accounting" in error
                        for error in validator.validate_mockup_manifest_for_generation(
                            self.run
                        )
                    )
                )
                with self.assertRaisesRegex(
                    ValueError, "legacy generation accounting.*migrate"
                ):
                    run_state.load_run(self.run)
                with self.assertRaisesRegex(
                    ValueError, "legacy generation accounting.*migrate"
                ):
                    run_state.authorize_generation(self.run, "d-0")
                self.assertEqual(run_path.read_bytes(), before)

    def test_cumulative_load_rejects_reference_and_markdown_tampering(self):
        for mutation in ("references", "design-markdown", "board-markdown"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temp:
                    self.root = Path(temp)
                    self.run = run_state.init_run(
                        self.root,
                        "tamper",
                        run_id=f"tamper-{mutation}",
                        target_viewports=["390x844"],
                        required_content=["Order summary"],
                        required_interactions=["Edit order"],
                    )
                    self.advance_to_approved()
                    self.write_pending_manifest()
                    if mutation == "references":
                        self.write_json("references.json", [])
                    elif mutation == "design-markdown":
                        (self.run / "design-evidence.md").unlink()
                    else:
                        (self.run / "reference-board.md").write_text(" ", encoding="utf-8")
                    before = (self.run / "run.json").read_bytes()
                    with self.assertRaisesRegex(ValueError, "research validation failed"):
                        run_state.load_run(self.run)
                    self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))
                    self.assertEqual((self.run / "run.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
