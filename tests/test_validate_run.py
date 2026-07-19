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


def evidence(identifier="ev-1", source_type="official"):
    return {
        "id": identifier,
        "problem": "reduce checkout uncertainty",
        "title": "Checkout guidance",
        "publisher_or_author": "W3C Web Accessibility Initiative",
        "source_url": "https://www.w3.org/WAI/",
        "source_type": source_type,
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
        "kind": "primary",
        "name": identifier.title(),
        "concept": "A distinct checkout direction",
        "ux_problem": "reduce checkout uncertainty",
        "evidence_ids": ["ev-1"],
        "evidence_application": "Uses adjacent reassurance and error recovery.",
        "baseline_exceptions": [],
        "axes": defaults,
        "tradeoffs": "Balances speed and reassurance.",
        "implementation_difficulty": "medium",
        "implementation_risks": "Needs careful responsive hierarchy.",
    }


def distinct_directions():
    return [
        direction("editorial"),
        direction("dense", layout="grid", density="dense", typography="serif"),
        direction("visual", layout="split", palette="vivid", imagery="photo"),
        direction(
            "calm",
            typography="rounded",
            density="spacious",
            interaction="guided",
        ),
        direction(
            "dark",
            layout="cards",
            palette="dark",
            imagery="illustration",
            interaction="direct",
        ),
    ]


def derived_direction(identifier, source_ids, **axes):
    item = direction(identifier, **axes)
    item["kind"] = "derived"
    item["derived_from_ids"] = source_ids
    item["combined_properties"] = {"layout": source_ids[0]}
    return item


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

    def test_http_urls_reject_whitespace_invalid_hosts_and_invalid_ports(self):
        invalid_urls = (
            "https://example.com/a path",
            "https:///missing-host",
            "https://-bad.example/path",
            "https://256.256.256.256/path",
            "https://example.com:not-a-port/path",
            "https://example.com:70000/path",
        )

        for value in invalid_urls:
            with self.subTest(value=value):
                self.assertFalse(validator.valid_url(value))

        self.assertTrue(validator.valid_url("https://example.com:443/path"))

    def test_research_rejects_duplicate_ids_per_source_collection(self):
        self.write("references.json", [reference(), reference()])
        self.write("evidence.json", [evidence(), evidence()])

        errors = validator.validate_phase(self.run, "research")

        self.assertIn("duplicate references id: ref-1", errors)
        self.assertIn("duplicate evidence id: ev-1", errors)

    def test_nested_research_and_direction_types_return_errors(self):
        bad_reference = reference()
        bad_reference["observations"] = "observed"
        self.write("references.json", [bad_reference])
        self.write("evidence.json", [evidence()])

        research_errors = validator.validate_phase(self.run, "research")

        self.assertIn("references[0] observations must be an object", research_errors)

        directions = [direction(f"d-{index}") for index in range(5)]
        directions[0]["evidence_ids"] = 42
        self.write("directions.json", directions)

        direction_errors = validator.validate_phase(self.run, "directions")

        self.assertIn(
            "directions[0] evidence_ids must be a non-empty list of non-empty strings",
            direction_errors,
        )

    def test_malformed_ids_modes_and_source_types_return_errors(self):
        bad_evidence = evidence()
        bad_evidence["source_type"] = []
        self.write("references.json", [reference()])
        self.write("evidence.json", [bad_evidence])

        research_errors = validator.validate_phase(self.run, "research")

        self.assertIn("evidence[0] has unsupported source_type", research_errors)

        bad_evidence = evidence()
        bad_evidence["id"] = {}
        directions = [direction(f"d-{index}") for index in range(5)]
        directions[0]["id"] = {}
        self.write("evidence.json", [bad_evidence])
        self.write("directions.json", directions)

        direction_errors = validator.validate_phase(self.run, "directions")

        self.assertIn("evidence[0] missing id", direction_errors)
        self.assertIn("directions[0] missing id", direction_errors)

        self.write(
            "run.json",
            {"selected_direction_id": "a", "approved_direction_ids": ["a"]},
        )
        self.write(
            "implementation.json",
            {
                "selected_direction_id": "a",
                "mode": [],
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

        implementation_errors = validator.validate_phase(self.run, "implementation")

        self.assertIn("implementation mode must be project or standalone", implementation_errors)

    def test_directions_require_five_and_three_axis_pairwise_difference(self):
        self.write("evidence.json", [evidence()])
        directions = [direction(f"d-{index}") for index in range(5)]
        self.write("directions.json", directions)
        errors = validator.validate_phase(self.run, "directions")
        self.assertTrue(any("fewer than three axes" in error for error in errors))

    def test_scalar_evidence_returns_an_error(self):
        self.write("evidence.json", 42)
        self.write("directions.json", [direction(f"d-{index}") for index in range(5)])

        errors = validator.validate_phase(self.run, "directions")

        self.assertIn("evidence.json must be a list", errors)

    def test_non_dict_axes_returns_an_error(self):
        self.write("evidence.json", [evidence()])
        directions = [direction(f"d-{index}") for index in range(5)]
        directions[0]["axes"] = "stacked"
        self.write("directions.json", directions)

        errors = validator.validate_phase(self.run, "directions")

        self.assertIn("directions[0] axes must be an object", errors)

    def test_axes_values_must_be_non_empty_strings(self):
        self.write("evidence.json", [evidence()])
        directions = [direction(f"d-{index}") for index in range(5)]
        directions[0]["axes"]["layout"] = "   "
        directions[1]["axes"]["typography"] = 42
        self.write("directions.json", directions)

        errors = validator.validate_phase(self.run, "directions")

        self.assertIn("directions[0] axis layout must be a non-empty string", errors)
        self.assertIn("directions[1] axis typography must be a non-empty string", errors)

    def test_axis_case_and_whitespace_do_not_count_as_material_difference(self):
        self.write("evidence.json", [evidence()])
        directions = [
            direction("first"),
            direction(
                "cosmetic",
                layout=" STACKED ",
                typography="SANS",
                palette="Neutral",
            ),
            direction("visual", layout="split", palette="vivid", imagery="photo"),
            direction(
                "calm",
                typography="rounded",
                density="spacious",
                interaction="guided",
            ),
            direction(
                "dark",
                layout="cards",
                palette="dark",
                imagery="illustration",
                interaction="direct",
            ),
        ]
        self.write("directions.json", directions)

        errors = validator.validate_phase(self.run, "directions")

        self.assertIn("first and cosmetic differ on fewer than three axes", errors)

    def test_distinct_evidence_linked_directions_pass(self):
        self.write("evidence.json", [evidence()])
        directions = distinct_directions()
        directions[0]["baseline_exceptions"] = [
            {
                "constraint": "minimum pointer target size",
                "justification": "The target platform owns this fixed native control.",
            }
        ]
        self.write("directions.json", directions)
        self.assertEqual(validator.validate_phase(self.run, "directions"), [])

    def test_every_direction_links_official_evidence(self):
        self.write(
            "evidence.json",
            [evidence(), evidence("ev-research", source_type="research")],
        )
        directions = distinct_directions()
        directions[0]["evidence_ids"] = ["ev-research"]
        self.write("directions.json", directions)

        errors = validator.validate_phase(self.run, "directions")

        self.assertIn(
            "directions[0] must link at least one official evidence item",
            errors,
        )

    def test_directions_require_well_formed_baseline_exceptions(self):
        self.write("evidence.json", [evidence()])
        directions = distinct_directions()
        directions[0].pop("baseline_exceptions")
        directions[1]["baseline_exceptions"] = "none"
        directions[2]["baseline_exceptions"] = [{}]
        directions[3]["baseline_exceptions"] = [
            {"constraint": "minimum contrast", "justification": "   "}
        ]
        self.write("directions.json", directions)

        errors = validator.validate_phase(self.run, "directions")

        self.assertIn("directions[0] missing baseline_exceptions", errors)
        self.assertIn("directions[1] baseline_exceptions must be a list", errors)
        self.assertIn(
            "directions[2] baseline_exceptions[0] missing constraint",
            errors,
        )
        self.assertIn(
            "directions[2] baseline_exceptions[0] missing justification",
            errors,
        )
        self.assertIn(
            "directions[3] baseline_exceptions[0] missing justification",
            errors,
        )

    def test_directions_require_kind_and_kind_specific_fields(self):
        self.write("evidence.json", [evidence()])
        directions = distinct_directions()
        directions[0].pop("kind")
        directions[1]["kind"] = "variant"
        directions[2]["derived_from_ids"] = []
        directions[3]["combined_properties"] = {}
        directions[4]["kind"] = "derived"
        self.write("directions.json", directions)

        errors = validator.validate_phase(self.run, "directions")

        self.assertIn("directions[0] kind must be primary or derived", errors)
        self.assertIn("directions[1] kind must be primary or derived", errors)
        self.assertIn(
            "directions[2] primary must omit derived_from_ids",
            errors,
        )
        self.assertIn(
            "directions[3] primary must omit combined_properties",
            errors,
        )
        self.assertIn(
            "directions[4] derived_from_ids must be a non-empty list of unique non-empty strings",
            errors,
        )
        self.assertIn(
            "directions[4] combined_properties must be a non-empty object",
            errors,
        )

    def test_non_string_direction_kind_returns_a_validation_error(self):
        self.write("evidence.json", [evidence()])
        for kind in ([], {}):
            with self.subTest(kind=kind):
                directions = distinct_directions()
                directions[0]["kind"] = kind
                self.write("directions.json", directions)

                errors = validator.validate_phase(self.run, "directions")

                self.assertIn(
                    "directions[0] kind must be primary or derived",
                    errors,
                )

    def test_derived_sources_reject_malformed_dangling_self_and_later_ids(self):
        self.write("evidence.json", [evidence()])
        cases = (
            (42, "non-empty list of unique non-empty strings"),
            (["editorial", "editorial"], "non-empty list of unique non-empty strings"),
            (["editorial", " "], "non-empty list of unique non-empty strings"),
            (["missing"], "previously declared direction IDs: missing"),
            (["combined"], "previously declared direction IDs: combined"),
        )
        for source_ids, expected in cases:
            with self.subTest(source_ids=source_ids):
                directions = distinct_directions()
                derived = derived_direction(
                    "combined",
                    ["editorial"],
                    layout="combined-layout",
                    typography="combined-type",
                    palette="combined-palette",
                    density="combined-density",
                    imagery="combined-imagery",
                    interaction="combined-interaction",
                )
                derived["derived_from_ids"] = source_ids
                directions.append(derived)
                self.write("directions.json", directions)

                errors = validator.validate_phase(self.run, "directions")

                self.assertTrue(any(expected in error for error in errors), errors)

        directions = distinct_directions()
        directions[0] = derived_direction(
            "editorial",
            ["dense"],
            layout="combined-layout",
            typography="combined-type",
            palette="combined-palette",
            density="combined-density",
            imagery="combined-imagery",
            interaction="combined-interaction",
        )
        self.write("directions.json", directions)
        errors = validator.validate_phase(self.run, "directions")
        self.assertTrue(
            any("previously declared direction IDs: dense" in error for error in errors),
            errors,
        )

    def test_derived_combined_properties_reject_invalid_or_unrelated_sources(self):
        self.write("evidence.json", [evidence()])
        invalid_properties = (
            ([], "must be a non-empty object"),
            ({}, "must be a non-empty object"),
            ({"border_radius": "editorial"}, "unsupported key: border_radius"),
            ({"layout": 42}, "layout must name a non-empty source ID"),
            ({"layout": "dense"}, "layout source is not in derived_from_ids: dense"),
        )
        for combined_properties, expected in invalid_properties:
            with self.subTest(combined_properties=combined_properties):
                directions = distinct_directions()
                derived = derived_direction(
                    "combined",
                    ["editorial"],
                    layout="combined-layout",
                    typography="combined-type",
                    palette="combined-palette",
                    density="combined-density",
                    imagery="combined-imagery",
                    interaction="combined-interaction",
                )
                derived["combined_properties"] = combined_properties
                directions.append(derived)
                self.write("directions.json", directions)

                errors = validator.validate_phase(self.run, "directions")

                self.assertTrue(any(expected in error for error in errors), errors)

    def test_valid_derived_combination_passes(self):
        self.write("evidence.json", [evidence()])
        directions = distinct_directions()
        derived = derived_direction(
            "combined",
            ["editorial", "visual"],
            layout="combined-layout",
            typography="combined-type",
            palette="combined-palette",
            density="combined-density",
            imagery="combined-imagery",
            interaction="combined-interaction",
        )
        derived["combined_properties"] = {
            "layout": "editorial",
            "palette": "visual",
            "imagery": "visual",
        }
        directions.append(derived)
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

    def test_mockups_require_at_least_one_approved_direction(self):
        self.write("run.json", {"approved_direction_ids": []})
        self.write("mockup-manifest.json", {"mockups": []})

        errors = validator.validate_phase(self.run, "mockups")

        self.assertIn(
            "run.json approved_direction_ids must be a non-empty list",
            errors,
        )

    def test_mockups_reject_invalid_or_duplicate_approved_direction_ids(self):
        self.write("run.json", {"approved_direction_ids": ["a", " ", "a", 42]})
        self.write("mockup-manifest.json", {"mockups": []})

        errors = validator.validate_phase(self.run, "mockups")

        self.assertIn(
            "run.json approved_direction_ids must contain non-empty strings",
            errors,
        )
        self.assertIn(
            "run.json approved_direction_ids must contain unique values",
            errors,
        )

    def test_mockups_reject_unapproved_directions_and_invalid_success_fields(self):
        self.write("run.json", {"approved_direction_ids": ["a"]})
        self.write(
            "mockup-manifest.json",
            {
                "mockups": [
                    {"direction_id": "b", "status": "failed"},
                    {
                        "direction_id": "a",
                        "status": "success",
                        "viewport": 390,
                        "prompt_digest": " ",
                        "output_ref": None,
                    },
                    {"direction_id": 42, "status": "failed"},
                ]
            },
        )

        errors = validator.validate_phase(self.run, "mockups")

        self.assertIn("mockups[0] direction_id is not approved: b", errors)
        self.assertIn("mockups[1] missing viewport", errors)
        self.assertIn("mockups[1] missing prompt_digest", errors)
        self.assertIn("mockups[1] missing output_ref", errors)
        self.assertIn("mockups[2] direction_id must be a non-empty string", errors)
        self.assertIn("missing successful mockups for: a", errors)

    def test_implementation_matches_selection_and_records_render_checks(self):
        self.write(
            "run.json",
            {"selected_direction_id": "a", "approved_direction_ids": ["a", "b"]},
        )
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

    def test_implementation_selection_must_be_non_empty_and_approved(self):
        valid_verification = {
            "rendered_viewports": ["390x844"],
            "checks": {
                "content": "pass",
                "overflow": "pass",
                "accessibility": "pass",
            },
        }
        self.write(
            "run.json",
            {"selected_direction_id": "a", "approved_direction_ids": ["a"]},
        )
        self.write(
            "implementation.json",
            {
                "selected_direction_id": " ",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": valid_verification,
            },
        )

        errors = validator.validate_phase(self.run, "implementation")

        self.assertIn(
            "implementation selected_direction_id must be a non-empty string",
            errors,
        )

        self.write(
            "run.json",
            {"selected_direction_id": "b", "approved_direction_ids": ["a"]},
        )
        self.write(
            "implementation.json",
            {
                "selected_direction_id": "b",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": valid_verification,
            },
        )

        errors = validator.validate_phase(self.run, "implementation")

        self.assertIn("implementation selected direction is not approved", errors)

    def test_implementation_non_dict_verification_returns_an_error(self):
        self.write(
            "run.json",
            {"selected_direction_id": "a", "approved_direction_ids": ["a"]},
        )
        self.write(
            "implementation.json",
            {
                "selected_direction_id": "a",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": "complete",
            },
        )

        errors = validator.validate_phase(self.run, "implementation")

        self.assertIn("implementation verification must be an object", errors)

    def test_implementation_non_dict_checks_returns_an_error(self):
        self.write(
            "run.json",
            {"selected_direction_id": "a", "approved_direction_ids": ["a"]},
        )
        self.write(
            "implementation.json",
            {
                "selected_direction_id": "a",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": {
                    "rendered_viewports": ["390x844"],
                    "checks": ["content", "overflow", "accessibility"],
                },
            },
        )

        errors = validator.validate_phase(self.run, "implementation")

        self.assertIn("implementation checks must be an object", errors)

    def test_implementation_rendered_viewports_are_non_empty_strings(self):
        self.write(
            "run.json",
            {"selected_direction_id": "a", "approved_direction_ids": ["a"]},
        )
        self.write(
            "implementation.json",
            {
                "selected_direction_id": "a",
                "mode": "project",
                "preview_path": "src/previews/Checkout.tsx",
                "verification": {
                    "rendered_viewports": ["390x844", " ", 42],
                    "checks": {
                        "content": "pass",
                        "overflow": "pass",
                        "accessibility": "pass",
                    },
                },
            },
        )

        errors = validator.validate_phase(self.run, "implementation")

        self.assertIn(
            "implementation rendered_viewports must be a non-empty list of non-empty strings",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
