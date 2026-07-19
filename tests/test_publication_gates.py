import hashlib
import json
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
            {"mockups": [self.entry(identifier) for identifier in approved]},
        )

    def test_mockups_enforce_comparable_viewport_prompt_and_typed_outputs(self):
        self.advance_to_approved(("d-0", "d-1"))
        first = self.entry("d-0", "success", 1, output_kind="local", output_ref="mockups/d-0.png")
        second = self.entry("d-1", "success", 1, output_kind="local", output_ref="mockups/d-1.png")
        write_png(self.run / "mockups" / "d-0.png", 390, 844)
        write_png(self.run / "mockups" / "d-1.png", 390, 844)
        manifest = run_state.load_run(self.run)
        manifest["generation_attempts_used"] = 2
        self.write_json("run.json", manifest)
        self.write_json("mockup-manifest.json", {"mockups": [first, second]})
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
                self.write_json("mockup-manifest.json", {"mockups": [first, changed]})
                errors = validator.validate_phase(self.run, "mockups")
                self.assertTrue(any(expected in error for error in errors), errors)

        write_png(self.run / "mockups" / "wrong.png", 1280, 800)
        self.write_json(
            "mockup-manifest.json",
            {"mockups": [first, {**second, "output_ref": "mockups/wrong.png"}]},
        )
        self.assertTrue(
            any("dimensions" in error for error in validator.validate_phase(self.run, "mockups"))
        )

    def test_provider_output_contract_is_strict_and_host_owned(self):
        self.advance_to_approved()
        manifest = run_state.load_run(self.run)
        manifest["generation_attempts_used"] = 1
        self.write_json("run.json", manifest)
        valid = self.entry(
            status="success",
            attempts=1,
            output_kind="provider",
            output_ref="provider:openai:artifact_abc-123",
        )
        self.write_json("mockup-manifest.json", {"mockups": [valid]})
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
                    "mockup-manifest.json", {"mockups": [{**valid, "output_ref": output_ref}]}
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
        lock.write_text("held", encoding="utf-8")
        before_run = (self.run / "run.json").read_bytes()
        before_mockups = (self.run / "mockup-manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already in progress"):
            run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_mockups)
        lock.unlink()

        manifest = json.loads((self.run / "run.json").read_text())
        manifest["generation_attempts_used"] = 1
        self.write_json("run.json", manifest)
        self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))

        manifest["generation_attempts_used"] = 0
        self.write_json("run.json", manifest)
        entry = self.entry(
            status="success",
            attempts=1,
            output_kind="provider",
            output_ref="provider:openai:artifact-1",
        )
        manifest["generation_attempts_used"] = 1
        manifest["last_generation_authorized_at"] = "2026-07-20T00:01:00Z"
        manifest["last_generation_authorized_direction_id"] = "d-0"
        self.write_json("run.json", manifest)
        self.write_json("mockup-manifest.json", {"mockups": [entry]})
        self.assertFalse(run_state.image_generation_allowed(self.run, "d-0"))

    def test_generation_audit_fields_and_ceiling_are_manifest_invariants(self):
        self.advance_to_approved()
        base = json.loads((self.run / "run.json").read_text())
        cases = (
            ({**base, "generation_attempts_used": 1}, "audit fields"),
            (
                {
                    **base,
                    "generation_attempts_used": 1,
                    "last_generation_authorized_at": "2026-07-20T00:01:00Z",
                    "last_generation_authorized_direction_id": "unknown",
                },
                "approved direction",
            ),
            (
                {
                    **base,
                    "generation_attempts_used": 3,
                    "last_generation_authorized_at": "2026-07-20T00:01:00Z",
                    "last_generation_authorized_direction_id": "d-0",
                },
                "authorization ceiling",
            ),
        )
        for changed, expected in cases:
            with self.subTest(expected=expected):
                self.write_json("run.json", changed)
                with self.assertRaisesRegex(ValueError, expected):
                    run_state.load_run(self.run)

    def test_two_file_reservation_rolls_back_if_second_replace_fails(self):
        self.advance_to_approved()
        self.write_pending_manifest()
        before_run = (self.run / "run.json").read_bytes()
        before_mockups = (self.run / "mockup-manifest.json").read_bytes()
        real_replace = run_state._replace_path
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second replace failure")
            return real_replace(source, destination)

        with mock.patch.object(run_state, "_replace_path", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "injected"):
                run_state.authorize_generation(self.run, "d-0")
        self.assertEqual((self.run / "run.json").read_bytes(), before_run)
        self.assertEqual((self.run / "mockup-manifest.json").read_bytes(), before_mockups)
        self.assertFalse((self.run / ".generation.lock").exists())
        self.assertEqual(list(self.run.glob("*.tmp-*")), [])
        self.assertEqual(list(self.run.glob("*.rollback-*")), [])

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
