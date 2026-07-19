import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
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
        lock = self.run / ".generation.lock"
        lock.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "created_at": "2026-07-20T00:00:00Z",
                    "transaction_id": "a" * 32,
                }
            ),
            encoding="utf-8",
        )
        before_run = (self.run / "run.json").read_bytes()
        before_mockups = (self.run / "mockup-manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already in progress"):
            run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_mockups)
        lock.unlink()

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
        self.assertFalse((self.run / ".generation.lock").exists())
        self.assertEqual(list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), [])

    def test_generation_failure_injection_preserves_valid_single_ledger(self):
        cases = (
            ("_write_generation_temp", OSError("stage failed"), False),
            ("_write_generation_temp", KeyboardInterrupt(), False),
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
                    self.assertFalse((self.run / ".generation.lock").exists())
                    self.assertEqual(
                        list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), []
                    )

    def test_partial_staging_interrupt_removes_transaction_temp(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        before_ledger = (self.run / "mockup-manifest.json").read_bytes()

        def write_part_then_interrupt(path, data):
            path.write_bytes(data[:17])
            raise KeyboardInterrupt()

        with mock.patch.object(
            run_state, "_write_generation_temp", side_effect=write_part_then_interrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_ledger)
        self.assertFalse((self.run / ".generation.lock").exists())
        self.assertEqual(list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), [])

    def test_lock_write_retries_short_writes_and_release_preserves_replacement(self):
        real_write = os.write

        def short_write(descriptor, data):
            return real_write(descriptor, data[:3])

        with mock.patch.object(run_state.os, "write", side_effect=short_write):
            transaction_id = run_state._acquire_generation_lock(
                self.run, "2026-07-20T00:01:00Z"
            )
        lock = self.run / ".generation.lock"
        payload = json.loads(lock.read_text(encoding="utf-8"))
        self.assertEqual(payload["transaction_id"], transaction_id)

        replacement = {
            "pid": os.getpid(),
            "created_at": "2026-07-20T00:02:00Z",
            "transaction_id": "f" * 32,
        }
        real_loads = json.loads

        def replace_after_parse(value):
            parsed = real_loads(value)
            lock.unlink()
            lock.write_text(json.dumps(replacement), encoding="utf-8")
            return parsed

        with mock.patch.object(run_state.json, "loads", side_effect=replace_after_parse):
            run_state._release_generation_lock(self.run, transaction_id)
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8")), replacement)

    def test_interrupt_after_replace_leaves_valid_new_ledger_and_no_residue(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        real_replace = run_state._replace_path

        def replace_then_interrupt(source, destination):
            real_replace(source, destination)
            raise KeyboardInterrupt()

        with mock.patch.object(run_state, "_replace_path", side_effect=replace_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        ledger = json.loads((self.run / "mockup-manifest.json").read_text())
        self.assertEqual(validator.validate_mockup_manifest_for_generation(self.run), [])
        self.assertEqual(ledger["generation_attempts_used"], 1)
        self.assertFalse((self.run / ".generation.lock").exists())
        self.assertEqual(list(self.run.glob(".mockup-manifest.json.generation-*.tmp")), [])

    def test_dead_and_old_malformed_locks_recover_matching_temp_files(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        lock = self.run / ".generation.lock"
        transaction_id = "b" * 32
        stale_temp = self.run / f".mockup-manifest.json.generation-{transaction_id}.tmp"
        foreign_temp = self.run / f".mockup-manifest.json.generation-{'c' * 32}.tmp"
        stale_temp.write_text("partial", encoding="utf-8")
        foreign_temp.write_text("foreign", encoding="utf-8")
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
        run_state.authorize_generation(self.run, "d-0")
        self.assertFalse(lock.exists())
        self.assertFalse(stale_temp.exists())
        self.assertEqual(foreign_temp.read_text(encoding="utf-8"), "foreign")

        ledger = json.loads((self.run / "mockup-manifest.json").read_text())
        ledger["mockups"][0]["status"] = "failed"
        self.write_json("mockup-manifest.json", ledger)
        lock.write_text("{malformed", encoding="utf-8")
        recent_mtime = time.time()
        os.utime(lock, (recent_mtime, recent_mtime))
        with self.assertRaisesRegex(ValueError, "malformed.*recent"):
            run_state.authorize_generation(self.run, "d-0")
        old_mtime = time.time() - run_state.MALFORMED_LOCK_STALE_SECONDS - 1
        os.utime(lock, (old_mtime, old_mtime))
        run_state.authorize_generation(self.run, "d-0")
        self.assertFalse(lock.exists())
        self.assertEqual(foreign_temp.read_text(encoding="utf-8"), "foreign")

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
        self.assertFalse((self.run / ".generation.lock").exists())
        self.assertEqual((self.run / "run.json").read_bytes(), before)

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
