import inspect
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from design_explorer_import import load_script_module
from test_preview_evidence import preview_digest, write_png


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


def implementation(identifier="d-0", source_digest=None):
    source_digest = source_digest or preview_digest(
        Path(__file__).parent,
        [],
    )
    return {
        "selected_direction_id": identifier,
        "mode": "project",
        "preview_path": "previews/Checkout.tsx",
        "preview_files": [
            "previews/App.tsx",
            "previews/Checkout.tsx",
            "previews/routes.json",
        ],
        "preview_route": "/design-explorer/checkout",
        "route_registry_path": "previews/routes.json",
        "route_consumer_path": "previews/App.tsx",
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
                    "source_digest": source_digest,
                    "content": "pass",
                    "overflow": "pass",
                    "accessibility": "pass",
                    "interaction": "pass",
                    "required_content": {"Order summary": "pass"},
                    "required_interactions": {"Edit order": "pass"},
                }
            },
        },
    }


class RunStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_dir = self.root / "project"
        (self.project_dir / "src").mkdir(parents=True)
        (self.project_dir / "src" / "App.tsx").write_text(
            "export const production = true;",
            encoding="utf-8",
        )
        (self.project_dir / "previews").mkdir()
        (self.project_dir / "previews" / "App.tsx").write_text(
            "import routes from './routes.json';\n"
            "import { Preview } from './Checkout';\n"
            "export const resolve = (path: string) => routes[path] ? Preview : null;",
            encoding="utf-8",
        )
        (self.project_dir / "previews" / "Checkout.tsx").write_text(
            "export const Preview = () => <main id='checkout-shell'>Preview</main>;",
            encoding="utf-8",
        )
        (self.project_dir / "previews" / "routes.json").write_text(
            '{"/design-explorer/checkout":{"component_path":"previews/Checkout.tsx","shell_id":"checkout-shell"}}',
            encoding="utf-8",
        )
        self.run_dir = run_state.init_run(
            self.root,
            "checkout",
            project_path=str(self.project_dir),
            now="2026-07-19T12:00:00Z",
            run_id="run-checkout",
            target_viewports=["390x844"],
            required_content=["Order summary"],
            required_interactions=["Edit order"],
            production_paths=["src/App.tsx"],
        )
        screenshot = self.run_dir / "evidence" / "390x844.png"
        screenshot.parent.mkdir()
        write_png(screenshot, 390, 844)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value="ok"):
        path = self.run_dir / name
        if isinstance(value, (dict, list)):
            path.write_text(json.dumps(value), encoding="utf-8")
        else:
            path.write_text(value, encoding="utf-8")

    def set_state(self, state):
        manifest = json.loads((self.run_dir / "run.json").read_text())
        if state != "initialized" and "brief_constraints" not in manifest:
            constraints = run_state._brief_constraints(manifest)
            manifest["brief_constraints"] = constraints
            manifest["brief_constraints_digest"] = run_state._constraints_digest(
                constraints
            )
            manifest["brief_locked_at"] = "2026-07-19T12:00:00Z"
        if run_state.STATES.index(state) >= run_state.STATES.index(
            "directions_approved"
        ):
            self.write_research()
            self.write_directions()
            manifest["approved_direction_ids"] = manifest["approved_direction_ids"] or [
                "d-0"
            ]
        manifest["state"] = state
        self.write("run.json", manifest)

    def implementation(self, identifier="d-0"):
        files = [
            "previews/App.tsx",
            "previews/Checkout.tsx",
            "previews/routes.json",
        ]
        return implementation(identifier, preview_digest(self.project_dir, files))

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

    def _mockup(self, identifier):
        prompt = self.run_dir / "prompts" / f"{identifier}.txt"
        prompt.parent.mkdir(exist_ok=True)
        prompt.write_text(f"prompt for {identifier}\n", encoding="utf-8")
        output = self.run_dir / "mockups" / f"{identifier}.png"
        write_png(output, 390, 844)
        return {
            "direction_id": identifier,
            "status": "success",
            "viewport": "390x844",
            "prompt_ref": prompt.relative_to(self.run_dir).as_posix(),
            "prompt_digest": "sha256:" + hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "output_kind": "local",
            "output_ref": f"mockups/{identifier}.png",
            "attempt_count": 1,
        }

    def write_mockups(self, identifiers, attempt_count=1, status="success"):
        values = [self._mockup(identifier) for identifier in identifiers]
        for value in values:
            value["attempt_count"] = attempt_count
            value["status"] = status
            if status != "success":
                value.pop("output_kind", None)
                value.pop("output_ref", None)
        self.write(
            "mockup-manifest.json",
            {"mockups": values},
        )
        manifest = json.loads((self.run_dir / "run.json").read_text())
        manifest["generation_attempts_used"] = sum(
            value["attempt_count"] for value in values
        )
        if manifest["generation_attempts_used"]:
            manifest["last_generation_authorized_at"] = "2026-07-19T12:00:00Z"
            manifest["last_generation_authorized_direction_id"] = identifiers[-1]
        else:
            manifest["last_generation_authorized_at"] = None
            manifest["last_generation_authorized_direction_id"] = None
        self.write("run.json", manifest)

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
        self.assertEqual(manifest["generation_attempts_used"], 0)
        self.assertIsNone(manifest["last_generation_authorized_at"])
        self.assertIsNone(manifest["last_generation_authorized_direction_id"])
        self.assertEqual(manifest["target_viewports"], ["390x844"])
        self.assertEqual(manifest["required_content"], ["Order summary"])
        self.assertEqual(manifest["required_interactions"], ["Edit order"])
        self.assertEqual(manifest["production_paths"], ["src/App.tsx"])

    def test_init_normalizes_and_deduplicates_evidence_requirements(self):
        run_dir = run_state.init_run(
            self.root,
            "profile",
            run_id="run-profile",
            target_viewports=[" 390x844 ", "390x844", "1440x900"],
            required_content=[" Profile name ", "Profile name", "Avatar"],
            required_interactions=[" Save ", "Save"],
            production_paths=[" src/App.tsx ", "src/App.tsx"],
        )

        manifest = run_state.load_run(run_dir)
        self.assertEqual(manifest["target_viewports"], ["390x844", "1440x900"])
        self.assertEqual(manifest["required_content"], ["Profile name", "Avatar"])
        self.assertEqual(manifest["required_interactions"], ["Save"])
        self.assertEqual(manifest["production_paths"], ["src/App.tsx"])

    def test_init_rejects_invalid_viewports_and_unsafe_production_paths(self):
        for viewport in ("banana", "0x844", "390X844", "10001x844"):
            with self.subTest(viewport=viewport), self.assertRaisesRegex(
                ValueError, "viewport"
            ):
                run_state.init_run(
                    self.root,
                    "invalid",
                    run_id=f"invalid-{viewport.replace('/', '-')}",
                    target_viewports=[viewport],
                )
        for production_path in (
            "../App.tsx",
            "/tmp/App.tsx",
            "src\\App.tsx",
            "src/App:secret.tsx",
            "~/App.tsx",
        ):
            with self.subTest(production_path=production_path), self.assertRaisesRegex(
                ValueError, "production_path"
            ):
                run_state.init_run(
                    self.root,
                    "invalid-path",
                    run_id="invalid-path-" + str(abs(hash(production_path))),
                    production_paths=[production_path],
                )

    def test_invalid_init_does_not_leave_a_partial_run_directory(self):
        expected = self.root / "partial-run"
        with self.assertRaisesRegex(ValueError, "viewport"):
            run_state.init_run(
                self.root,
                "partial",
                run_id="partial-run",
                target_viewports=["banana"],
            )
        self.assertFalse(expected.exists())

    def test_load_rejects_malformed_requirement_collections_as_value_errors(self):
        base = run_state.load_run(self.run_dir)
        for key in (
            "target_viewports",
            "required_content",
            "required_interactions",
            "production_paths",
        ):
            with self.subTest(key=key):
                manifest = dict(base)
                manifest[key] = 42
                self.write("run.json", manifest)
                with self.assertRaisesRegex(ValueError, key):
                    run_state.load_run(self.run_dir)

    def test_post_brief_manifest_cannot_drop_viewports_or_content(self):
        manifest = run_state.load_run(self.run_dir)
        manifest["state"] = "directions_approved"
        for key in ("target_viewports", "required_content"):
            with self.subTest(key=key):
                tampered = dict(manifest)
                tampered[key] = []
                self.write("run.json", tampered)
                with self.assertRaisesRegex(ValueError, key):
                    run_state.load_run(self.run_dir)
                self.assertFalse(run_state.image_generation_allowed(self.run_dir, "d-0"))

    def test_brief_gate_requires_machine_readable_targets(self):
        run_dir = run_state.init_run(
            self.root, "empty", run_id="run-empty"
        )
        (run_dir / "brief.md").write_text("# Brief", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "target_viewports"):
            run_state.transition_run(run_dir, "brief_ready")

        manifest = run_state.load_run(run_dir)
        manifest["target_viewports"] = ["390x844"]
        (run_dir / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "required_content"):
            run_state.transition_run(run_dir, "brief_ready")

    def test_empty_interactions_require_explicit_none_in_brief(self):
        run_dir = run_state.init_run(
            self.root,
            "static",
            run_id="run-static",
            target_viewports=["390x844"],
            required_content=["Headline"],
        )
        (run_dir / "brief.md").write_text("# Static brief", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "interactive requirements"):
            run_state.transition_run(run_dir, "brief_ready")
        (run_dir / "brief.md").write_text(
            "# Static brief\n\nInteractive requirements: none", encoding="utf-8"
        )
        self.assertEqual(
            run_state.transition_run(run_dir, "brief_ready")["state"], "brief_ready"
        )

    def test_generation_authorization_fails_closed_and_tracks_exact_state(self):
        self.assertFalse(run_state.image_generation_allowed(self.run_dir, "d-0"))
        self.advance_to_pending()
        self.assertFalse(run_state.image_generation_allowed(self.run_dir, "d-0"))
        run_state.transition_run(
            self.run_dir, "directions_approved", approved_direction_ids=["d-0"]
        )
        self.write_mockups(["d-0"], attempt_count=0, status="pending")
        self.assertTrue(run_state.image_generation_allowed(self.run_dir, "d-0"))
        self.write_mockups(["d-0"])
        run_state.transition_run(self.run_dir, "mockups_generated")
        self.assertFalse(run_state.image_generation_allowed(self.run_dir, "d-0"))

        (self.run_dir / "run.json").write_text("{tampered", encoding="utf-8")
        self.assertFalse(run_state.image_generation_allowed(self.run_dir, "d-0"))

    def test_can_generate_cli_returns_boolean_and_status_without_traceback(self):
        for expected, returncode in (("false", 1),):
            result = subprocess.run(
                [sys.executable, run_state.__file__, "can-generate", "--run", str(self.run_dir), "--direction", "d-0"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, returncode)
            self.assertEqual(result.stdout.strip(), expected)
            self.assertNotIn("Traceback", result.stderr)

        self.advance_to_pending()
        run_state.transition_run(
            self.run_dir, "directions_approved", approved_direction_ids=["d-0"]
        )
        self.write_mockups(["d-0"], attempt_count=0, status="pending")
        result = subprocess.run(
            [sys.executable, run_state.__file__, "can-generate", "--run", str(self.run_dir), "--direction", "d-0"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true")

        (self.run_dir / "run.json").write_text("{tampered", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, run_state.__file__, "can-generate", "--run", str(self.run_dir), "--direction", "d-0"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "false")
        self.assertNotIn("Traceback", result.stderr)

    def test_public_interfaces_expose_promised_annotations(self):
        init_signature = inspect.signature(run_state.init_run)
        self.assertEqual(init_signature.parameters["project_path"].annotation, str | None)
        self.assertEqual(init_signature.parameters["now"].annotation, str | None)
        self.assertEqual(init_signature.parameters["run_id"].annotation, str | None)
        for name in (
            "target_viewports",
            "required_content",
            "required_interactions",
            "production_paths",
        ):
            self.assertEqual(init_signature.parameters[name].annotation, list[str] | None)
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

        generation_signature = inspect.signature(run_state.image_generation_allowed)
        self.assertEqual(generation_signature.parameters["run_dir"].annotation, Path)
        self.assertEqual(generation_signature.parameters["direction_id"].annotation, str)
        self.assertEqual(generation_signature.return_annotation, bool)
        authorization_signature = inspect.signature(run_state.authorize_generation)
        self.assertEqual(authorization_signature.parameters["run_dir"].annotation, Path)
        self.assertEqual(authorization_signature.parameters["direction_id"].annotation, str)
        self.assertEqual(authorization_signature.parameters["now"].annotation, str | None)
        self.assertEqual(authorization_signature.return_annotation, dict)

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
        self.write_mockups(["d-0"])
        manifest = json.loads((self.run_dir / "run.json").read_text())
        manifest["approved_direction_ids"] = ["d-0"]
        manifest["selected_direction_id"] = "d-0"
        self.write("run.json", manifest)
        self.write("implementation.json", self.implementation())
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
        self.write_mockups(["d-0"])
        archived_expected = json.loads(
            (self.run_dir / "mockup-manifest.json").read_text()
        )
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["d-0"]
        manifest["selected_direction_id"] = "d-0"
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
        self.assertEqual(revised["generation_attempts_used"], 0)
        self.assertIsNone(revised["last_generation_authorized_at"])
        self.assertIsNone(revised["last_generation_authorized_direction_id"])
        self.assertNotIn("budget_expansion_approved_at", revised)
        self.assertFalse((self.run_dir / "mockup-manifest.json").exists())
        self.assertTrue(
            (self.run_dir / "mockup-manifest.revision-1.json").is_file()
        )
        self.assertEqual(
            json.loads(
                (self.run_dir / "mockup-manifest.revision-1.json").read_text()
            ),
            archived_expected,
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
        self.write_mockups(["d-0"])
        current = json.loads((self.run_dir / "mockup-manifest.json").read_text())
        archived = {"mockups": [{"direction_id": "archived"}]}
        self.write("mockup-manifest.revision-1.json", archived)
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["d-0"]
        self.write("run.json", manifest)

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
        self.assertEqual(
            json.loads((self.run_dir / "run.json").read_text())["state"],
            "mockups_generated",
        )

    def test_revision_rolls_back_archive_when_run_write_fails(self):
        self.set_state("mockups_generated")
        self.write_mockups(["d-0"])
        current = json.loads((self.run_dir / "mockup-manifest.json").read_text())
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["d-0"]
        self.write("run.json", manifest)

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
        self.assertEqual(
            json.loads((self.run_dir / "run.json").read_text())["state"],
            "mockups_generated",
        )

    def test_revision_revalidates_pending_prerequisites_after_archive(self):
        self.set_state("mockups_generated")
        self.write_mockups(["d-0"])
        before_run = (self.run_dir / "run.json").read_bytes()
        before_mockups = (self.run_dir / "mockup-manifest.json").read_bytes()
        real_validate = run_state._validate_phases

        def fail_after_archive(run_dir, phases):
            if not (Path(run_dir) / "mockup-manifest.json").exists():
                raise ValueError("post-archive prerequisite failure")
            return real_validate(run_dir, phases)

        with mock.patch.object(
            run_state, "_validate_phases", side_effect=fail_after_archive
        ):
            with self.assertRaisesRegex(ValueError, "post-archive"):
                run_state.revise_run(self.run_dir, "Create a variation")

        self.assertEqual((self.run_dir / "run.json").read_bytes(), before_run)
        self.assertEqual(
            (self.run_dir / "mockup-manifest.json").read_bytes(), before_mockups
        )
        self.assertFalse(
            (self.run_dir / "mockup-manifest.revision-1.json").exists()
        )

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

    def test_load_rejects_unaudited_or_stale_budget_expansion(self):
        base = json.loads((self.run_dir / "run.json").read_text())
        cases = (
            (
                dict(base, generation_budget=6),
                "expanded budget requires valid budget_expansion_approved_at",
            ),
            (
                dict(
                    base,
                    max_attempts_per_direction=3,
                    budget_expansion_approved_at="yesterday",
                ),
                "expanded budget requires valid budget_expansion_approved_at",
            ),
            (
                dict(
                    base,
                    budget_expansion_approved_at="2026-07-19T12:01:00Z",
                ),
                "budget_expansion_approved_at requires an expanded budget",
            ),
        )
        for manifest, expected in cases:
            with self.subTest(expected=expected):
                self.write("run.json", manifest)
                before = (self.run_dir / "run.json").read_bytes()
                with self.assertRaisesRegex(ValueError, expected):
                    run_state.load_run(self.run_dir)
                with self.assertRaisesRegex(ValueError, expected):
                    run_state.transition_run(self.run_dir, "brief_ready")
                self.assertEqual((self.run_dir / "run.json").read_bytes(), before)

    def test_revision_validates_mockups_before_archiving_or_mutating(self):
        cases = (
            {**self._mockup("d-1")},
            {**self._mockup("d-0"), "attempt_count": 999},
            {**self._mockup("d-0"), "output_ref": "../private.png"},
            {**self._mockup("d-0"), "status": "complete"},
        )
        for index, invalid in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                run_dir = run_state.init_run(
                    Path(temp),
                    "revision",
                    now="2026-07-19T12:00:00Z",
                    run_id=f"revision-{index}",
                    target_viewports=["390x844"],
                    required_content=["Order summary"],
                    required_interactions=["Edit order"],
                )
                manifest = run_state.load_run(run_dir)
                constraints = run_state._brief_constraints(manifest)
                manifest["brief_constraints"] = constraints
                manifest["brief_constraints_digest"] = run_state._constraints_digest(
                    constraints
                )
                manifest["brief_locked_at"] = "2026-07-19T12:00:00Z"
                manifest["state"] = "mockups_generated"
                manifest["approved_direction_ids"] = ["d-0"]
                (run_dir / "references.json").write_text(
                    json.dumps([reference()]), encoding="utf-8"
                )
                (run_dir / "evidence.json").write_text(
                    json.dumps([evidence()]), encoding="utf-8"
                )
                (run_dir / "design-evidence.md").write_text(
                    "# Evidence", encoding="utf-8"
                )
                (run_dir / "reference-board.md").write_text(
                    "# Board", encoding="utf-8"
                )
                (run_dir / "directions.json").write_text(
                    json.dumps([direction(f"d-{item}", item) for item in range(5)]),
                    encoding="utf-8",
                )
                (run_dir / "mood-directions.md").write_text(
                    "# Directions", encoding="utf-8"
                )
                (run_dir / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
                (run_dir / "mockup-manifest.json").write_text(
                    json.dumps({"mockups": [invalid]}), encoding="utf-8"
                )
                run_before = (run_dir / "run.json").read_bytes()
                mockups_before = (run_dir / "mockup-manifest.json").read_bytes()

                with self.assertRaisesRegex(ValueError, "mockups validation failed"):
                    run_state.revise_run(run_dir, "Try a revision")

                self.assertEqual((run_dir / "run.json").read_bytes(), run_before)
                self.assertEqual(
                    (run_dir / "mockup-manifest.json").read_bytes(), mockups_before
                )
                self.assertFalse(
                    (run_dir / "mockup-manifest.revision-1.json").exists()
                )

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
        self.assertEqual(
            json.loads((self.run_dir / "run.json").read_text())["state"],
            "mockups_generated",
        )

        self.write_mockups(["d-0"])
        run_state.transition_run(
            self.run_dir, "implementation_selected", selected_direction_id="d-0"
        )
        self.write("implementation.json", {})
        with self.assertRaisesRegex(ValueError, "implementation validation failed"):
            run_state.transition_run(self.run_dir, "prototype_ready")
        self.assertEqual(run_state.load_run(self.run_dir)["state"], "implementation_selected")

        self.write("implementation.json", self.implementation())
        run_state.transition_run(self.run_dir, "prototype_ready")
        self.write("implementation.json", {})
        with self.assertRaisesRegex(ValueError, "implementation validation failed"):
            run_state.transition_run(
                self.run_dir, "integrated", integration_approved=True
            )
        manifest = json.loads((self.run_dir / "run.json").read_text())
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

    def test_direction_approval_returning_to_defaults_clears_stale_expansion(self):
        self.advance_to_pending()
        manifest = run_state.load_run(self.run_dir)
        manifest["generation_budget"] = 6
        manifest["max_attempts_per_direction"] = 3
        manifest["budget_expansion_approved_at"] = "2026-07-19T12:03:00Z"
        self.write("run.json", manifest)

        approved = run_state.transition_run(
            self.run_dir,
            "directions_approved",
            approved_direction_ids=["d-0"],
            generation_budget=5,
            max_attempts_per_direction=2,
        )

        self.assertEqual(approved["generation_budget"], 5)
        self.assertEqual(approved["max_attempts_per_direction"], 2)
        self.assertEqual(approved["generation_attempts_used"], 0)
        self.assertIsNone(approved["last_generation_authorized_at"])
        self.assertIsNone(approved["last_generation_authorized_direction_id"])
        self.assertNotIn("budget_expansion_approved_at", approved)
        reloaded = run_state.load_run(self.run_dir)
        self.assertEqual(reloaded, approved)

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
        self.write("implementation.json", self.implementation())
        self.assertEqual(run_state.transition_run(self.run_dir, "prototype_ready")["state"], "prototype_ready")
        integrated = run_state.transition_run(
            self.run_dir,
            "integrated",
            integration_approved=True,
            now="2026-07-19T12:05:00Z",
        )
        self.assertEqual(integrated["state"], "integrated")
        self.assertEqual(integrated["integration_approved_at"], "2026-07-19T12:05:00Z")

    def test_evidence_tamper_fails_closed_at_every_later_state(self):
        def assert_tamper_denied(transition=None):
            original = json.loads((self.run_dir / "evidence.json").read_text())
            tampered = json.loads(json.dumps(original))
            tampered[0]["source_url"] = "not-a-url"
            tampered[0]["publisher_or_author"] = " "
            self.write("evidence.json", tampered)
            before = (self.run_dir / "run.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "research validation failed"):
                run_state.load_run(self.run_dir)
            self.assertFalse(run_state.image_generation_allowed(self.run_dir, "d-0"))
            with self.assertRaisesRegex(ValueError, "research validation failed"):
                run_state.authorize_generation(self.run_dir, "d-0")
            if transition is not None:
                with self.assertRaisesRegex(ValueError, "research validation failed"):
                    transition()
            self.assertEqual((self.run_dir / "run.json").read_bytes(), before)
            self.write("evidence.json", original)

        self.advance_to_pending()
        assert_tamper_denied(
            lambda: run_state.transition_run(
                self.run_dir,
                "directions_approved",
                approved_direction_ids=["d-0"],
            )
        )
        run_state.transition_run(
            self.run_dir, "directions_approved", approved_direction_ids=["d-0"]
        )
        self.write_mockups(["d-0"], attempt_count=0, status="pending")
        assert_tamper_denied()
        self.write_mockups(["d-0"])
        run_state.transition_run(self.run_dir, "mockups_generated")
        assert_tamper_denied(
            lambda: run_state.transition_run(
                self.run_dir,
                "implementation_selected",
                selected_direction_id="d-0",
            )
        )
        run_state.transition_run(
            self.run_dir, "implementation_selected", selected_direction_id="d-0"
        )
        self.write("implementation.json", self.implementation())
        assert_tamper_denied(
            lambda: run_state.transition_run(self.run_dir, "prototype_ready")
        )
        run_state.transition_run(self.run_dir, "prototype_ready")
        assert_tamper_denied(
            lambda: run_state.transition_run(
                self.run_dir, "integrated", integration_approved=True
            )
        )
        run_state.transition_run(
            self.run_dir, "integrated", integration_approved=True
        )
        assert_tamper_denied()

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
        self.write_mockups(["d-0"])
        manifest = run_state.load_run(self.run_dir)
        manifest["approved_direction_ids"] = ["d-0"]
        self.write("run.json", manifest)

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
        self.write_mockups(["d-0"])
        manifest = json.loads((self.run_dir / "run.json").read_text())
        manifest["approved_direction_ids"] = ["d-0"]
        manifest["selected_direction_id"] = "d-0"
        self.write("run.json", manifest)
        self.write("implementation.json", self.implementation())

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
