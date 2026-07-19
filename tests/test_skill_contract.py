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


if __name__ == "__main__":
    unittest.main()
