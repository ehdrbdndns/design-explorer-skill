import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "design-explorer"


class SkillContractTests(unittest.TestCase):
    def test_skill_metadata_and_references(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\n(?P<frontmatter>.*?)\n---\n(?P<body>.*)\Z", text, re.S)
        self.assertIsNotNone(match)
        frontmatter = match.group("frontmatter")
        self.assertIn("name: design-explorer", frontmatter)
        description = re.search(r"^description:\s*(.+)$", frontmatter, re.M)
        self.assertIsNotNone(description)
        self.assertTrue(description.group(1).startswith("Use when "))
        self.assertLessEqual(len(frontmatter), 1024)
        self.assertLessEqual(len(text.splitlines()), 500)
        self.assertLessEqual(len(re.findall(r"\b\w+\b", match.group("body"))), 500)

        for relative in (
            "agents/openai.yaml",
            "references/artifact-contracts.md",
            "references/research-evidence.md",
            "references/mockups-implementation.md",
            "scripts/run_state.py",
            "scripts/validate_run.py",
        ):
            self.assertIn(relative, text)

    def test_openai_metadata_mentions_explicit_invocation(self):
        text = (SKILL_DIR / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Design Explorer"', text)
        self.assertIn("$design-explorer", text)

    def test_skill_contains_required_gates_and_resource_routing(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        required = (
            "directions_pending_approval",
            "directions_approved",
            "Do not call image generation",
            "at least five",
            "three axes",
            "explicit approval",
            "isolated preview",
            "references/research-evidence.md",
            "references/mockups-implementation.md",
            "references/artifact-contracts.md",
        )
        for phrase in required:
            self.assertIn(phrase, text)

    def test_skill_and_references_define_baselines_and_revision_loop(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        research = (SKILL_DIR / "references/research-evidence.md").read_text(
            encoding="utf-8"
        )
        mockups = (SKILL_DIR / "references/mockups-implementation.md").read_text(
            encoding="utf-8"
        )
        contracts = (SKILL_DIR / "references/artifact-contracts.md").read_text(
            encoding="utf-8"
        )

        for phrase in (
            "official evidence",
            "baseline_exceptions",
            "revise",
            "first-class direction ID",
        ):
            self.assertIn(phrase, skill)
        self.assertIn("explicit approval of disclosed exceptions", research)
        for phrase in (
            "derived_from_ids",
            "combined_properties",
            "after their sources",
            "only the newly approved IDs",
            "obtain explicit approval again",
            "bounded variations the user authorized",
        ):
            self.assertIn(phrase, mockups)
        self.assertIn("mockup-manifest.revision-", contracts)

        ordered_commands = (
            "--phase mockups",
            "--to mockups_generated",
            "--to implementation_selected",
            "--phase implementation",
            "--to prototype_ready",
            "--to integrated --approve-integration",
        )
        positions = [contracts.index(command) for command in ordered_commands]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(
            contracts.index("--to implementation_selected"),
            contracts.index("Build the isolated preview and write `implementation.json`"),
        )
        self.assertLess(
            contracts.index("Build the isolated preview and write `implementation.json`"),
            contracts.index("--phase implementation"),
        )
        self.assertLess(
            contracts.index("--to prototype_ready"),
            contracts.index("wait for explicit user integration approval"),
        )
        self.assertLess(
            contracts.index("wait for explicit user integration approval"),
            contracts.index("--to integrated --approve-integration"),
        )
        for phrase in (
            "`kind`: exactly `primary` or `derived`",
            "previously declared direction IDs",
            "six design axes",
        ):
            self.assertIn(phrase, contracts)

    def test_user_facing_direction_review_requires_complete_blocks(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        research = (SKILL_DIR / "references/research-evidence.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Present every direction in the response", skill)
        for phrase in (
            "direction id",
            "ux problem",
            "evidence IDs and direct links",
            "evidence application",
            "trade-offs",
            "layout, typography, palette, density, imagery, and interaction",
            "artifact links do not replace these user-facing direction blocks",
        ):
            self.assertIn(phrase.lower(), research.lower())

    def test_preview_verification_requires_visible_content_and_operable_controls(self):
        mockups = (SKILL_DIR / "references/mockups-implementation.md").read_text(
            encoding="utf-8"
        )

        for phrase in (
            "At every target viewport",
            "each required brief content item",
            "operable, tested disclosure path",
            "`Edit`, `Change`, or `Apply`",
            "function or must not be presented as a control",
            "rendered and interactive state, not DOM presence",
            "Do not record `content: pass` while required content is inaccessible",
        ):
            self.assertIn(phrase, mockups)

    def test_evidence_gate_commands_and_machine_readable_contract_are_documented(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        contracts = (SKILL_DIR / "references/artifact-contracts.md").read_text(
            encoding="utf-8"
        )
        mockups = (SKILL_DIR / "references/mockups-implementation.md").read_text(
            encoding="utf-8"
        )
        research = (SKILL_DIR / "references/research-evidence.md").read_text(
            encoding="utf-8"
        )

        for flag in (
            "--viewport 390x844",
            '--required-content "Order summary"',
            '--required-interaction "Edit order"',
            "--production-path src/App.tsx",
        ):
            self.assertIn(flag, contracts)
        self.assertIn("can-generate --run <run-dir>", contracts)
        self.assertIn("can-generate --run <run-dir>", mockups)
        self.assertIn("immediately before every image provider call", mockups)
        self.assertIn("screenshot_ref", mockups)
        self.assertIn("viewport_checks", mockups)
        self.assertIn("stored inside the run directory", mockups)
        self.assertIn("preview_files", contracts)
        self.assertIn("preview_route", contracts)
        self.assertIn("production_paths", contracts)
        self.assertIn("Interactive requirements: none", research)
        self.assertIn("can-generate", skill)


if __name__ == "__main__":
    unittest.main()
