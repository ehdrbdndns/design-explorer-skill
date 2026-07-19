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

    def test_initial_manifest_is_resumable(self):
        manifest = run_state.load_run(self.run_dir)
        self.assertEqual(manifest["state"], "initialized")
        self.assertEqual(manifest["run_id"], "run-checkout")
        self.assertEqual(manifest["approved_direction_ids"], [])
        self.assertEqual(manifest["revision_count"], 0)

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
        self.write("brief.md")
        run_state.transition_run(self.run_dir, "brief_ready")
        for name in ("references.json", "evidence.json", "design-evidence.md", "reference-board.md"):
            self.write(name, [] if name.endswith(".json") else "# Evidence")
        run_state.transition_run(self.run_dir, "research_complete")
        self.write("directions.json", [{"id": "calm-editorial"}])
        self.write("mood-directions.md", "## Calm Editorial")
        run_state.transition_run(self.run_dir, "directions_pending_approval")
        with self.assertRaisesRegex(ValueError, "explicit approved_direction_ids"):
            run_state.transition_run(self.run_dir, "directions_approved")
        with self.assertRaisesRegex(ValueError, "unknown direction"):
            run_state.transition_run(
                self.run_dir,
                "directions_approved",
                approved_direction_ids=["unknown"],
            )

    def test_mockups_and_selection_are_limited_to_approved_directions(self):
        self.write("brief.md")
        run_state.transition_run(self.run_dir, "brief_ready")
        for name in ("references.json", "evidence.json"):
            self.write(name, [])
        for name in ("design-evidence.md", "reference-board.md"):
            self.write(name)
        run_state.transition_run(self.run_dir, "research_complete")
        self.write("directions.json", [{"id": "a"}, {"id": "b"}])
        self.write("mood-directions.md")
        run_state.transition_run(self.run_dir, "directions_pending_approval")
        run_state.transition_run(
            self.run_dir, "directions_approved", approved_direction_ids=["a"]
        )
        self.write("mockup-manifest.json", {"mockups": [{"direction_id": "a", "status": "success"}]})
        run_state.transition_run(self.run_dir, "mockups_generated")
        with self.assertRaisesRegex(ValueError, "approved direction"):
            run_state.transition_run(
                self.run_dir, "implementation_selected", selected_direction_id="b"
            )
        manifest = run_state.transition_run(
            self.run_dir, "implementation_selected", selected_direction_id="a"
        )
        self.assertEqual(manifest["selected_direction_id"], "a")

    def test_integration_requires_explicit_approval_and_records_it(self):
        self.set_state("prototype_ready")
        self.write("implementation.json", {})
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
        self.write(
            "mockup-manifest.json",
            {"mockups": [{"direction_id": "a", "status": "success"}]},
        )
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["a"]
        manifest["selected_direction_id"] = "a"
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
        self.assertFalse((self.run_dir / "mockup-manifest.json").exists())
        self.assertTrue(
            (self.run_dir / "mockup-manifest.revision-1.json").is_file()
        )
        self.assertEqual(
            json.loads(
                (self.run_dir / "mockup-manifest.revision-1.json").read_text()
            ),
            {"mockups": [{"direction_id": "a", "status": "success"}]},
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
        self.write("implementation.json", {})

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
