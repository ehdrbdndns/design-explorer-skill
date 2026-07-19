import copy
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from design_explorer_import import load_script_module


validator = load_script_module("validate_run_evidence", "design-explorer/scripts/validate_run.py")


def write_png(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, level=9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def run_manifest(project_path: str | None, production_paths=None):
    return {
        "schema_version": 2,
        "run_id": "run-checkout",
        "slug": "checkout",
        "state": "implementation_selected",
        "created_at": "2026-07-19T12:00:00Z",
        "updated_at": "2026-07-19T12:00:00Z",
        "project_path": project_path,
        "approved_direction_ids": ["a"],
        "selected_direction_id": "a",
        "revision_count": 0,
        "generation_budget": 5,
        "max_attempts_per_direction": 2,
        "target_viewports": ["390x844", "1440x900"],
        "required_content": ["Order summary", "Total"],
        "required_interactions": ["Edit order"],
        "production_paths": list(production_paths or []),
    }


def viewport_check(viewport: str):
    return {
        "screenshot_ref": f"evidence/{viewport}.png",
        "content": "pass",
        "overflow": "pass",
        "accessibility": "pass",
        "interaction": "pass",
        "required_content": {"Order summary": "pass", "Total": "pass"},
        "required_interactions": {"Edit order": "pass"},
    }


class PreviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.project = self.root / "project"
        (self.project / "src").mkdir(parents=True)
        (self.project / "src" / "App.tsx").write_text("production", encoding="utf-8")
        (self.project / "previews").mkdir()
        (self.project / "previews" / "Checkout.tsx").write_text("preview", encoding="utf-8")
        for viewport in ("390x844", "1440x900"):
            width, height = (int(part) for part in viewport.split("x"))
            write_png(self.run / f"evidence/{viewport}.png", width, height)
        self.manifest = run_manifest(str(self.project), ["src/App.tsx"])
        self.implementation = {
            "selected_direction_id": "a",
            "mode": "project",
            "preview_path": "previews/Checkout.tsx",
            "preview_files": ["previews/Checkout.tsx"],
            "preview_route": "/design-explorer/checkout",
            "verification": {
                "rendered_viewports": ["390x844", "1440x900"],
                "checks": {
                    "content": "pass",
                    "overflow": "pass",
                    "accessibility": "pass",
                },
                "viewport_checks": {
                    viewport: viewport_check(viewport)
                    for viewport in ("390x844", "1440x900")
                },
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, manifest=None, implementation=None):
        (self.run / "run.json").write_text(
            json.dumps(manifest or self.manifest), encoding="utf-8"
        )
        (self.run / "implementation.json").write_text(
            json.dumps(implementation or self.implementation), encoding="utf-8"
        )
        return validator.validate_phase(self.run, "implementation")

    def test_project_preview_requires_complete_machine_verified_evidence(self):
        self.assertEqual(self.validate(), [])

    def test_standalone_preview_is_scoped_to_run_and_requires_vite_entry(self):
        standalone = self.run / "standalone"
        (standalone / "src").mkdir(parents=True)
        (standalone / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
        (standalone / "src" / "main.tsx").write_text("entry", encoding="utf-8")
        manifest = run_manifest(None)
        implementation = copy.deepcopy(self.implementation)
        implementation.update(
            mode="standalone",
            preview_path="standalone/src/main.tsx",
            preview_files=["standalone/package.json", "standalone/src/main.tsx"],
        )
        self.assertEqual(self.validate(manifest, implementation), [])

    def test_aggregate_checks_cannot_substitute_for_viewport_evidence(self):
        implementation = copy.deepcopy(self.implementation)
        implementation["verification"].pop("viewport_checks")
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("viewport_checks" in error for error in errors), errors)

    def test_rendered_viewports_must_exactly_match_unique_run_targets(self):
        for rendered in (
            ["390x844"],
            ["390x844", "390x844"],
            ["1440x900", "390x844"],
            ["banana", "1440x900"],
        ):
            with self.subTest(rendered=rendered):
                implementation = copy.deepcopy(self.implementation)
                implementation["verification"]["rendered_viewports"] = rendered
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("rendered_viewports" in error for error in errors), errors)

    def test_each_viewport_requires_exact_item_maps_and_pass_statuses(self):
        mutations = (
            lambda check: check["required_content"].pop("Total"),
            lambda check: check["required_content"].update({"Extra": "pass"}),
            lambda check: check["required_interactions"].clear(),
            lambda check: check.update(interaction="pending"),
            lambda check: check["required_content"].update({"Total": "fail"}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                implementation = copy.deepcopy(self.implementation)
                mutate(implementation["verification"]["viewport_checks"]["390x844"])
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("390x844" in error for error in errors), errors)

    def test_screenshot_must_be_safe_existing_png_with_exact_dimensions(self):
        cases = (
            ("../390x844.png", None),
            ("evidence/missing.png", None),
            ("evidence/not-png.png", b"not png"),
        )
        for screenshot_ref, payload in cases:
            with self.subTest(screenshot_ref=screenshot_ref):
                implementation = copy.deepcopy(self.implementation)
                implementation["verification"]["viewport_checks"]["390x844"]["screenshot_ref"] = screenshot_ref
                if payload is not None:
                    (self.run / screenshot_ref).write_bytes(payload)
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("screenshot" in error for error in errors), errors)

        write_png(self.run / "evidence/wrong.png", 391, 844)
        implementation = copy.deepcopy(self.implementation)
        implementation["verification"]["viewport_checks"]["390x844"]["screenshot_ref"] = "evidence/wrong.png"
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("dimensions" in error for error in errors), errors)

    def test_project_paths_must_exist_and_preview_must_not_overlap_production(self):
        cases = []
        missing = copy.deepcopy(self.implementation)
        missing["preview_files"] = ["previews/missing.tsx"]
        missing["preview_path"] = "previews/missing.tsx"
        cases.append((self.manifest, missing, "existing file"))

        overlap_manifest = copy.deepcopy(self.manifest)
        overlap_manifest["production_paths"] = ["previews"]
        cases.append((overlap_manifest, self.implementation, "production_path"))

        missing_production_manifest = copy.deepcopy(self.manifest)
        missing_production_manifest["production_paths"] = ["src/Missing.tsx"]
        cases.append(
            (missing_production_manifest, self.implementation, "production_path must exist")
        )

        unsafe = copy.deepcopy(self.implementation)
        unsafe["preview_path"] = "../Checkout.tsx"
        unsafe["preview_files"] = ["../Checkout.tsx"]
        cases.append((self.manifest, unsafe, "safe project-relative"))

        for manifest, implementation, expected in cases:
            with self.subTest(expected=expected):
                errors = self.validate(manifest, implementation)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_preview_route_is_a_normalized_absolute_url_path(self):
        for route in (
            "preview",
            "//preview",
            "/a//b",
            "/a/../b",
            "/a/%2e%2e/b",
            "/preview?q=1",
            "/preview#x",
        ):
            with self.subTest(route=route):
                implementation = copy.deepcopy(self.implementation)
                implementation["preview_route"] = route
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("preview_route" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
