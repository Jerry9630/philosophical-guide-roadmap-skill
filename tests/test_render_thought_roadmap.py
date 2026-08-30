import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    def test_checked_in_svg_and_markdown_preview_match_the_example(self):
        ROADMAP.validate_state(self.state)
        view = ROADMAP.build_view_state(self.state)
        width, height, positions, tree_layouts = ROADMAP.layout_state(view)
        svg = ROADMAP.render_svg(view, width, height, positions, tree_layouts)
        markdown = ROADMAP.render_markdown(self.state, view)
        preview_root = REPO_ROOT / "preview"
        self.assertEqual((preview_root / "thought-roadmap-v1.svg").read_text(encoding="utf-8"), svg)
        self.assertEqual((preview_root / "thought-roadmap-v1.md").read_text(encoding="utf-8"), markdown)

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

    def test_short_wrapped_text_is_not_falsely_truncated(self):
        self.assertEqual(ROADMAP.wrap_text("ab cd", 3, 2), ["ab", "cd"])
        self.assertEqual(ROADMAP.wrap_text("abc", 2, 1), ["a…"])

    def test_padded_identifiers_and_enums_are_rejected_instead_of_rewritten(self):
        self.state["trees"][0]["id"] = "  tree-a  "
        self.state["trees"][0]["status"] = " active "
        self.state["trees"][0]["nodes"][0]["id"] = "  a-u1  "
        with self.assertRaises(ROADMAP.RoadmapError):
            ROADMAP.validate_state(self.state)

    def test_validation_does_not_mutate_canonical_state(self):
        self.state["trees"][0]["nodes"][0]["quote"] = "第一行\n  第二行\t末尾"
        before = copy.deepcopy(self.state)
        ROADMAP.validate_state(self.state)
        self.assertEqual(self.state, before)

    def test_multiline_quote_round_trips_through_markdown_exactly(self):
        quote = "第一行\n  第二行\t末尾"
        self.state["trees"][0]["nodes"][0]["quote"] = quote
        ROADMAP.validate_state(self.state)
        view = ROADMAP.build_view_state(self.state)
        markdown = ROADMAP.render_markdown(self.state, view)
        restored, metadata = ROADMAP.extract_markdown_state(markdown)
        self.assertEqual(restored["trees"][0]["nodes"][0]["quote"], quote)
        self.assertEqual(metadata["producer"], f"philosophical-guide/{ROADMAP.SKILL_VERSION}")

    def test_quote_and_evidence_are_reserved_for_user_confirmed_nodes(self):
        pending = self.state["trees"][0]["nodes"][-1]
        pending["quote"] = "模型写下的句子"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "quote is allowed only"):
            ROADMAP.validate_state(self.state)
        pending.pop("quote")
        pending["evidence_turn"] = "M4"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "evidence_turn is allowed only"):
            ROADMAP.validate_state(self.state)

    def test_pending_revision_relation_requires_pending_model_source(self):
        node = self.state["trees"][0]["nodes"][1]
        node["relation"] = "revision_pending"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "requires source='model_inference_pending'"):
            ROADMAP.validate_state(self.state)

    def test_confirmed_node_relations_require_user_confirmed_source(self):
        node = self.state["trees"][0]["nodes"][1]
        node["relation"] = "revision_confirmed"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "requires source='user_confirmed'"):
            ROADMAP.validate_state(self.state)

    def test_confirmed_cross_link_cannot_use_pending_inference(self):
        self.state["cross_links"][0].update(
            {"relation": "confirmed_bridge", "status": "confirmed"}
        )
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "pending model inference"):
            ROADMAP.validate_state(self.state)

    def test_confirmed_revision_must_connect_two_user_nodes(self):
        self.state["cross_links"][0].update(
            {"relation": "confirmed_revision", "status": "confirmed"}
        )
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "pending model inference|two user_confirmed"):
            ROADMAP.validate_state(self.state)

    def test_non_active_tree_cannot_keep_a_current_frontier(self):
        self.state["trees"][0]["status"] = "completed"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "must belong to an active tree"):
            ROADMAP.validate_state(self.state)

    def test_callback_relation_requires_reactivated_path(self):
        self.state["trees"][0]["nodes"][5]["path_status"] = "traversed"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "callback requires path_status='reactivated'"):
            ROADMAP.validate_state(self.state)

    def test_non_object_tree_is_rejected_without_traceback(self):
        self.state["trees"][0] = "bad"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "must be an object"):
            ROADMAP.validate_state(self.state)

    def test_unknown_fields_are_not_silently_exported(self):
        self.state["private_note"] = "must not leak"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "unsupported fields"):
            ROADMAP.validate_state(self.state)

    def test_xml_control_characters_are_rejected(self):
        self.state["title"] = "bad\u0001title"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "XML cannot represent"):
            ROADMAP.validate_state(self.state)

    def test_self_cross_link_is_rejected(self):
        self.state["cross_links"][0]["to"] = self.state["cross_links"][0]["from"]
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "itself"):
            ROADMAP.validate_state(self.state)

    def test_possible_bridge_cannot_claim_confirmation(self):
        self.state["cross_links"][0]["relation"] = "possible_bridge"
        self.state["cross_links"][0]["status"] = "confirmed"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "must have status='pending'"):
            ROADMAP.validate_state(self.state)

    def test_collapsed_group_cannot_hide_a_fork(self):
        self.state["trees"][0]["nodes"][2]["parent_id"] = "a-u1"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "cannot hide a fork"):
            ROADMAP.validate_state(self.state)

    def test_markdown_escapes_headings_and_uses_safe_json_fence(self):
        self.state["title"] = "Roadmap\n## injected"
        self.state["trees"][0]["nodes"][0]["summary"] = "```json\n# injected"
        ROADMAP.validate_state(self.state)
        view = ROADMAP.build_view_state(self.state)
        markdown = ROADMAP.render_markdown(self.state, view)
        self.assertIn("Roadmap \\#\\# injected", markdown.splitlines()[0])
        self.assertIn("````json", markdown)
        self.assertNotIn("\n# injected\n", markdown.split("## 机器可读状态", 1)[0])

    def test_markdown_digest_rejects_tampered_state(self):
        ROADMAP.validate_state(self.state)
        markdown = ROADMAP.render_markdown(self.state, ROADMAP.build_view_state(self.state))
        tampered = markdown.replace("他们确实活得很好", "他们未必活得很好")
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "digest mismatch"):
            ROADMAP.extract_markdown_state(tampered)

    def test_legacy_markdown_requires_explicit_opt_in(self):
        payload = ROADMAP.state_payload(self.state)
        legacy = f"# 旧路标\n\n## 机器可读状态\n\n```json\n{payload}\n```\n"
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "allow-legacy-markdown"):
            ROADMAP.extract_markdown_state(legacy)
        restored, metadata = ROADMAP.extract_markdown_state(legacy, allow_legacy=True)
        self.assertEqual(restored, self.state)
        self.assertEqual(metadata["digest"], "unverified")

    def test_multiple_state_blocks_are_rejected_as_ambiguous(self):
        markdown = ROADMAP.render_markdown(self.state, ROADMAP.build_view_state(self.state))
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "malformed or ambiguous"):
            ROADMAP.extract_markdown_state(markdown + markdown)

    def test_legacy_opt_in_cannot_downgrade_a_malformed_rc5_block(self):
        markdown = ROADMAP.render_markdown(self.state, ROADMAP.build_view_state(self.state))
        malformed = markdown.replace(
            ROADMAP.STATE_BLOCK_BEGIN,
            ROADMAP.STATE_BLOCK_BEGIN + "\n" + ROADMAP.STATE_BLOCK_BEGIN,
            1,
        )
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "malformed or ambiguous"):
            ROADMAP.extract_markdown_state(malformed, allow_legacy=True)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "duplicate.json"
            path.write_text('{"schema_version":"1.1","schema_version":"1.0"}', encoding="utf-8")
            with self.assertRaisesRegex(ROADMAP.RoadmapError, "duplicate JSON key"):
                ROADMAP.load_json_strict(path)

    def test_iterative_depth_layout_handles_long_chains(self):
        nodes = []
        for index in range(1500):
            nodes.append({"id": f"n{index}", "parent_id": None if index == 0 else f"n{index - 1}"})
        depths = ROADMAP.compute_depths(list(reversed(nodes)))
        self.assertEqual(depths["n1499"], 1499)

    def test_oversized_canvas_is_rejected_before_png_rendering(self):
        nodes = []
        for index in range(42):
            nodes.append(
                {
                    "id": f"n{index}",
                    "parent_id": None if index == 0 else f"n{index - 1}",
                    "relation": "root" if index == 0 else "led_to",
                    "source": "model_trigger",
                    "path_status": "current" if index == 41 else "traversed",
                    "title": f"node {index}",
                    "summary": "bounded test node",
                }
            )
        self.state["trees"] = [{"id": "long", "title": "long", "status": "active", "nodes": nodes}]
        self.state["current_node_ids"] = ["n41"]
        self.state["cross_links"] = []
        self.state["tensions"] = []
        self.state["display"] = {"collapsed_groups": []}
        self.state["pending_calibration"] = None
        ROADMAP.validate_state(self.state)
        view = ROADMAP.build_view_state(self.state)
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "safe rendering limit"):
            ROADMAP.layout_state(view)

    def test_pending_revision_relation_can_target_user_content(self):
        self.state["cross_links"].append(
            {
                "from": "a-u1",
                "to": "a-u5",
                "relation": "possible_revision",
                "status": "pending",
                "label": "新观点可能修正旧观点",
            }
        )
        self.state["pending_calibration"] = {
            "node_id": "a-u5",
            "question": "你会怎样描述这两处观点之间的关系？",
        }
        ROADMAP.validate_state(self.state)

    def test_windows_device_names_with_extensions_are_made_safe(self):
        self.assertEqual(ROADMAP.safe_basename("CON.txt"), "_CON.txt")
        self.assertEqual(ROADMAP.safe_basename("LPT1.map"), "_LPT1.map")

    def test_pathological_json_returns_a_clean_error_code(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            input_path = temp_dir / "deep.json"
            input_path.write_text("[" * 20000 + "]" * 20000, encoding="utf-8")
            argv = [
                str(SCRIPT_PATH),
                "--input", str(input_path),
                "--output-dir", str(temp_dir / "out"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(ROADMAP.sys, "argv", argv), mock.patch(
                "sys.stdout", new=stdout
            ), mock.patch("sys.stderr", new=stderr):
                self.assertEqual(ROADMAP.main(), 2)
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertIn("cannot be parsed safely", stderr.getvalue())

    def test_existing_outputs_require_force_and_failed_png_does_not_stay_stale(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            input_path = temp_dir / "input.json"
            input_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            output_dir = temp_dir / "out"
            output_dir.mkdir()
            stale_png = output_dir / "map.png"
            stale_png.write_bytes(b"stale")
            argv = [
                str(SCRIPT_PATH),
                "--input", str(input_path),
                "--output-dir", str(output_dir),
                "--basename", "map",
            ]
            with mock.patch.object(ROADMAP.sys, "argv", argv), mock.patch.object(
                ROADMAP, "render_png", return_value=(False, "unavailable")
            ), mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(ROADMAP.main(), 2)
            self.assertEqual(stale_png.read_bytes(), b"stale")

            with mock.patch.object(ROADMAP.sys, "argv", argv + ["--force"]), mock.patch.object(
                ROADMAP, "render_png", return_value=(False, "unavailable")
            ), mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(ROADMAP.main(), 0)
            self.assertFalse(stale_png.exists())
            self.assertTrue((output_dir / "map.svg").is_file())
            self.assertTrue((output_dir / "map.md").is_file())

    def test_skip_png_completes_without_optional_renderer(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            input_path = temp_dir / "input.json"
            input_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            output_dir = temp_dir / "out"
            argv = [
                str(SCRIPT_PATH),
                "--input", str(input_path),
                "--output-dir", str(output_dir),
                "--basename", "demo",
                "--skip-png",
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(ROADMAP.sys, "argv", argv), mock.patch.object(
                ROADMAP, "render_png"
            ) as png_mock, mock.patch("sys.stdout", new=stdout), mock.patch(
                "sys.stderr", new=stderr
            ):
                self.assertEqual(ROADMAP.main(), 0)
            png_mock.assert_not_called()
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue((output_dir / "demo.svg").is_file())
            self.assertTrue((output_dir / "demo.md").is_file())
            self.assertFalse((output_dir / "demo.png").exists())

    def test_validate_only_accepts_markdown_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            markdown_path = temp_dir / "roadmap.md"
            markdown_path.write_text(
                ROADMAP.render_markdown(self.state, ROADMAP.build_view_state(self.state)),
                encoding="utf-8",
            )
            argv = [str(SCRIPT_PATH), "--input-markdown", str(markdown_path), "--validate-only"]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(ROADMAP.sys, "argv", argv), mock.patch(
                "sys.stdout", new=stdout
            ), mock.patch("sys.stderr", new=stderr):
                self.assertEqual(ROADMAP.main(), 0)
            report = json.loads(stdout.getvalue())
            self.assertTrue(report["valid"])
            self.assertEqual(report["input_kind"], "markdown")
            self.assertEqual(stderr.getvalue(), "")

    def test_existing_output_lock_blocks_a_second_writer(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            input_path = temp_dir / "input.json"
            input_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            output_dir = temp_dir / "out"
            output_dir.mkdir()
            (output_dir / ".map.lock").write_text("pid=unknown\n", encoding="utf-8")
            argv = [
                str(SCRIPT_PATH),
                "--input", str(input_path),
                "--output-dir", str(output_dir),
                "--basename", "map",
                "--skip-png",
            ]
            stderr = io.StringIO()
            with mock.patch.object(ROADMAP.sys, "argv", argv), mock.patch(
                "sys.stdout", new=io.StringIO()
            ), mock.patch("sys.stderr", new=stderr):
                self.assertEqual(ROADMAP.main(), 2)
            self.assertIn("output is locked", stderr.getvalue())

    def test_unbounded_counters_and_collections_are_rejected(self):
        self.state["map_version"] = ROADMAP.MAX_COUNTER_VALUE + 1
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "map_version"):
            ROADMAP.validate_state(self.state)
        self.state["map_version"] = 1
        self.state["tensions"] = [copy.deepcopy(self.state["tensions"][0])] * (
            ROADMAP.MAX_TENSIONS + 1
        )
        with self.assertRaisesRegex(ROADMAP.RoadmapError, "tensions exceeds"):
            ROADMAP.validate_state(self.state)


if __name__ == "__main__":
    unittest.main()
