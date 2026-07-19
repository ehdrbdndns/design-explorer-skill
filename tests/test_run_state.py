import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from design_explorer_import import load_script_module


run_state = load_script_module("run_state", "design-explorer/scripts/run_state.py")


AXES = ("layout", "typography", "palette", "density", "imagery", "interaction")


def reference():
    return {
        "id": "ref-1",
        "title": "Example checkout",
        "source_url": "https://example.com/checkout",
        "source_type": "product",
        "captured_at": "2026-07-19T12:00:00Z",
        "relevance": "Comparable checkout hierarchy.",
        "observations": {axis: "observed" for axis in AXES},
    }


def evidence():
    return {
        "id": "ev-1",
        "problem": "reduce uncertainty",
        "title": "Checkout guidance",
        "publisher_or_author": "W3C",
        "source_url": "https://www.w3.org/WAI/",
        "source_type": "official",
        "summary": "Make status perceivable.",
        "application": "Keep errors adjacent.",
        "limitations": "Confirm for the target platform.",
    }


def direction(identifier, index):
    return {
        "id": identifier,
        "kind": "primary",
        "name": identifier.title(),
        "concept": "Distinct direction",
        "ux_problem": "reduce uncertainty",
        "evidence_ids": ["ev-1"],
        "evidence_application": "Uses clear hierarchy.",
        "baseline_exceptions": [],
        "axes": {
            "layout": f"layout-{index}",
            "typography": f"type-{index}",
            "palette": f"palette-{index}",
            "density": "comfortable",
            "imagery": "none",
            "interaction": "progressive",
        },
        "tradeoffs": "Balances speed and reassurance.",
        "implementation_difficulty": "medium",
        "implementation_risks": "Responsive hierarchy.",
    }


def implementation(identifier="d-0"):
    return {
        "selected_direction_id": identifier,
        "mode": "project",
        "preview_path": "src/previews/Checkout.tsx",
        "verification": {
            "rendered_viewports": ["390x844"],
            "checks": {
                "content": "pass",
                "overflow": "pass",
                "accessibility": "pass",
            },
        },
    }


class RunStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_dir = run_state.init_run(
            self.root, "checkout", now="2026-07-19T12:00:00Z", run_id="run-checkout"
        )

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value="ok"):
        path = self.run_dir / name
        if isinstance(value, (dict, list)):
            path.write_text(json.dumps(value), encoding="utf-8")
        else:
            path.write_text(value, encoding="utf-8")

    def set_state(self, state):
        manifest = run_state.load_run(self.run_dir)
        manifest["state"] = state
        self.write("run.json", manifest)

    def write_brief(self):
        self.write("brief.md", "# Design Brief\n\nCheckout screen")

    def write_research(self):
        self.write("references.json", [reference()])
        self.write("evidence.json", [evidence()])
        self.write("design-evidence.md", "# Design evidence")
        self.write("reference-board.md", "# Reference board")

    def write_directions(self, count=5):
        values = [direction(f"d-{index}", index) for index in range(count)]
        self.write("directions.json", values)
        self.write("mood-directions.md", "# Mood directions")
        return values

    def write_mockups(self, identifiers, attempt_count=1):
        self.write(
            "mockup-manifest.json",
            {
                "mockups": [
                    {
                        "direction_id": identifier,
                        "status": "success",
                        "viewport": "390x844",
                        "prompt_digest": f"sha256:{identifier}",
                        "output_ref": f"mockups/{identifier}.png",
                        "attempt_count": attempt_count,
                    }
                    for identifier in identifiers
                ]
            },
        )

    def advance_to_pending(self, direction_count=5):
        self.write_brief()
        run_state.transition_run(self.run_dir, "brief_ready")
        self.write_research()
        run_state.transition_run(self.run_dir, "research_complete")
        self.write_directions(direction_count)
        run_state.transition_run(self.run_dir, "directions_pending_approval")

    def advance_to_mockups(self, approved=None, **transition_options):
        approved = approved or ["d-0"]
        self.advance_to_pending(max(5, len(approved)))
        run_state.transition_run(
            self.run_dir,
            "directions_approved",
            approved_direction_ids=approved,
            **transition_options,
        )
        self.write_mockups(approved)
        run_state.transition_run(self.run_dir, "mockups_generated")

    def test_initial_manifest_is_resumable(self):
        manifest = run_state.load_run(self.run_dir)
        self.assertEqual(manifest["state"], "initialized")
        self.assertEqual(manifest["run_id"], "run-checkout")
        self.assertEqual(manifest["approved_direction_ids"], [])
        self.assertEqual(manifest["revision_count"], 0)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["generation_budget"], 5)
        self.assertEqual(manifest["max_attempts_per_direction"], 2)

    def test_public_interfaces_expose_promised_annotations(self):
        init_signature = inspect.signature(run_state.init_run)
        self.assertEqual(init_signature.parameters["project_path"].annotation, str | None)
        self.assertEqual(init_signature.parameters["now"].annotation, str | None)
        self.assertEqual(init_signature.parameters["run_id"].annotation, str | None)
        self.assertEqual(init_signature.return_annotation, Path)

        load_signature = inspect.signature(run_state.load_run)
        self.assertEqual(load_signature.parameters["run_dir"].annotation, Path)
        self.assertEqual(load_signature.return_annotation, dict)

        transition_signature = inspect.signature(run_state.transition_run)
        self.assertEqual(
            transition_signature.parameters["approved_direction_ids"].annotation,
            list[str] | None,
        )
        self.assertEqual(
            transition_signature.parameters["selected_direction_id"].annotation,
            str | None,
        )
        self.assertEqual(transition_signature.parameters["now"].annotation, str | None)
        self.assertEqual(
            transition_signature.parameters["integration_approved"].annotation,
            bool,
        )
        self.assertIs(
            transition_signature.parameters["integration_approved"].default,
            False,
        )
        self.assertEqual(
            transition_signature.parameters["generation_budget"].annotation,
            int | None,
        )
        self.assertEqual(
            transition_signature.parameters["max_attempts_per_direction"].annotation,
            int | None,
        )
        self.assertEqual(
            transition_signature.parameters["budget_expansion_approved"].annotation,
            bool,
        )
        self.assertEqual(transition_signature.return_annotation, dict)

        revise_signature = inspect.signature(run_state.revise_run)
        self.assertEqual(revise_signature.parameters["run_dir"].annotation, Path)
        self.assertEqual(revise_signature.parameters["reason"].annotation, str)
        self.assertEqual(revise_signature.parameters["now"].annotation, str | None)
        self.assertEqual(revise_signature.return_annotation, dict)

    def test_init_rejects_absolute_run_id(self):
        absolute_run_id = str(self.root / "absolute-run")
        with self.assertRaisesRegex(ValueError, "safe path component"):
            run_state.init_run(self.root, "checkout", run_id=absolute_run_id)

    def test_init_rejects_traversal_run_id(self):
        runs_root = self.root / "runs"
        runs_root.mkdir()
        with self.assertRaisesRegex(ValueError, "safe path component"):
            run_state.init_run(runs_root, "checkout", run_id="../escaped")

    def test_transition_requires_the_next_state_and_artifacts(self):
        with self.assertRaisesRegex(ValueError, "requires brief.md"):
            run_state.transition_run(self.run_dir, "brief_ready")
        self.write("brief.md", "# Design Brief")
        manifest = run_state.transition_run(
            self.run_dir, "brief_ready", now="2026-07-19T12:01:00Z"
        )
        self.assertEqual(manifest["state"], "brief_ready")
        with self.assertRaisesRegex(ValueError, "illegal transition"):
            run_state.transition_run(self.run_dir, "directions_approved")

    def test_approval_cannot_be_inferred_or_reference_unknown_direction(self):
        self.advance_to_pending()
        with self.assertRaisesRegex(ValueError, "explicit approved_direction_ids"):
            run_state.transition_run(self.run_dir, "directions_approved")
        with self.assertRaisesRegex(ValueError, "unknown direction"):
            run_state.transition_run(
                self.run_dir,
                "directions_approved",
                approved_direction_ids=["unknown"],
            )

    def test_mockups_and_selection_are_limited_to_approved_directions(self):
        self.advance_to_mockups(["d-0"])
        with self.assertRaisesRegex(ValueError, "approved direction"):
            run_state.transition_run(
                self.run_dir, "implementation_selected", selected_direction_id="d-1"
            )
        manifest = run_state.transition_run(
            self.run_dir, "implementation_selected", selected_direction_id="d-0"
        )
        self.assertEqual(manifest["selected_direction_id"], "d-0")

    def test_integration_requires_explicit_approval_and_records_it(self):
        self.set_state("prototype_ready")
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["d-0"]
        manifest["selected_direction_id"] = "d-0"
        self.write("run.json", manifest)
        self.write("implementation.json", implementation())
        with self.assertRaisesRegex(ValueError, "explicit integration approval"):
            run_state.transition_run(self.run_dir, "integrated")

        manifest = run_state.transition_run(
            self.run_dir,
            "integrated",
            integration_approved=True,
            now="2026-07-19T12:02:00Z",
        )
        self.assertEqual(manifest["state"], "integrated")
        self.assertEqual(manifest["integration_approved_at"], "2026-07-19T12:02:00Z")

    def test_revision_archives_mockups_and_returns_to_pending_approval(self):
        self.set_state("mockups_generated")
        self.write_mockups(["a"])
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["a"]
        manifest["selected_direction_id"] = "a"
        manifest["generation_budget"] = 8
        manifest["max_attempts_per_direction"] = 3
        manifest["budget_expansion_approved_at"] = "2026-07-19T12:02:00Z"
        self.write("run.json", manifest)

        revised = run_state.revise_run(
            self.run_dir,
            "Combine the calm hierarchy with the dense summary.",
            now="2026-07-19T12:03:00Z",
        )

        self.assertEqual(revised["state"], "directions_pending_approval")
        self.assertEqual(revised["revision_count"], 1)
        self.assertEqual(
            revised["last_revision_reason"],
            "Combine the calm hierarchy with the dense summary.",
        )
        self.assertEqual(revised["last_revision_at"], "2026-07-19T12:03:00Z")
        self.assertEqual(revised["approved_direction_ids"], [])
        self.assertIsNone(revised["selected_direction_id"])
        self.assertEqual(revised["generation_budget"], 5)
        self.assertEqual(revised["max_attempts_per_direction"], 2)
        self.assertNotIn("budget_expansion_approved_at", revised)
        self.assertFalse((self.run_dir / "mockup-manifest.json").exists())
        self.assertTrue(
            (self.run_dir / "mockup-manifest.revision-1.json").is_file()
        )
        self.assertEqual(
            json.loads(
                (self.run_dir / "mockup-manifest.revision-1.json").read_text()
            ),
            {
                "mockups": [
                    {
                        "direction_id": "a",
                        "status": "success",
                        "viewport": "390x844",
                        "prompt_digest": "sha256:a",
                        "output_ref": "mockups/a.png",
                        "attempt_count": 1,
                    }
                ]
            },
        )

    def test_revision_rejects_empty_reason_and_illegal_state(self):
        self.set_state("mockups_generated")
        self.write("mockup-manifest.json", {"mockups": []})
        with self.assertRaisesRegex(ValueError, "non-empty reason"):
            run_state.revise_run(self.run_dir, "   ")

        self.set_state("directions_approved")
        with self.assertRaisesRegex(ValueError, "illegal revision"):
            run_state.revise_run(self.run_dir, "Try a combined direction")

    def test_revision_preserves_current_manifest_on_archive_collision(self):
        self.set_state("mockups_generated")
        current = {"mockups": [{"direction_id": "current"}]}
        archived = {"mockups": [{"direction_id": "archived"}]}
        self.write("mockup-manifest.json", current)
        self.write("mockup-manifest.revision-1.json", archived)

        with self.assertRaisesRegex(ValueError, "archive already exists"):
            run_state.revise_run(self.run_dir, "Create a variation")

        self.assertEqual(
            json.loads((self.run_dir / "mockup-manifest.json").read_text()),
            current,
        )
        self.assertEqual(
            json.loads(
                (self.run_dir / "mockup-manifest.revision-1.json").read_text()
            ),
            archived,
        )
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "mockups_generated")

    def test_revision_rolls_back_archive_when_run_write_fails(self):
        self.set_state("mockups_generated")
        current = {"mockups": [{"direction_id": "current"}]}
        self.write("mockup-manifest.json", current)

        with mock.patch.object(
            run_state, "write_json_atomic", side_effect=OSError("disk full")
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                run_state.revise_run(self.run_dir, "Create a variation")

        self.assertEqual(
            json.loads((self.run_dir / "mockup-manifest.json").read_text()),
            current,
        )
        self.assertFalse(
            (self.run_dir / "mockup-manifest.revision-1.json").exists()
        )
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "mockups_generated")

    def test_load_rejects_unsupported_or_malformed_manifest_schema(self):
        manifest = json.loads((self.run_dir / "run.json").read_text())
        manifest["schema_version"] = 1
        self.write("run.json", manifest)
        with self.assertRaisesRegex(ValueError, "unsupported run schema.*migrate"):
            run_state.load_run(self.run_dir)

        manifest["schema_version"] = 2
        manifest["generation_budget"] = "5"
        self.write("run.json", manifest)
        with self.assertRaisesRegex(ValueError, "generation_budget"):
            run_state.load_run(self.run_dir)

        manifest.pop("state")
        manifest["generation_budget"] = 5
        self.write("run.json", manifest)
        with self.assertRaisesRegex(ValueError, "missing required key: state"):
            run_state.load_run(self.run_dir)

    def test_invalid_consuming_artifacts_cannot_bypass_transition_or_mutate_state(self):
        self.write("brief.md", "   ")
        with self.assertRaisesRegex(ValueError, "brief validation failed"):
            run_state.transition_run(self.run_dir, "brief_ready")
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "initialized")

        self.write_brief()
        run_state.transition_run(self.run_dir, "brief_ready")
        self.write("references.json", [])
        self.write("evidence.json", [])
        self.write("design-evidence.md", "# Evidence")
        self.write("reference-board.md", "# References")
        with self.assertRaisesRegex(ValueError, "research validation failed"):
            run_state.transition_run(self.run_dir, "research_complete")
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "brief_ready")

        self.write_research()
        run_state.transition_run(self.run_dir, "research_complete")
        self.write("directions.json", [{"id": "bypass"}])
        self.write("mood-directions.md", "# Mood")
        with self.assertRaisesRegex(ValueError, "directions validation failed"):
            run_state.transition_run(self.run_dir, "directions_pending_approval")
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "research_complete")

        self.write_directions()
        run_state.transition_run(self.run_dir, "directions_pending_approval")
        self.write("directions.json", [{"id": "changed-after-review"}])
        before = (self.run_dir / "run.json").read_text()
        with self.assertRaisesRegex(ValueError, "directions validation failed"):
            run_state.transition_run(
                self.run_dir,
                "directions_approved",
                approved_direction_ids=["changed-after-review"],
            )
        self.assertEqual((self.run_dir / "run.json").read_text(), before)

    def test_mockups_and_implementation_are_revalidated_at_every_consuming_gate(self):
        self.advance_to_pending()
        run_state.transition_run(
            self.run_dir, "directions_approved", approved_direction_ids=["d-0"]
        )
        self.write("mockup-manifest.json", {"mockups": []})
        with self.assertRaisesRegex(ValueError, "mockups validation failed"):
            run_state.transition_run(self.run_dir, "mockups_generated")
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "directions_approved")

        self.write_mockups(["d-0"])
        run_state.transition_run(self.run_dir, "mockups_generated")
        self.write("mockup-manifest.json", {"mockups": []})
        with self.assertRaisesRegex(ValueError, "mockups validation failed"):
            run_state.transition_run(
                self.run_dir, "implementation_selected", selected_direction_id="d-0"
            )
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "mockups_generated")

        self.write_mockups(["d-0"])
        run_state.transition_run(
            self.run_dir, "implementation_selected", selected_direction_id="d-0"
        )
        self.write("implementation.json", {})
        with self.assertRaisesRegex(ValueError, "implementation validation failed"):
            run_state.transition_run(self.run_dir, "prototype_ready")
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "implementation_selected")

        self.write("implementation.json", implementation())
        run_state.transition_run(self.run_dir, "prototype_ready")
        self.write("implementation.json", {})
        with self.assertRaisesRegex(ValueError, "implementation validation failed"):
            run_state.transition_run(
                self.run_dir, "integrated", integration_approved=True
            )
        manifest = run_state.load_run(self.run_dir)
        self.assertEqual(manifest["state"], "prototype_ready")
        self.assertNotIn("integration_approved_at", manifest)

    def test_direction_approval_enforces_and_audits_budget_expansion(self):
        self.advance_to_pending(direction_count=6)
        approved = [f"d-{index}" for index in range(6)]
        with self.assertRaisesRegex(ValueError, "generation budget"):
            run_state.transition_run(
                self.run_dir, "directions_approved", approved_direction_ids=approved
            )
        with self.assertRaisesRegex(ValueError, "budget expansion approval"):
            run_state.transition_run(
                self.run_dir,
                "directions_approved",
                approved_direction_ids=approved,
                generation_budget=6,
                max_attempts_per_direction=3,
            )
        manifest = run_state.transition_run(
            self.run_dir,
            "directions_approved",
            approved_direction_ids=approved,
            generation_budget=6,
            max_attempts_per_direction=3,
            budget_expansion_approved=True,
            now="2026-07-19T12:04:00Z",
        )
        self.assertEqual(manifest["generation_budget"], 6)
        self.assertEqual(manifest["max_attempts_per_direction"], 3)
        self.assertEqual(
            manifest["budget_expansion_approved_at"], "2026-07-19T12:04:00Z"
        )

    def test_complete_valid_lifecycle_reaches_integrated_deterministically(self):
        self.write_brief()
        self.assertEqual(run_state.transition_run(self.run_dir, "brief_ready")["state"], "brief_ready")
        self.write_research()
        self.assertEqual(run_state.transition_run(self.run_dir, "research_complete")["state"], "research_complete")
        self.write_directions()
        self.assertEqual(run_state.transition_run(self.run_dir, "directions_pending_approval")["state"], "directions_pending_approval")
        approved = run_state.transition_run(
            self.run_dir,
            "directions_approved",
            approved_direction_ids=["d-0"],
        )
        self.assertEqual(approved["generation_budget"], 5)
        self.assertEqual(approved["max_attempts_per_direction"], 2)
        self.write_mockups(["d-0"])
        self.assertEqual(run_state.transition_run(self.run_dir, "mockups_generated")["state"], "mockups_generated")
        self.assertEqual(
            run_state.transition_run(
                self.run_dir, "implementation_selected", selected_direction_id="d-0"
            )["selected_direction_id"],
            "d-0",
        )
        self.write("implementation.json", implementation())
        self.assertEqual(run_state.transition_run(self.run_dir, "prototype_ready")["state"], "prototype_ready")
        integrated = run_state.transition_run(
            self.run_dir,
            "integrated",
            integration_approved=True,
            now="2026-07-19T12:05:00Z",
        )
        self.assertEqual(integrated["state"], "integrated")
        self.assertEqual(integrated["integration_approved_at"], "2026-07-19T12:05:00Z")

    def test_cli_expected_failures_are_concise_without_tracebacks(self):
        unsupported = json.loads((self.run_dir / "run.json").read_text())
        unsupported["schema_version"] = 1
        self.write("run.json", unsupported)
        result = subprocess.run(
            [sys.executable, run_state.__file__, "status", "--run", str(self.run_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("migrate", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

        self.write("run.json", "{bad json")
        result = subprocess.run(
            [sys.executable, run_state.__file__, "status", "--run", str(self.run_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid run.json", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_revises_mockups_with_an_audit_reason(self):
        self.set_state("mockups_generated")
        self.write("mockup-manifest.json", {"mockups": []})

        result = subprocess.run(
            [
                sys.executable,
                run_state.__file__,
                "revise",
                "--run",
                str(self.run_dir),
                "--reason",
                "Create one bounded variation.",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["state"], "directions_pending_approval")
        self.assertEqual(manifest["revision_count"], 1)
        self.assertEqual(
            manifest["last_revision_reason"], "Create one bounded variation."
        )

    def test_cli_accepts_explicit_integration_approval(self):
        self.set_state("prototype_ready")
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["d-0"]
        manifest["selected_direction_id"] = "d-0"
        self.write("run.json", manifest)
        self.write("implementation.json", implementation())

        result = subprocess.run(
            [
                sys.executable,
                run_state.__file__,
                "transition",
                "--run",
                str(self.run_dir),
                "--to",
                "integrated",
                "--approve-integration",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["state"], "integrated")
        self.assertIn("integration_approved_at", manifest)


if __name__ == "__main__":
    unittest.main()
