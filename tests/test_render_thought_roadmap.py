import copy
import importlib.util
import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "philosophical-guide"
SCRIPT_PATH = SKILL_ROOT / "scripts" / "render_thought_roadmap.py"
EXAMPLE_PATH = SKILL_ROOT / "references" / "example-roadmap.json"

SPEC = importlib.util.spec_from_file_location("render_thought_roadmap", SCRIPT_PATH)
ROADMAP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ROADMAP)


class RoadmapStateTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    def test_example_validates_and_renders_collapsed_view(self):
        ROADMAP.validate_state(self.state)
        view = ROADMAP.build_view_state(self.state)
        canonical_ids = {
            node["id"] for tree in self.state["trees"] for node in tree["nodes"]
        }
        view_ids = {node["id"] for tree in view["trees"] for node in tree["nodes"]}

        self.assertIn("a-u1", canonical_ids)
        self.assertNotIn("a-u1", view_ids)
        self.assertIn("collapse-a-early", view_ids)

        width, height, positions, tree_layouts = ROADMAP.layout_state(view)
        svg = ROADMAP.render_svg(view, width, height, positions, tree_layouts)
        markdown = ROADMAP.render_markdown(self.state, view)
        self.assertIn('data-roadmap-id="collapse-a-early"', svg)
        self.assertIn("完整节点仍保存在下方机器可读状态中", markdown)
        self.assertIn('"id": "a-u1"', markdown)

    def test_user_confirmed_quote_is_optional_when_evidence_turn_exists(self):
        self.state["trees"][0]["nodes"][0].pop("quote")
        ROADMAP.validate_state(self.state)

    def test_malformed_tensions_are_rejected_before_rendering(self):
        self.state["tensions"] = "bad"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "tensions must be a list"):
            ROADMAP.validate_state(self.state)

    def test_duplicate_tree_ids_are_rejected(self):
        self.state["trees"][1]["id"] = self.state["trees"][0]["id"]
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "duplicate tree id"):
            ROADMAP.validate_state(self.state)

    def test_collapsed_group_cannot_hide_current_node(self):
        state = copy.deepcopy(self.state)
        state["display"]["collapsed_groups"][0]["member_node_ids"].append("a-m2")
        with self.assertRaises(ROADMAP.RoadmapError):
            ROADMAP.validate_state(state)

    def test_removing_display_rule_restores_original_nodes(self):
        state = copy.deepcopy(self.state)
        state["display"]["collapsed_groups"] = []
        ROADMAP.validate_state(state)
        view = ROADMAP.build_view_state(state)
        view_ids = {node["id"] for tree in view["trees"] for node in tree["nodes"]}
        self.assertIn("a-u1", view_ids)
        self.assertIn("a-m1", view_ids)
        self.assertIn("a-u2", view_ids)


if __name__ == "__main__":
    unittest.main()
