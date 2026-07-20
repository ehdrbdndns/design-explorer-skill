import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from design_explorer_import import load_script_module
from test_preview_evidence import preview_digest, write_png


validator = load_script_module("validate_run", "design-explorer/scripts/validate_run.py")
PROMPT_BYTES = b"mockup prompt\n"
DIGEST = "sha256:" + hashlib.sha256(PROMPT_BYTES).hexdigest()


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


def run_manifest(**overrides):
    value = {
        "schema_version": 2,
        "run_id": "run-checkout",
        "slug": "checkout",
        "state": "directions_approved",
        "created_at": "2026-07-19T12:00:00Z",
        "updated_at": "2026-07-19T12:00:00Z",
        "project_path": None,
        "approved_direction_ids": ["a"],
        "selected_direction_id": None,
        "revision_count": 0,
        "generation_budget": 5,
        "max_attempts_per_direction": 2,
        "target_viewports": ["390x844"],
        "required_content": ["Order summary"],
        "required_interactions": ["Edit order"],
        "production_paths": [],
    }
    value.update(overrides)
    return value


def mockup(direction_id="a", **overrides):
    value = {
        "direction_id": direction_id,
        "status": "success",
        "viewport": "390x844",
        "prompt_ref": f"prompts/{direction_id}.txt",
        "prompt_digest": DIGEST,
        "output_kind": "provider",
        "output_ref": f"provider:openai:{direction_id}",
        "attempt_count": 1,
    }
    value.update(overrides)
    return value


def mockup_manifest(mockups, **overrides):
    attempts = sum(
        item.get("attempt_count", 0)
        for item in mockups
        if isinstance(item, dict)
        and isinstance(item.get("attempt_count", 0), int)
        and not isinstance(item.get("attempt_count", 0), bool)
    )
    direction_id = next(
        (
            item.get("direction_id")
            for item in reversed(mockups)
            if isinstance(item, dict)
            and isinstance(item.get("direction_id"), str)
            and item["direction_id"].strip()
        ),
        None,
    )
    value = {
        "schema_version": 1,
        "generation_attempts_used": attempts,
        "last_generation_authorized_at": (
            "2026-07-19T12:00:00Z" if attempts else None
        ),
        "last_generation_direction_id": direction_id if attempts else None,
        "mockups": mockups,
    }
    value.update(overrides)
    return value


class ValidateRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)
        (self.run / "prompts").mkdir()
        for identifier in ("a", "b", *(f"d-{index}" for index in range(8))):
            (self.run / "prompts" / f"{identifier}.txt").write_bytes(PROMPT_BYTES)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.run / name).write_text(json.dumps(value), encoding="utf-8")

    def code_preview_fixture(self, *, mode="project", viewports=None):
        viewports = viewports or ["390x844"]
        direction_id = "d-0"
        if mode == "project":
            source_root = self.run / "project"
            prefix = ""
            project_path = str(source_root)
        else:
            source_root = self.run
            prefix = "standalone/"
            project_path = None
        preview_files = [
            f"{prefix}previews/{direction_id}/Screen.tsx",
            f"{prefix}src/tokens.css",
            f"{prefix}src/Button.tsx",
        ]
        screen = source_root / preview_files[0]
        tokens = source_root / preview_files[1]
        button = source_root / preview_files[2]
        screen.parent.mkdir(parents=True, exist_ok=True)
        tokens.parent.mkdir(parents=True, exist_ok=True)
        tokens.write_text(
            ":root { --color-surface: white; --space-4: 1rem; }\n",
            encoding="utf-8",
        )
        button.write_text(
            "export function Button(){return <button>Continue</button>}\n",
            encoding="utf-8",
        )
        screen.write_text(
            "import '../../src/tokens.css';\n"
            "import { Button } from '../../src/Button';\n"
            "export function Screen(){return <main style={{background: "
            "'var(--color-surface)', gap: 'var(--space-4)'}}><Button /></main>}\n",
            encoding="utf-8",
        )
        checks = {}
        for viewport in viewports:
            screenshot_ref = f"evidence/{direction_id}/{viewport}.png"
            screenshot = self.run / screenshot_ref
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            width, height = (int(value) for value in viewport.split("x"))
            write_png(screenshot, width, height)
            checks[viewport] = {
                "screenshot_ref": screenshot_ref,
                "content": "pass",
                "overflow": "pass",
                "accessibility": "pass",
                "interaction": "pass",
            }
        run = run_manifest(
            approved_direction_ids=[direction_id],
            target_viewports=viewports,
            project_path=project_path,
        )
        item = mockup(
            direction_id,
            artifact_kind="code-preview",
            attempt_count=0,
            output_kind="local",
            output_ref=checks[viewports[0]]["screenshot_ref"],
            preview_mode=mode,
            preview_path=preview_files[0],
            preview_files=preview_files,
            preview_route=f"/design-explorer/{direction_id}",
            token_sources=[preview_files[1]],
            used_tokens=["--color-surface", "--space-4"],
            component_sources=[preview_files[2]],
            supporting_provider_refs=[],
            source_digest=preview_digest(source_root, preview_files),
            viewport_checks=checks,
        )
        return run, item, source_root

    def test_research_requires_traceable_http_sources(self):
        bad = reference()
        bad["source_url"] = "pinterest screenshot"
        self.write("references.json", [bad])
        self.write("evidence.json", [evidence()])
        errors = validator.validate_phase(self.run, "research")
        self.assertTrue(any("source_url" in error for error in errors))

    def test_validator_cli_json_failure_is_concise_stderr(self):
        (self.run / "references.json").write_text("{bad json", encoding="utf-8")
        self.write("evidence.json", [evidence()])

        result = subprocess.run(
            [
                sys.executable,
                validator.__file__,
                "--run",
                str(self.run),
                "--phase",
                "research",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid references.json", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

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

    def test_http_urls_reject_credentials_and_non_public_hosts(self):
        invalid_urls = (
            "https://user:password@example.com/path",
            "http://localhost/path",
            "http://intranet/path",
            "https://service.local/path",
            "https://service.localhost/path",
            "https://service.test/path",
            "https://service.invalid/path",
            "https://service.example/path",
            "https://service.onion/path",
            "https://service.internal/path",
            "https://service.alt/path",
            "https://home.arpa/path",
            "https://device.home.arpa/path",
            "https://127-0-0-1.nip.io/path",
            "https://private.127-0-0-1.nip.io/path",
            "https://127-0-0-1.sslip.io/path",
            "https://127.0.0.1/path",
            "https://10.0.0.1/path",
            "https://169.254.1.1/path",
            "https://224.0.0.1/path",
            "https://0.0.0.0/path",
            "https://192.0.2.1/path",
            "https://[::1]/path",
            "https://example.com/\x00hidden",
            "https://example.com/\x1fhidden",
            "https://example.com/\x7fhidden",
            "https://example.com/\u0080hidden",
            "https://example.com/\u009fhidden",
        )

        for value in invalid_urls:
            with self.subTest(value=value):
                self.assertFalse(validator.valid_url(value))

        self.assertTrue(validator.valid_url("https://www.w3.org/WAI/"))

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
            layout="stacked",
            typography="combined-type",
            palette="vivid",
            density="combined-density",
            imagery="photo",
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

    def test_derived_axes_must_equal_named_prior_sources_and_use_every_source(self):
        self.write("evidence.json", [evidence()])
        directions = distinct_directions()
        derived = derived_direction(
            "combined",
            ["editorial", "visual"],
            layout="not-from-editorial",
            typography="combined-type",
            palette="vivid",
            density="combined-density",
            imagery="photo",
            interaction="combined-interaction",
        )
        derived["combined_properties"] = {"layout": "editorial"}
        directions.append(derived)
        self.write("directions.json", directions)

        errors = validator.validate_phase(self.run, "directions")

        self.assertTrue(any("layout must match source editorial" in error for error in errors), errors)
        self.assertTrue(any("source must contribute at least one axis: visual" in error for error in errors), errors)

    def test_derived_provenance_normalizes_case_and_whitespace(self):
        self.write("evidence.json", [evidence()])
        directions = distinct_directions()
        derived = derived_direction(
            "combined",
            ["editorial", "visual"],
            layout=" STACKED ",
            typography="combined-type",
            palette="VIVID",
            density="combined-density",
            imagery="PHOTO ",
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
        self.write("run.json", run_manifest(approved_direction_ids=["a", "b"]))
        self.write(
            "mockup-manifest.json",
            mockup_manifest([mockup("a")]),
        )
        errors = validator.validate_phase(self.run, "mockups")
        self.assertIn("missing current mockup entries for: b", errors)
        self.assertIn("missing successful mockups for: b", errors)

    def test_code_preview_success_allows_zero_provider_attempts(self):
        for mode in ("project", "standalone"):
            with self.subTest(mode=mode):
                run, item, _ = self.code_preview_fixture(mode=mode)
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))

                self.assertEqual(validator.validate_mockups(self.run), [])

    def test_code_preview_requires_complete_topology_and_all_viewports(self):
        run, item, _ = self.code_preview_fixture(
            mode="project", viewports=["390x844", "1280x720"]
        )
        self.write("run.json", run)

        cases = []
        missing_tokens = copy.deepcopy(item)
        missing_tokens.pop("token_sources")
        cases.append(
            (missing_tokens, "code preview token_sources must be a non-empty list")
        )
        missing_components = copy.deepcopy(item)
        missing_components["component_sources"] = []
        cases.append(
            (
                missing_components,
                "project code preview component_sources must be a non-empty list",
            )
        )
        missing_viewport = copy.deepcopy(item)
        missing_viewport["viewport_checks"].pop("1280x720")
        cases.append(
            (
                missing_viewport,
                "code preview viewport_checks keys must exactly match run target_viewports",
            )
        )
        incomplete_check = copy.deepcopy(item)
        incomplete_check["viewport_checks"]["1280x720"].pop("interaction")
        cases.append(
            (
                incomplete_check,
                "code preview viewport_checks[1280x720] status must pass: interaction",
            )
        )
        missing_screenshot = copy.deepcopy(item)
        missing_screenshot["viewport_checks"]["1280x720"]["screenshot_ref"] = (
            "evidence/d-0/missing.png"
        )
        cases.append(
            (
                missing_screenshot,
                "code preview viewport_checks[1280x720] screenshot must be an existing file",
            )
        )
        for invalid, expected in cases:
            with self.subTest(expected=expected):
                self.write("mockup-manifest.json", mockup_manifest([invalid]))
                errors = validator.validate_mockups(self.run)
                self.assertTrue(any(expected in error for error in errors), errors)

        standalone_run, standalone_item, _ = self.code_preview_fixture(
            mode="standalone"
        )
        standalone_item["component_sources"] = []
        self.write("run.json", standalone_run)
        self.write("mockup-manifest.json", mockup_manifest([standalone_item]))
        errors = validator.validate_mockups(self.run)
        self.assertTrue(
            any(
                "code preview component_sources must be a non-empty list" in error
                for error in errors
            ),
            errors,
        )

    def test_code_preview_tokens_must_be_defined_and_used(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        self.write("run.json", run)

        undefined = copy.deepcopy(item)
        undefined["used_tokens"].append("--color-unknown")
        self.write("mockup-manifest.json", mockup_manifest([undefined]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] code preview used token must be defined by token_sources: --color-unknown",
            errors,
        )

        tokens = source_root / item["token_sources"][0]
        tokens.write_text(
            tokens.read_text(encoding="utf-8").replace(
                "}", " --space-8: 2rem; }"
            ),
            encoding="utf-8",
        )
        unused = copy.deepcopy(item)
        unused["used_tokens"].append("--space-8")
        unused["source_digest"] = preview_digest(source_root, unused["preview_files"])
        self.write("mockup-manifest.json", mockup_manifest([unused]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] code preview used token must be referenced by preview dependency set: --space-8",
            errors,
        )

    def test_code_preview_provenance_must_be_reachable_from_preview_path(self):
        for mode in ("project", "standalone"):
            with self.subTest(mode=mode):
                run, item, source_root = self.code_preview_fixture(mode=mode)
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "export function Screen(){return <main style={{background: "
                    "'var(--color-surface)', gap: 'var(--space-4)'}}>Preview</main>}\n",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))

                errors = validator.validate_mockups(self.run)

                self.assertIn(
                    f"mockups[0] code preview token_source must be reachable from preview_path: {item['token_sources'][0]}",
                    errors,
                )
                self.assertIn(
                    f"mockups[0] code preview component_source must be reachable from preview_path: {item['component_sources'][0]}",
                    errors,
                )

        run, item, source_root = self.code_preview_fixture(mode="project")
        screen = source_root / item["preview_path"]
        screen.write_text(
            "import '../../src/tokens.css';\n"
            "import { Button } from '../../src/Button';\n"
            "export function Screen(){return <main><Button /></main>}\n",
            encoding="utf-8",
        )
        unreachable = "previews/d-0/Unused.tsx"
        (source_root / unreachable).write_text(
            "export const Unused=()=> <aside style={{background: "
            "'var(--color-surface)', gap: 'var(--space-4)'}} />;\n",
            encoding="utf-8",
        )
        item["preview_files"].append(unreachable)
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("run.json", run)
        self.write("mockup-manifest.json", mockup_manifest([item]))

        errors = validator.validate_mockups(self.run)

        for token in item["used_tokens"]:
            self.assertIn(
                f"mockups[0] code preview used token must be referenced by preview dependency set: {token}",
                errors,
            )

    def test_code_preview_component_source_requires_runtime_reachability(self):
        for mode in ("project", "standalone"):
            with self.subTest(mode=mode):
                run, item, source_root = self.code_preview_fixture(mode=mode)
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "import '../../src/tokens.css';\n"
                    "import type { Button } from '../../src/Button';\n"
                    "export function Screen(){return <main style={{background: "
                    "'var(--color-surface)', gap: 'var(--space-4)'}}>Preview</main>}\n",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))

                errors = validator.validate_mockups(self.run)

                self.assertIn(
                    f"mockups[0] code preview component_source must be reachable from preview_path: {item['component_sources'][0]}",
                    errors,
                )

    def test_code_preview_type_only_reexport_is_not_runtime_reachable(self):
        for mode in ("project", "standalone"):
            with self.subTest(mode=mode):
                run, item, source_root = self.code_preview_fixture(mode=mode)
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "import '../../src/tokens.css';\n"
                    "export type { Button } from '../../src/Button';\n"
                    "export function Screen(){return <main style={{background: "
                    "'var(--color-surface)', gap: 'var(--space-4)'}}>Preview</main>}\n",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))

                errors = validator.validate_mockups(self.run)

                self.assertIn(
                    f"mockups[0] code preview component_source must be reachable from preview_path: {item['component_sources'][0]}",
                    errors,
                )

    def test_code_preview_ts_type_import_expressions_are_not_runtime_reachable(self):
        type_expressions = (
            "type ButtonType = typeof import('../../src/Button');",
            "type ButtonType = import('../../src/Button').Button;",
            "type ButtonType =\n typeof import('../../src/Button');",
            "type ButtonType =\n import('../../src/Button').Button;",
            "type ButtonType = { button: import('../../src/Button').Button };",
            "interface Props { button: import('../../src/Button').Button }",
            "declare const button: import('../../src/Button').Button;",
            "declare function use(button: import('../../src/Button').Button): void;",
            "const button = null as import('../../src/Button').Button;",
            "function use(button: import('../../src/Button').Button): void {}",
        )
        for mode in ("project", "standalone"):
            for type_expression in type_expressions:
                with self.subTest(mode=mode, type_expression=type_expression):
                    run, item, source_root = self.code_preview_fixture(mode=mode)
                    screen = source_root / item["preview_path"]
                    screen.write_text(
                        "import '../../src/tokens.css';\n"
                        f"{type_expression}\n"
                        "export function Screen(){return <main style={{background: "
                        "'var(--color-surface)', gap: 'var(--space-4)'}}>Preview</main>}\n",
                        encoding="utf-8",
                    )
                    item["source_digest"] = preview_digest(
                        source_root, item["preview_files"]
                    )
                    self.write("run.json", run)
                    self.write("mockup-manifest.json", mockup_manifest([item]))

                    errors = validator.validate_mockups(self.run)

                    self.assertIn(
                        f"mockups[0] code preview component_source must be reachable from preview_path: {item['component_sources'][0]}",
                        errors,
                    )

    def test_code_preview_runtime_dynamic_import_remains_reachable(self):
        prefixes = (
            "",
            "type Meta = string\n",
            "declare const metadata: string\n",
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                run, item, source_root = self.code_preview_fixture(mode="project")
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "import '../../src/tokens.css';\n"
                    f"{prefix}"
                    "const buttonModule = import('../../src/Button');\n"
                    "export function Screen(){return <main style={{background: "
                    "'var(--color-surface)', gap: 'var(--space-4)'}}>Preview</main>}\n",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))

                self.assertEqual(validator.validate_mockups(self.run), [])

    def test_code_preview_ts_annotations_are_not_runtime_reachable(self):
        annotations = (
            "const fake: typeof import('../../src/Button').Button = null as never;",
            "function fake(): import('../../src/Button').Button { throw new Error(); }",
            "class Fake { value!: typeof import('../../src/Button').Button }",
            "const fake: Promise<import('../../src/Button').Button> = Promise.reject();",
            "function fake(): Promise<ReadonlyArray<import('../../src/Button').Button>> { throw new Error(); }",
            "class Fake { value!: Array<import('../../src/Button').Button | null> }",
            "const fake: true extends boolean ? import('../../src/Button').Button[] : never = null as never;",
        )
        for mode in ("project", "standalone"):
            for annotation in annotations:
                with self.subTest(mode=mode, annotation=annotation):
                    run, item, source_root = self.code_preview_fixture(mode=mode)
                    screen = source_root / item["preview_path"]
                    screen.write_text(
                        "import '../../src/tokens.css';\n"
                        f"{annotation}\n"
                        "export function Screen(){return <main style={{background: "
                        "'var(--color-surface)', gap: 'var(--space-4)'}}>Preview</main>}\n",
                        encoding="utf-8",
                    )
                    item["source_digest"] = preview_digest(
                        source_root, item["preview_files"]
                    )
                    self.write("run.json", run)
                    self.write("mockup-manifest.json", mockup_manifest([item]))

                    errors = validator.validate_mockups(self.run)

                    self.assertIn(
                        f"mockups[0] code preview component_source must be reachable from preview_path: {item['component_sources'][0]}",
                        errors,
                    )

    def test_code_preview_css_token_definitions_ignore_comments(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        tokens = source_root / item["token_sources"][0]
        tokens.write_text(
            ":root { /* --color-surface: white; */ --space-4: 1rem; }\n",
            encoding="utf-8",
        )
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("run.json", run)
        self.write("mockup-manifest.json", mockup_manifest([item]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] code preview used token must be defined by token_sources: --color-surface",
            errors,
        )

    def test_code_preview_css_token_uses_ignore_comments(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        screen = source_root / item["preview_path"]
        screen.write_text(
            "import '../../src/tokens.css';\n"
            "import { Button } from '../../src/Button';\n"
            "export function Screen(){return <main><Button /></main>}\n",
            encoding="utf-8",
        )
        tokens = source_root / item["token_sources"][0]
        tokens.write_text(
            tokens.read_text(encoding="utf-8")
            + "/* var(--color-surface); var(--space-4); */\n",
            encoding="utf-8",
        )
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("run.json", run)
        self.write("mockup-manifest.json", mockup_manifest([item]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] code preview used token must be referenced by preview dependency set: --color-surface",
            errors,
        )

    def test_code_preview_js_token_uses_ignore_comments_and_inert_strings(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        screen = source_root / item["preview_path"]
        screen.write_text(
            "import '../../src/tokens.css';\n"
            "import { Button } from '../../src/Button';\n"
            "// var(--color-surface); var(--space-4);\n"
            "/* var(--color-surface); var(--space-4); */\n"
            "const inert = 'var(--color-surface) var(--space-4)';\n"
            "export function Screen(){return <main><Button /></main>}\n",
            encoding="utf-8",
        )
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("run.json", run)
        self.write("mockup-manifest.json", mockup_manifest([item]))
        errors = validator.validate_mockups(self.run)
        for token in item["used_tokens"]:
            self.assertIn(
                f"mockups[0] code preview used token must be referenced by preview dependency set: {token}",
                errors,
            )

    def test_code_preview_jsx_style_values_are_direct_and_type_aware(self):
        valid_styles = (
            "{{background: 'var(--color-surface)', gap: 'var(--space-4)'} as React.CSSProperties}",
            "{{background: 'var(--color-surface)', gap: 'var(--space-4)'} satisfies React.CSSProperties}",
            "{{background: `var(--color-surface)`, gap: `var(--space-4)`}}",
            "{{background: active ? 'var(--color-surface)' : 'transparent', gap: ('var(--space-4)')}}",
        )
        for style in valid_styles:
            with self.subTest(valid=style):
                run, item, source_root = self.code_preview_fixture(mode="project")
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "import '../../src/tokens.css';\n"
                    "import { Button } from '../../src/Button';\n"
                    f"export function Screen(){{return <main style={style}><Button /></main>}}\n",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))
                self.assertEqual(validator.validate_mockups(self.run), [])

        inert_styles = (
            "{{background: log('var(--color-surface)'), gap: log('var(--space-4)')}}",
            "{{'var(--color-surface)': 'red', 'var(--space-4)': '1rem'}}",
            "{{background: log(active ? 'red' : 'var(--color-surface)'), gap: log(active ? '0' : 'var(--space-4)')}}",
        )
        for style in inert_styles:
            with self.subTest(inert=style):
                run, item, source_root = self.code_preview_fixture(mode="project")
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "import '../../src/tokens.css';\n"
                    "import { Button } from '../../src/Button';\n"
                    f"export function Screen(){{return <main style={style}><Button /></main>}}\n",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))
                errors = validator.validate_mockups(self.run)
                for token in item["used_tokens"]:
                    self.assertIn(
                        f"mockups[0] code preview used token must be referenced by preview dependency set: {token}",
                        errors,
                    )

    def test_code_preview_css_comment_scanner_preserves_quoted_markers(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        screen = source_root / item["preview_path"]
        screen.write_text(
            "import '../../src/tokens.css';\n"
            "import { Button } from '../../src/Button';\n"
            "export function Screen(){return <main><Button /></main>}\n",
            encoding="utf-8",
        )
        tokens = source_root / item["token_sources"][0]
        tokens.write_text(
            ':root { --marker: "/*"; --color-surface: white; --space-4: 1rem; }\n'
            ".preview { background: var(--color-surface); gap: var(--space-4); }\n",
            encoding="utf-8",
        )
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("run.json", run)
        self.write("mockup-manifest.json", mockup_manifest([item]))

        self.assertEqual(validator.validate_mockups(self.run), [])

    def test_code_preview_css_like_token_sources_mask_comments_and_quotes(self):
        for suffix in (".scss", ".sass", ".less"):
            for comment_only in (True, False):
                with self.subTest(suffix=suffix, comment_only=comment_only):
                    run, item, source_root = self.code_preview_fixture(mode="project")
                    old_relative = item["token_sources"][0]
                    new_relative = str(PurePosixPath(old_relative).with_suffix(suffix))
                    old_path = source_root / old_relative
                    new_path = source_root / new_relative
                    old_path.rename(new_path)
                    item["token_sources"] = [new_relative]
                    item["preview_files"] = [
                        new_relative if path == old_relative else path
                        for path in item["preview_files"]
                    ]
                    screen = source_root / item["preview_path"]
                    screen.write_text(
                        screen.read_text(encoding="utf-8").replace(
                            "tokens.css", f"tokens{suffix}"
                        ),
                        encoding="utf-8",
                    )
                    new_path.write_text(
                        (
                            ':root { --marker: "/*"; /* --color-surface: white; */ --space-4: 1rem; }\n'
                            if comment_only
                            else ':root { --marker: "/*"; --color-surface: white; --space-4: 1rem; }\n'
                        ),
                        encoding="utf-8",
                    )
                    item["source_digest"] = preview_digest(
                        source_root, item["preview_files"]
                    )
                    self.write("run.json", run)
                    self.write("mockup-manifest.json", mockup_manifest([item]))

                    errors = validator.validate_mockups(self.run)

                    if comment_only:
                        self.assertIn(
                            "mockups[0] code preview used token must be defined by token_sources: --color-surface",
                            errors,
                        )
                    else:
                        self.assertEqual(errors, [])

    def test_code_preview_rejects_unsupported_token_source_suffix(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        old_relative = item["token_sources"][0]
        new_relative = str(PurePosixPath(old_relative).with_suffix(".tokens"))
        old_path = source_root / old_relative
        new_path = source_root / new_relative
        old_path.rename(new_path)
        item["token_sources"] = [new_relative]
        item["preview_files"] = [
            new_relative if path == old_relative else path
            for path in item["preview_files"]
        ]
        screen = source_root / item["preview_path"]
        screen.write_text(
            screen.read_text(encoding="utf-8").replace("tokens.css", "tokens.tokens"),
            encoding="utf-8",
        )
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("run.json", run)
        self.write("mockup-manifest.json", mockup_manifest([item]))

        errors = validator.validate_mockups(self.run)

        self.assertIn(
            f"mockups[0] code preview token_source must use a supported stylesheet suffix (.css, .less, .sass, .scss): {new_relative}",
            errors,
        )

    def test_code_preview_style_alias_must_be_referenced_by_jsx(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        screen = source_root / item["preview_path"]
        screen.write_text(
            "import '../../src/tokens.css';\n"
            "import { Button } from '../../src/Button';\n"
            "const styles = { background: 'var(--color-surface)', gap: 'var(--space-4)' };\n"
            "export function Screen(){return <main style={styles}><Button /></main>}\n",
            encoding="utf-8",
        )
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("run.json", run)
        self.write("mockup-manifest.json", mockup_manifest([item]))

        self.assertEqual(validator.validate_mockups(self.run), [])

        screen.write_text(
            screen.read_text(encoding="utf-8").replace(
                "style={styles}", "data-style-name=\"styles\""
            ),
            encoding="utf-8",
        )
        item["source_digest"] = preview_digest(source_root, item["preview_files"])
        self.write("mockup-manifest.json", mockup_manifest([item]))

        errors = validator.validate_mockups(self.run)
        for token in item["used_tokens"]:
            self.assertIn(
                f"mockups[0] code preview used token must be referenced by preview dependency set: {token}",
                errors,
            )

    def test_code_preview_style_alias_uses_lexical_const_binding(self):
        valid_sources = (
            "const styles: CSSProperties = { background: 'var(--color-surface)', gap: 'var(--space-4)' };\n"
            "export function Screen(){return <main style={styles}><Button /></main>}\n",
            "export function Screen(){const styles: CSSProperties = "
            "{ background: 'var(--color-surface)', gap: 'var(--space-4)' }; "
            "return <main style={styles}><Button /></main>}\n",
        )
        for source in valid_sources:
            with self.subTest(valid=source):
                run, item, source_root = self.code_preview_fixture(mode="project")
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "import '../../src/tokens.css';\n"
                    "import { Button } from '../../src/Button';\n"
                    f"{source}",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))

                self.assertEqual(validator.validate_mockups(self.run), [])

        decoys = (
            "const styles = { background: 'var(--color-surface)', gap: 'var(--space-4)' };\n"
            "export function Screen(){const styles = { background: 'white', gap: '1rem' }; "
            "return <main style={styles}><Button /></main>}\n",
            "function Decoy(){const styles = { background: 'var(--color-surface)', "
            "gap: 'var(--space-4)' }; return styles}\n"
            "const styles = { background: 'white', gap: '1rem' };\n"
            "export function Screen(){return <main style={styles}><Button /></main>}\n",
        )
        for source in decoys:
            with self.subTest(decoy=source):
                run, item, source_root = self.code_preview_fixture(mode="project")
                screen = source_root / item["preview_path"]
                screen.write_text(
                    "import '../../src/tokens.css';\n"
                    "import { Button } from '../../src/Button';\n"
                    f"{source}",
                    encoding="utf-8",
                )
                item["source_digest"] = preview_digest(
                    source_root, item["preview_files"]
                )
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([item]))

                errors = validator.validate_mockups(self.run)
                for token in item["used_tokens"]:
                    self.assertIn(
                        f"mockups[0] code preview used token must be referenced by preview dependency set: {token}",
                        errors,
                    )

    def test_code_preview_sources_are_digest_bound_and_contained(self):
        run, item, source_root = self.code_preview_fixture(mode="project")
        self.write("run.json", run)

        stale = copy.deepcopy(item)
        stale["source_digest"] = "sha256:" + "0" * 64
        self.write("mockup-manifest.json", mockup_manifest([stale]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] code preview source_digest must match current preview_files",
            errors,
        )

        unsafe = copy.deepcopy(item)
        unsafe["token_sources"] = ["../tokens.css"]
        self.write("mockup-manifest.json", mockup_manifest([unsafe]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] code preview token_sources must use safe relative paths",
            errors,
        )

        with tempfile.TemporaryDirectory() as outside_temp:
            outside = Path(outside_temp) / "Outside.tsx"
            outside.write_text("export const outside = true;\n", encoding="utf-8")
            (source_root / "escape.tsx").symlink_to(outside)
            escaped = copy.deepcopy(item)
            escaped["preview_files"].append("escape.tsx")
            self.write("mockup-manifest.json", mockup_manifest([escaped]))
            errors = validator.validate_mockups(self.run)
            self.assertIn(
                "mockups[0] code preview preview file must be contained and existing: escape.tsx",
                errors,
            )

        provider_mismatch = copy.deepcopy(item)
        provider_mismatch["supporting_provider_refs"] = [
            "provider:openai:asset-123"
        ]
        self.write("mockup-manifest.json", mockup_manifest([provider_mismatch]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] code preview supporting_provider_refs require a positive attempt_count",
            errors,
        )

    def test_legacy_provider_image_manifest_remains_valid(self):
        code_run, code_item, _ = self.code_preview_fixture(mode="project")
        self.write("run.json", run_manifest())
        self.write("mockup-manifest.json", mockup_manifest([mockup()]))
        self.assertEqual(validator.validate_mockups(self.run), [])

        zero_attempt = mockup(attempt_count=0)
        self.write("mockup-manifest.json", mockup_manifest([zero_attempt]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] attempt_count must be positive, or zero for initial pending authorization",
            errors,
        )

        unknown_kind = mockup(
            artifact_kind="code-prevew",
            preview_files=["../unsafe.tsx"],
            source_digest="sha256:" + "0" * 64,
            viewport_checks={},
        )
        self.write("mockup-manifest.json", mockup_manifest([unknown_kind]))
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockups[0] artifact_kind must be code-preview when present",
            errors,
        )

        code_run["approved_direction_ids"] = ["a", "d-0"]
        self.write("run.json", code_run)
        mixed = mockup_manifest([mockup("a"), code_item])
        self.assertEqual(mixed["last_generation_direction_id"], "d-0")
        self.write("mockup-manifest.json", mixed)
        errors = validator.validate_mockups(self.run)
        self.assertIn(
            "mockup-manifest audit direction must have a positive attempt_count",
            errors,
        )

    def test_mockups_require_at_least_one_approved_direction(self):
        self.write("run.json", run_manifest(approved_direction_ids=[]))
        self.write("mockup-manifest.json", mockup_manifest([]))

        errors = validator.validate_phase(self.run, "mockups")

        self.assertIn(
            "run.json approved_direction_ids must be a non-empty list",
            errors,
        )

    def test_mockups_reject_invalid_or_duplicate_approved_direction_ids(self):
        self.write("run.json", run_manifest(approved_direction_ids=["a", " ", "a", 42]))
        self.write("mockup-manifest.json", mockup_manifest([]))

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
        self.write("run.json", run_manifest(approved_direction_ids=["a"]))
        self.write(
            "mockup-manifest.json",
            mockup_manifest(
                [
                    {"direction_id": "b", "status": "failed", "attempt_count": 1},
                    {
                        "direction_id": "a",
                        "status": "success",
                        "viewport": 390,
                        "prompt_digest": " ",
                        "output_ref": None,
                        "attempt_count": 1,
                    },
                    {"direction_id": 42, "status": "failed", "attempt_count": 1},
                ]
            ),
        )

        errors = validator.validate_phase(self.run, "mockups")

        self.assertIn("mockups[0] direction_id is not approved: b", errors)
        self.assertIn("mockups[1] viewport must use WIDTHxHEIGHT", errors)
        self.assertIn(
            "mockups[1] prompt_digest must be sha256 plus 64 lowercase hex", errors
        )
        self.assertIn("mockups[1] success requires output_ref", errors)
        self.assertIn("mockups[2] direction_id must be a non-empty string", errors)
        self.assertIn("missing successful mockups for: a", errors)

    def test_mockup_manifest_enforces_budget_uniqueness_status_and_attempts(self):
        cases = (
            (
                run_manifest(generation_budget=5),
                mockup_manifest([mockup("a") for _ in range(100)]),
                "exceeds generation_budget",
            ),
            (
                run_manifest(approved_direction_ids=["a", "b"]),
                mockup_manifest([mockup("a"), mockup("a"), mockup("b")]),
                "duplicate current direction_id: a",
            ),
            (
                run_manifest(),
                mockup_manifest([mockup(status="complete")]),
                "status must be pending, success, or failed",
            ),
            (
                run_manifest(),
                mockup_manifest([mockup(attempt_count=999)]),
                "attempt_count exceeds max_attempts_per_direction",
            ),
        )
        for run, manifest, expected in cases:
            with self.subTest(expected=expected):
                self.write("run.json", run)
                self.write("mockup-manifest.json", manifest)
                errors = validator.validate_phase(self.run, "mockups")
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_mockups_pass_with_explicitly_expanded_budget(self):
        approved = [f"d-{index}" for index in range(8)]
        self.write(
            "run.json",
            run_manifest(
                approved_direction_ids=approved,
                generation_budget=8,
                max_attempts_per_direction=3,
                budget_expansion_approved_at="2026-07-19T12:01:00Z",
            ),
        )
        self.write(
            "mockup-manifest.json",
            mockup_manifest([mockup(identifier, attempt_count=3) for identifier in approved]),
        )

        self.assertEqual(validator.validate_phase(self.run, "mockups"), [])

    def test_mockup_budget_expansion_requires_valid_matching_approval_timestamp(self):
        cases = (
            (
                run_manifest(generation_budget=6),
                "expanded budget requires valid budget_expansion_approved_at",
            ),
            (
                run_manifest(
                    max_attempts_per_direction=3,
                    budget_expansion_approved_at="not-a-timestamp",
                ),
                "expanded budget requires valid budget_expansion_approved_at",
            ),
            (
                run_manifest(
                    budget_expansion_approved_at="2026-07-19T12:01:00Z"
                ),
                "budget_expansion_approved_at requires an expanded budget",
            ),
        )
        for run, expected in cases:
            with self.subTest(expected=expected):
                self.write("run.json", run)
                self.write("mockup-manifest.json", mockup_manifest([mockup()]))
                errors = validator.validate_phase(self.run, "mockups")
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_pending_and_failed_mockups_require_valid_viewport_and_digest(self):
        self.write("run.json", run_manifest(approved_direction_ids=["a", "b"]))
        self.write(
            "mockup-manifest.json",
            mockup_manifest(
                [
                    mockup("a", status="pending", viewport="mobile"),
                    mockup("b", status="failed", prompt_digest="sha256:ABC"),
                ]
            ),
        )

        errors = validator.validate_phase(self.run, "mockups")

        self.assertIn("mockups[0] viewport must use WIDTHxHEIGHT", errors)
        self.assertIn("mockups[1] prompt_digest must be sha256 plus 64 lowercase hex", errors)

    def test_mockup_status_type_error_is_reported_without_cli_traceback(self):
        self.write("run.json", run_manifest())
        self.write("mockup-manifest.json", mockup_manifest([mockup(status=[])]))

        errors = validator.validate_phase(self.run, "mockups")
        self.assertIn("mockups[0] status must be pending, success, or failed", errors)

        result = subprocess.run(
            [
                sys.executable,
                validator.__file__,
                "--run",
                str(self.run),
                "--phase",
                "mockups",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("status must be pending", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_artifact_paths_and_provider_refs_reject_escaping_or_credentials(self):
        bad_reference = reference()
        bad_reference["capture_path"] = "../cookies/session.png"
        self.write("references.json", [bad_reference])
        self.write("evidence.json", [evidence()])
        errors = validator.validate_phase(self.run, "research")
        self.assertTrue(any("capture_path" in error for error in errors), errors)

        self.write("run.json", run_manifest())
        for value in (
            "/tmp/a.png",
            "../a.png",
            "mockups\\a.png",
            "https://user:secret@example.com/a.png",
            "provider:user@secret",
        ):
            with self.subTest(value=value):
                self.write("mockup-manifest.json", mockup_manifest([mockup(output_ref=value)]))
                errors = validator.validate_phase(self.run, "mockups")
                self.assertTrue(any("output_ref" in error for error in errors), errors)

        self.write(
            "mockup-manifest.json",
            mockup_manifest([mockup(output_ref="provider:openai:artifact_abc-123")]),
        )
        self.assertEqual(validator.validate_phase(self.run, "mockups"), [])

    def test_failed_mockup_rejects_an_unsafe_optional_output_ref(self):
        self.write("run.json", run_manifest())
        self.write(
            "mockup-manifest.json",
            mockup_manifest(
                [
                    mockup(
                        status="failed",
                        output_ref="../private/result.png",
                    )
                ]
            ),
        )

        errors = validator.validate_phase(self.run, "mockups")

        self.assertTrue(any("output_ref" in error for error in errors), errors)

    def test_json_artifacts_recursively_reject_secret_keys_and_high_confidence_values(self):
        artifacts = (
            ("research", "references.json", [dict(reference(), api_key="hidden")], "evidence.json", [evidence()]),
            ("directions", "directions.json", [dict(item) for item in distinct_directions()], "evidence.json", [evidence()]),
            ("mockups", "mockup-manifest.json", mockup_manifest([mockup(prompt_digest="Bearer abcdefghijklmnopqrstuvwxyz0123456789")]), "run.json", run_manifest()),
            ("implementation", "implementation.json", {
                "selected_direction_id": "a",
                "mode": "project",
                "preview_path": "src/preview.tsx",
                "verification": {"rendered_viewports": ["390x844"], "checks": {"content": "pass", "overflow": "pass", "accessibility": "pass"}},
                "metadata": {"pairing_token": "hidden"},
            }, "run.json", run_manifest(state="implementation_selected", selected_direction_id="a")),
        )
        for phase, first_name, first_value, second_name, second_value in artifacts:
            with self.subTest(phase=phase):
                value = copy.deepcopy(first_value)
                if phase == "directions":
                    value[0]["concept"] = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
                self.write(first_name, value)
                self.write(second_name, second_value)
                errors = validator.validate_phase(self.run, phase)
                self.assertTrue(any("secret-like" in error for error in errors), errors)

    def test_realistic_secret_formats_and_provider_hints_are_rejected(self):
        credentials = (
            "xoxb-123456789012-abcdefghijklmnopqrstuvwx",
            "github_pat_11AAabcdefghijklmnopqrstuvwxyz012345",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "sk_live_abcdefghijklmnopqrstuvwxyz",
            "AIzaSyA1234567890abcdefghijklmnopqrst",
            "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
            "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        )
        for credential in credentials:
            with self.subTest(credential=credential[:10]):
                item = reference()
                item["relevance"] = credential
                self.write("references.json", [item])
                self.write("evidence.json", [evidence()])
                errors = validator.validate_phase(self.run, "research")
                self.assertTrue(any("secret-like value" in error for error in errors), errors)

        self.write("run.json", run_manifest())
        self.write(
            "mockup-manifest.json",
            mockup_manifest(
                [
                    mockup(output_ref="openai:sk-proj-abcdefghijklmnopqrstuvwxyz")
                ]
            ),
        )
        errors = validator.validate_phase(self.run, "mockups")
        self.assertTrue(any("secret-like value" in error for error in errors), errors)

        self.write(
            "run.json",
            run_manifest(state="implementation_selected", selected_direction_id="a"),
        )
        value = {
            "selected_direction_id": "a",
            "mode": "project",
            "preview_path": "src/preview.tsx",
            "verification": {
                "rendered_viewports": ["390x844"],
                "checks": {
                    "content": "pass",
                    "overflow": "pass",
                    "accessibility": "pass",
                },
            },
            "note": "sk_live_abcdefghijklmnopqrstuvwxyz",
        }
        self.write("implementation.json", value)
        errors = validator.validate_phase(self.run, "implementation")
        self.assertTrue(any("secret-like value" in error for error in errors), errors)

    def test_bearer_prose_and_placeholders_pass_but_realistic_credential_fails(self):
        for summary in (
            "Bearer <token>",
            "Use a Bearer token for the request",
            "Bearer authentication-credentials-should-be-protected",
            "Bearer token-based-authentication-for-api-requests",
        ):
            with self.subTest(summary=summary):
                item = evidence()
                item["summary"] = summary
                self.write("references.json", [reference()])
                self.write("evidence.json", [item])
                self.assertEqual(validator.validate_phase(self.run, "research"), [])

        item = evidence()
        item["summary"] = "Bearer abcdefghijklmnopqrstuvwxyz0123456789.ABCDEF"
        self.write("references.json", [reference()])
        self.write("evidence.json", [item])
        errors = validator.validate_phase(self.run, "research")
        self.assertTrue(any("secret-like value" in error for error in errors), errors)

        item["summary"] = (
            "Bearer eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        self.write("evidence.json", [item])
        errors = validator.validate_phase(self.run, "research")
        self.assertTrue(any("secret-like value" in error for error in errors), errors)

    def test_normal_prose_about_passwords_is_not_a_secret(self):
        item = evidence()
        item["summary"] = "Passwords should remain private and error text should be clear."
        self.write("references.json", [reference()])
        self.write("evidence.json", [item])
        self.assertEqual(validator.validate_phase(self.run, "research"), [])

    def test_implementation_matches_selection_and_records_render_checks(self):
        self.write(
            "run.json",
            run_manifest(state="implementation_selected", selected_direction_id="a", approved_direction_ids=["a", "b"]),
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
                "mode": "standalone",
                "preview_path": "standalone/src/main.tsx",
                "preview_files": [
                    "standalone/package.json",
                    "standalone/index.html",
                    "standalone/vite.config.ts",
                    "standalone/tsconfig.json",
                    "standalone/src/main.tsx",
                    "standalone/src/App.tsx",
                ],
                "preview_route": "/preview",
                "verification": {
                    "rendered_viewports": ["390x844"],
                    "checks": {
                        "content": "pass",
                        "overflow": "pass",
                        "accessibility": "pass",
                    },
                    "viewport_checks": {
                        "390x844": {
                            "screenshot_ref": "evidence/390x844.png",
                            "source_digest": "replace-after-files",
                            "content": "pass",
                            "overflow": "pass",
                            "accessibility": "pass",
                            "interaction": "pass",
                            "required_content": {"Order summary": "pass"},
                            "required_interactions": {"Edit order": "pass"},
                        }
                    },
                },
            },
        )
        standalone = self.run / "standalone" / "src"
        standalone.mkdir(parents=True)
        (self.run / "standalone" / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"dev": "vite", "build": "vite build"},
                    "dependencies": {"react": "1", "react-dom": "1"},
                    "devDependencies": {
                        "vite": "1",
                        "typescript": "1",
                        "@vitejs/plugin-react": "1",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.run / "standalone" / "index.html").write_text(
            '<div id="root"></div><script type="module" src="/src/main.tsx"></script>',
            encoding="utf-8",
        )
        (self.run / "standalone" / "vite.config.ts").write_text(
            "import { defineConfig } from 'vite';\n"
            "import react from '@vitejs/plugin-react';\n"
            "export default defineConfig({plugins:[react()]})",
            encoding="utf-8",
        )
        (self.run / "standalone" / "tsconfig.json").write_text(
            '{"compilerOptions":{"jsx":"react-jsx","module":"ESNext"}}',
            encoding="utf-8",
        )
        (standalone / "main.tsx").write_text(
            "import React from 'react';\n"
            "import {createRoot} from 'react-dom/client';\n"
            "import App from './App';\n"
            "createRoot(document.getElementById('root')!).render(<App/>);",
            encoding="utf-8",
        )
        (standalone / "App.tsx").write_text(
            "export default function App(){return <main>Preview</main>}", encoding="utf-8"
        )
        evidence_dir = self.run / "evidence"
        evidence_dir.mkdir()
        write_png(evidence_dir / "390x844.png", 390, 844)
        implementation_value = json.loads((self.run / "implementation.json").read_text())
        implementation_value["verification"]["viewport_checks"]["390x844"][
            "source_digest"
        ] = preview_digest(self.run, implementation_value["preview_files"])
        self.write("implementation.json", implementation_value)
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
            run_manifest(state="implementation_selected", selected_direction_id="a", approved_direction_ids=["a"]),
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
            run_manifest(state="implementation_selected", selected_direction_id="b", approved_direction_ids=["a"]),
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
            run_manifest(state="implementation_selected", selected_direction_id="a", approved_direction_ids=["a"]),
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
            run_manifest(state="implementation_selected", selected_direction_id="a", approved_direction_ids=["a"]),
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
            run_manifest(state="implementation_selected", selected_direction_id="a", approved_direction_ids=["a"]),
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

        self.assertTrue(any("implementation rendered_viewports" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
