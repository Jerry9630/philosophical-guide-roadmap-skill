import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "philosophical-guide"


class SkillStructureTests(unittest.TestCase):
    def test_frontmatter_and_folder_name(self):
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(?P<frontmatter>.*?)\n---", content, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group("frontmatter")
        name = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE).group(1).strip()
        description = re.search(
            r"^description:\s*(.+)$", frontmatter, re.MULTILINE
        ).group(1).strip()
        self.assertEqual(name, SKILL_ROOT.name)
        self.assertRegex(name, r"^[a-z0-9-]+$")
        self.assertLessEqual(len(name), 64)
        self.assertLessEqual(len(description), 1024)
        self.assertIn("license: MIT", frontmatter)
        self.assertIn('version: "0.3.0-rc.2"', frontmatter)
        self.assertNotIn("[TODO:", content)

    def test_referenced_skill_files_exist(self):
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        links = re.findall(r"\]\((references/[^)]+)\)", content)
        self.assertGreaterEqual(len(links), 3)
        for relative in links:
            self.assertTrue((SKILL_ROOT / relative).is_file(), relative)

    def test_openai_default_prompt_names_skill(self):
        content = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("$philosophical-guide", content)


if __name__ == "__main__":
    unittest.main()
