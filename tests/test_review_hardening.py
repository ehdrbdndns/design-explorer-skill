import hashlib
import json
import subprocess
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from design_explorer_import import load_script_module
from test_run_state import direction, evidence, reference


run_state = load_script_module("run_state_review", "design-explorer/scripts/run_state.py")
validator = load_script_module("validator_review", "design-explorer/scripts/validate_run.py")


def png_chunk(name: bytes, data: bytes, corrupt_crc: bool = False) -> bytes:
    crc = zlib.crc32(name + data) & 0xFFFFFFFF
    if corrupt_crc:
        crc ^= 1
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)


def png_bytes(
    *, include_idat: bool = True, include_iend: bool = True, corrupt_crc: bool = False
) -> bytes:
    value = b"\x89PNG\r\n\x1a\n" + png_chunk(
        b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 2, 0, 0, 0), corrupt_crc
    )
    if include_idat:
        value += png_chunk(b"IDAT", zlib.compress(b"\x00" + b"\x00" * 6))
    if include_iend:
        value += png_chunk(b"IEND", b"")
    return value


class StateInvariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.run = run_state.init_run(
            self.root / "runs",
            "review",
            project_path=str(self.project / ".." / "project"),
            run_id="review-run",
            target_viewports=["390x844"],
            required_content=["Summary"],
            required_interactions=["Edit"],
            production_paths=["src/App.tsx"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, name: str, value) -> None:
        (self.run / name).write_text(json.dumps(value), encoding="utf-8")

    def write_directions(self) -> None:
        self.write_json("evidence.json", [evidence()])
        self.write_json(
            "directions.json", [direction(f"d-{index}", index) for index in range(5)]
        )
        (self.run / "mood-directions.md").write_text("# Directions", encoding="utf-8")

    def advance_to_pending(self) -> None:
        (self.run / "brief.md").write_text("# Brief", encoding="utf-8")
        run_state.transition_run(self.run, "brief_ready")
        self.write_json("references.json", [reference()])
        self.write_json("evidence.json", [evidence()])
        (self.run / "design-evidence.md").write_text("# Evidence", encoding="utf-8")
        (self.run / "reference-board.md").write_text("# Board", encoding="utf-8")
        run_state.transition_run(self.run, "research_complete")
        self.write_directions()
        run_state.transition_run(self.run, "directions_pending_approval")

    def test_brief_transition_locks_canonical_constraints_and_digest(self):
        initialized = run_state.load_run(self.run)
        self.assertEqual(initialized["project_path"], str(self.project.resolve()))
        (self.run / "brief.md").write_text("# Brief", encoding="utf-8")

        locked = run_state.transition_run(
            self.run, "brief_ready", now="2026-07-20T10:00:00Z"
        )
        expected = {
            "project_path": str(self.project.resolve()),
            "target_viewports": ["390x844"],
            "required_content": ["Summary"],
            "required_interactions": ["Edit"],
            "production_paths": ["src/App.tsx"],
        }
        canonical = json.dumps(
            expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual(locked["brief_constraints"], expected)
        self.assertEqual(
            locked["brief_constraints_digest"],
            "sha256:" + hashlib.sha256(canonical).hexdigest(),
        )
        self.assertEqual(locked["brief_locked_at"], "2026-07-20T10:00:00Z")
        self.assertEqual(run_state.load_run(self.run), locked)

    def test_later_load_fails_closed_for_each_mutated_constraint_or_digest(self):
        (self.run / "brief.md").write_text("# Brief", encoding="utf-8")
        locked = run_state.transition_run(self.run, "brief_ready")
        mutations = {
            "project_path": str((self.root / "other").resolve()),
            "target_viewports": ["1440x900"],
            "required_content": ["Other"],
            "required_interactions": ["Other"],
            "production_paths": ["src/Other.tsx"],
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                tampered = dict(locked)
                tampered[key] = value
                self.write_json("run.json", tampered)
                before = json.loads((self.run / "run.json").read_text())
                with self.assertRaisesRegex(ValueError, "brief constraints"):
                    run_state.load_run(self.run)
                with self.assertRaisesRegex(ValueError, "brief constraints"):
                    run_state.transition_run(self.run, "research_complete")
                self.assertEqual(json.loads((self.run / "run.json").read_text()), before)

        tampered = dict(locked)
        tampered["brief_constraints_digest"] = "sha256:" + "0" * 64
        self.write_json("run.json", tampered)
        with self.assertRaisesRegex(ValueError, "brief_constraints_digest"):
            run_state.load_run(self.run)

    def test_generation_state_requires_current_valid_bounded_approved_ids(self):
        self.advance_to_pending()
        valid = run_state.transition_run(
            self.run, "directions_approved", approved_direction_ids=["d-0"]
        )
        cases = (
            ([], "non-empty"),
            (["unknown"], "unknown"),
            (["d-0", "d-0"], "unique"),
            ([f"d-{index}" for index in range(5)], "generation_budget"),
        )
        for index, (approved, expected) in enumerate(cases):
            with self.subTest(approved=approved):
                tampered = dict(valid)
                tampered["approved_direction_ids"] = approved
                if index == 3:
                    tampered["generation_budget"] = 4
                self.write_json("run.json", tampered)
                with self.assertRaisesRegex(ValueError, expected):
                    run_state.load_run(self.run)
                self.assertFalse(run_state.image_generation_allowed(self.run))
                result = subprocess.run(
                    [sys.executable, run_state.__file__, "can-generate", "--run", str(self.run)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout.strip(), "false")
                self.assertNotIn("Traceback", result.stderr)

    def test_later_state_revalidates_approved_ids_against_changed_directions(self):
        self.advance_to_pending()
        manifest = run_state.transition_run(
            self.run, "directions_approved", approved_direction_ids=["d-0"]
        )
        manifest["state"] = "mockups_generated"
        self.write_json("run.json", manifest)
        directions = [direction(f"d-{index}", index) for index in range(5)]
        directions[0]["id"] = "replacement"
        self.write_json("directions.json", directions)

        with self.assertRaisesRegex(ValueError, "approved.*current directions"):
            run_state.load_run(self.run)


class PngValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "evidence.png"

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_png_with_all_chunk_crcs_returns_dimensions(self):
        self.path.write_bytes(png_bytes())
        self.assertEqual(validator.png_dimensions(self.path), (2, 3))

    def test_png_rejects_corruption_truncation_missing_chunks_and_trailing_bytes(self):
        invalid_values = (
            png_bytes(corrupt_crc=True),
            png_bytes()[:-3],
            png_bytes(include_idat=False),
            png_bytes(include_iend=False),
            png_bytes() + b"trailing",
            b"\x89PNG\r\n\x1a\n" + png_chunk(b"IDAT", b"") + png_chunk(b"IEND", b""),
            png_bytes()[:-12] + png_chunk(b"IEND", b"not-empty"),
        )
        for index, value in enumerate(invalid_values):
            with self.subTest(index=index):
                self.path.write_bytes(value)
                self.assertIsNone(validator.png_dimensions(self.path))

    def test_png_rejects_declared_oversize_chunk_and_oversize_file(self):
        self.path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", validator.MAX_PNG_CHUNK_BYTES + 1)
            + b"IHDR"
        )
        self.assertIsNone(validator.png_dimensions(self.path))

        with self.path.open("wb") as stream:
            stream.seek(validator.MAX_PNG_FILE_BYTES)
            stream.write(b"x")
        self.assertIsNone(validator.png_dimensions(self.path))


if __name__ == "__main__":
    unittest.main()
