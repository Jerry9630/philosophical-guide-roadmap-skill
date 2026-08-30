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
        self.assertIn('version: "0.3.0"', frontmatter)
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
        self.assertIn("可选思想路标", content)
        self.assertIn("默认不保存路标", content)

    def test_roadmap_mode_discovery_preserves_explicit_opt_in(self):
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("同一对话不重复这项首次提示", content)
        self.assertIn("再作至多一次贴合当前语境的提醒", content)
        self.assertIn("不得仅因轮次增加或达到三轮就触发", content)
        self.assertIn("显式点名本 Skill 也不自动开启路标", content)

    def test_release_and_security_files_exist(self):
        for relative in [
            "SECURITY.md",
            "tools/build_release.py",
            ".github/workflows/test.yml",
            ".github/workflows/release.yml",
            ".github/dependabot.yml",
        ]:
            self.assertTrue((REPO_ROOT / relative).is_file(), relative)

    def test_github_actions_are_pinned_to_full_commit_shas(self):
        for name in ["test.yml", "release.yml"]:
            content = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            uses = re.findall(r"^\s*- uses:\s*([^\s]+)$", content, re.MULTILINE)
            self.assertTrue(uses, name)
            for action in uses:
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
