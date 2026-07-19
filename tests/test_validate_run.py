import json
import tempfile
import unittest
from pathlib import Path

from design_explorer_import import load_script_module


validator = load_script_module("validate_run", "design-explorer/scripts/validate_run.py")


def reference(identifier="ref-1"):
    return {
        "id": identifier,
        "title": "Example checkout",
        "source_url": "https://example.com/checkout",
        "source_type": "product",
        "captured_at": "2026-07-19T12:00:00Z",
        "relevance": "Shows a comparable checkout hierarchy.",
        "observations": {axis: "observed" for axis in validator.AXES},
    }


def evidence(identifier="ev-1"):
    return {
        "id": identifier,
        "problem": "reduce checkout uncertainty",
        "title": "Checkout guidance",
        "publisher_or_author": "W3C Web Accessibility Initiative",
        "source_url": "https://www.w3.org/WAI/",
        "source_type": "official",
        "summary": "Make status and error information perceivable near the relevant control.",
        "application": "Keep errors adjacent to their fields.",
        "limitations": "Confirm against the target platform guidance.",
    }


def direction(identifier, **axes):
    defaults = dict(
        layout="stacked",
        typography="sans",
        palette="neutral",
        density="comfortable",
        imagery="none",
        interaction="progressive",
    )
    defaults.update(axes)
    return {
        "id": identifier,
        "name": identifier.title(),
        "concept": "A distinct checkout direction",
        "ux_problem": "reduce checkout uncertainty",
        "evidence_ids": ["ev-1"],
        "evidence_application": "Uses adjacent reassurance and error recovery.",
        "axes": defaults,
        "tradeoffs": "Balances speed and reassurance.",
        "implementation_difficulty": "medium",
        "implementation_risks": "Needs careful responsive hierarchy.",
    }


class ValidateRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.run / name).write_text(json.dumps(value), encoding="utf-8")

    def test_research_requires_traceable_http_sources(self):
        bad = reference()
        bad["source_url"] = "pinterest screenshot"
        self.write("references.json", [bad])
        self.write("evidence.json", [evidence()])
        errors = validator.validate_phase(self.run, "research")
        self.assertTrue(any("source_url" in error for error in errors))

    def test_directions_require_five_and_three_axis_pairwise_difference(self):
        self.write("evidence.json", [evidence()])
        directions = [direction(f"d-{index}") for index in range(5)]
        self.write("directions.json", directions)
        errors = validator.validate_phase(self.run, "directions")
        self.assertTrue(any("fewer than three axes" in error for error in errors))

    def test_distinct_evidence_linked_directions_pass(self):
        self.write("evidence.json", [evidence()])
        directions = [
            direction("editorial"),
            direction("dense", layout="grid", density="dense", typography="serif"),
            direction("visual", layout="split", palette="vivid", imagery="photo"),
            direction("calm", typography="rounded", density="spacious", interaction="guided"),
            direction("dark", layout="cards", palette="dark", imagery="illustration", interaction="direct"),
        ]
        self.write("directions.json", directions)
        self.assertEqual(validator.validate_phase(self.run, "directions"), [])

    def test_mockups_cover_every_approved_direction(self):
        self.write("run.json", {"approved_direction_ids": ["a", "b"]})
        self.write(
            "mockup-manifest.json",
            {
                "mockups": [
                    {
                        "direction_id": "a",
                        "status": "success",
                        "viewport": "390x844",
                        "prompt_digest": "sha256:abc",
                        "output_ref": "mockups/a.png",
                    }
                ]
            },
        )
        errors = validator.validate_phase(self.run, "mockups")
        self.assertEqual(errors, ["missing successful mockups for: b"])

    def test_implementation_matches_selection_and_records_render_checks(self):
        self.write("run.json", {"selected_direction_id": "a"})
        self.write(
            "implementation.json",
            {
                "selected_direction_id": "b",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": {"rendered_viewports": [], "checks": {}},
            },
        )
        errors = validator.validate_phase(self.run, "implementation")
        self.assertTrue(any("selected direction" in error for error in errors))
        self.assertTrue(any("rendered_viewports" in error for error in errors))

        self.write(
            "implementation.json",
            {
                "selected_direction_id": "a",
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
            },
        )
        self.assertEqual(validator.validate_phase(self.run, "implementation"), [])


if __name__ == "__main__":
    unittest.main()
