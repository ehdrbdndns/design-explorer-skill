import json
import tempfile
import unittest
from pathlib import Path

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

    def test_initial_manifest_is_resumable(self):
        manifest = run_state.load_run(self.run_dir)
        self.assertEqual(manifest["state"], "initialized")
        self.assertEqual(manifest["run_id"], "run-checkout")
        self.assertEqual(manifest["approved_direction_ids"], [])

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


if __name__ == "__main__":
    unittest.main()
