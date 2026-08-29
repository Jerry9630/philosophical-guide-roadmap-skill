#!/usr/bin/env python3
"""Render a philosophical thought-roadmap JSON into SVG, PNG, and Markdown.

The SVG and Markdown paths require only the Python standard library. PNG is
produced when CairoSVG or a compatible headless browser is available.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.1"
NODE_WIDTH = 320
X_GAP = 92
Y_GAP = 36
MARGIN_X = 70
TREE_HEADER_HEIGHT = 62
TREE_GAP = 74
TOP_AREA = 168
BOTTOM_AREA = 132

SOURCES = {
    "user_confirmed": {
        "label": "用户确认",
        "fill": "#ffffff",
        "stroke": "#83ad9c",
        "badge_fill": "#dfeee7",
        "badge_text": "#32634f",
    },
    "model_trigger": {
        "label": "模型触发",
        "fill": "#f2f6fc",
        "stroke": "#86a1ca",
        "badge_fill": "#dfe9f8",
        "badge_text": "#456a9f",
    },
    "model_inference_pending": {
        "label": "模型推测 · 待确认",
        "fill": "#fff8e9",
        "stroke": "#d7a343",
        "badge_fill": "#f7e7bd",
        "badge_text": "#94681e",
    },
    "display_summary": {
        "label": "折叠展示",
        "fill": "#eef1f4",
        "stroke": "#9aaab5",
        "badge_fill": "#dfe5e9",
        "badge_text": "#586a76",
    },
}

CANONICAL_SOURCES = {
    "user_confirmed",
    "model_trigger",
    "model_inference_pending",
}

PATH_STATUSES = {
    "current",
    "traversed",
    "unexplored",
    "shelved",
    "reactivated",
}

TREE_STATUSES = {"active", "shelved", "completed"}
TENSION_STATUSES = {"open", "resolved", "shelved"}
CROSS_LINK_RELATIONS = {"possible_bridge", "confirmed_bridge", "callback"}

RELATIONS = {
    "root",
    "led_to",
    "distinction",
    "counterexample",
    "revision_pending",
    "revision_confirmed",
    "qualification",
    "conflict",
    "branch",
    "callback",
}

RELATION_LABELS = {
    "led_to": "推进",
    "distinction": "区分",
    "counterexample": "反例",
    "revision_pending": "可能修正",
    "revision_confirmed": "修正",
    "qualification": "限定",
    "conflict": "冲突",
    "branch": "留下分支",
    "callback": "callback",
    "possible_bridge": "可能桥接",
    "confirmed_bridge": "桥接",
}

STATUS_LABELS = {
    "current": "当前位置",
    "traversed": "已走过",
    "unexplored": "未展开",
    "shelved": "已搁置",
    "reactivated": "重新激活",
}


class RoadmapError(ValueError):
    pass


def display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
    return width


def wrap_text(text: str, max_width: int, max_lines: int) -> list[str]:
    text = " ".join(str(text).split())
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and display_width(candidate) > max_width:
            if char in "，。！？；：、）》】”’」』":
                current += char
                continue
            lines.append(current.rstrip())
            current = char.lstrip()
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if len(lines) < max_lines and current:
        lines.append(current.rstrip())
    consumed = "".join(lines)
    if len(consumed) < len(text) and lines:
        last = lines[-1]
        lines[-1] = (last[:-1] if last else "") + "…"
    return lines


def require_string(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RoadmapError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def require_string_list(value: Any, where: str, *, non_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise RoadmapError(f"{where} must be a list")
    if non_empty and not value:
        raise RoadmapError(f"{where} must be a non-empty list")
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise RoadmapError(f"{where}[{i}] must be a non-empty string")
    if len(value) != len(set(value)):
        raise RoadmapError(f"{where} must not contain duplicates")
    return value


def validate_state(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise RoadmapError(
            f"schema_version must be {SCHEMA_VERSION!r}; got {state.get('schema_version')!r}"
        )
    if type(state.get("map_version")) is not int or state["map_version"] < 1:
        raise RoadmapError("map_version must be a positive integer")
    require_string(state, "title", "root")
    require_string(state, "topic_summary", "root")
    turns = state.get("substantive_turns_since_checkpoint")
    if type(turns) is not int or turns < 0:
        raise RoadmapError("substantive_turns_since_checkpoint must be a non-negative integer")

    trees = state.get("trees")
    if not isinstance(trees, list) or not trees:
        raise RoadmapError("trees must be a non-empty list")

    node_index: dict[str, dict[str, Any]] = {}
    node_tree: dict[str, str] = {}
    tree_ids: set[str] = set()
    for tree_i, tree in enumerate(trees):
        where = f"trees[{tree_i}]"
        tree_id = require_string(tree, "id", where)
        if tree_id in tree_ids:
            raise RoadmapError(f"duplicate tree id: {tree_id}")
        tree_ids.add(tree_id)
        require_string(tree, "title", where)
        tree_status = require_string(tree, "status", where)
        if tree_status not in TREE_STATUSES:
            raise RoadmapError(f"{where}.status is not supported: {tree_status}")
        nodes = tree.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise RoadmapError(f"{where}.nodes must be a non-empty list")
        local_ids: set[str] = set()
        for node_i, node in enumerate(nodes):
            node_where = f"{where}.nodes[{node_i}]"
            node_id = require_string(node, "id", node_where)
            if node_id in node_index:
                raise RoadmapError(f"duplicate node id: {node_id}")
            source = require_string(node, "source", node_where)
            if source not in CANONICAL_SOURCES:
                raise RoadmapError(f"{node_where}.source is not supported: {source}")
            path_status = require_string(node, "path_status", node_where)
            if path_status not in PATH_STATUSES:
                raise RoadmapError(f"{node_where}.path_status is not supported: {path_status}")
            relation = require_string(node, "relation", node_where)
            if relation not in RELATIONS:
                raise RoadmapError(f"{node_where}.relation is not supported: {relation}")
            require_string(node, "title", node_where)
            require_string(node, "summary", node_where)
            if source == "user_confirmed":
                require_string(node, "evidence_turn", node_where)
            if "quote" in node and (not isinstance(node["quote"], str) or not node["quote"].strip()):
                raise RoadmapError(f"{node_where}.quote must be omitted or be a non-empty string")
            local_ids.add(node_id)
            node_index[node_id] = node
            node_tree[node_id] = tree_id
        for node in nodes:
            parent_id = node.get("parent_id")
            if parent_id is not None and parent_id not in local_ids:
                raise RoadmapError(
                    f"node {node['id']} has missing or cross-tree parent_id {parent_id!r}; use cross_links"
                )
            if parent_id is None and node["relation"] != "root":
                raise RoadmapError(f"root node {node['id']} must use relation='root'")
            if parent_id is not None and node["relation"] == "root":
                raise RoadmapError(f"non-root node {node['id']} cannot use relation='root'")

    current_ids = require_string_list(state.get("current_node_ids", []), "current_node_ids")
    for node_id in current_ids:
        if node_id not in node_index:
            raise RoadmapError(f"current_node_ids contains unknown node: {node_id}")
        if node_index[node_id]["path_status"] not in {"current", "reactivated"}:
            raise RoadmapError(
                f"current node {node_id} must have path_status current or reactivated"
            )
    listed_current = set(current_ids)
    for node_id, node in node_index.items():
        if node["path_status"] == "current" and node_id not in listed_current:
            raise RoadmapError(f"node {node_id} has path_status current but is missing from current_node_ids")

    links = state.get("cross_links", [])
    if not isinstance(links, list):
        raise RoadmapError("cross_links must be a list")
    for i, link in enumerate(links):
        where = f"cross_links[{i}]"
        start = require_string(link, "from", where)
        end = require_string(link, "to", where)
        if start not in node_index or end not in node_index:
            raise RoadmapError(f"{where} references an unknown node")
        if link.get("status") not in {"confirmed", "pending"}:
            raise RoadmapError(f"{where}.status must be confirmed or pending")
        relation = require_string(link, "relation", where)
        if relation not in CROSS_LINK_RELATIONS:
            raise RoadmapError(f"{where}.relation is not supported: {relation}")
        require_string(link, "label", where)

    tensions = state.get("tensions", [])
    if not isinstance(tensions, list):
        raise RoadmapError("tensions must be a list")
    tension_ids: set[str] = set()
    for i, tension in enumerate(tensions):
        where = f"tensions[{i}]"
        if not isinstance(tension, dict):
            raise RoadmapError(f"{where} must be an object")
        tension_id = require_string(tension, "id", where)
        if tension_id in tension_ids:
            raise RoadmapError(f"duplicate tension id: {tension_id}")
        tension_ids.add(tension_id)
        require_string(tension, "summary", where)
        if tension.get("status") not in TENSION_STATUSES:
            raise RoadmapError(f"{where}.status is not supported: {tension.get('status')}")
        related = require_string_list(
            tension.get("related_node_ids"), f"{where}.related_node_ids", non_empty=True
        )
        for node_id in related:
            if node_id not in node_index:
                raise RoadmapError(f"{where} references an unknown node: {node_id}")

    calibration = state.get("pending_calibration")
    if calibration is not None:
        if not isinstance(calibration, dict):
            raise RoadmapError("pending_calibration must be null or an object")
        node_id = require_string(calibration, "node_id", "pending_calibration")
        require_string(calibration, "question", "pending_calibration")
        if node_id not in node_index:
            raise RoadmapError("pending_calibration.node_id is unknown")
        if node_index[node_id]["source"] != "model_inference_pending":
            raise RoadmapError("pending_calibration must point to a model_inference_pending node")

    display = state.get("display", {"collapsed_groups": []})
    if not isinstance(display, dict):
        raise RoadmapError("display must be an object")
    groups = display.get("collapsed_groups", [])
    if not isinstance(groups, list):
        raise RoadmapError("display.collapsed_groups must be a list")
    group_ids: set[str] = set()
    grouped_nodes: set[str] = set()
    for i, group in enumerate(groups):
        where = f"display.collapsed_groups[{i}]"
        if not isinstance(group, dict):
            raise RoadmapError(f"{where} must be an object")
        group_id = require_string(group, "id", where)
        if group_id in group_ids or group_id in node_index:
            raise RoadmapError(f"duplicate or colliding collapsed group id: {group_id}")
        group_ids.add(group_id)
        tree_id = require_string(group, "tree_id", where)
        if tree_id not in tree_ids:
            raise RoadmapError(f"{where}.tree_id is unknown: {tree_id}")
        require_string(group, "title", where)
        require_string(group, "summary", where)
        member_ids = require_string_list(
            group.get("member_node_ids"), f"{where}.member_node_ids", non_empty=True
        )
        members = set(member_ids)
        if grouped_nodes.intersection(members):
            raise RoadmapError(f"{where} overlaps another collapsed group")
        grouped_nodes.update(members)
        entries = []
        for node_id in member_ids:
            if node_id not in node_index:
                raise RoadmapError(f"{where} references an unknown node: {node_id}")
            if node_tree[node_id] != tree_id:
                raise RoadmapError(f"{where} contains a node from another tree: {node_id}")
            node = node_index[node_id]
            if node["path_status"] != "traversed":
                raise RoadmapError(f"{where} may contain only traversed nodes: {node_id}")
            if node["source"] == "model_inference_pending":
                raise RoadmapError(f"{where} cannot hide a pending model inference: {node_id}")
            if node_id in listed_current:
                raise RoadmapError(f"{where} cannot hide a current node: {node_id}")
            if node.get("parent_id") not in members:
                entries.append(node_id)
        if len(entries) != 1:
            raise RoadmapError(f"{where} must describe one connected branch with exactly one entry node")
        if calibration is not None and calibration["node_id"] in members:
            raise RoadmapError(f"{where} cannot hide the pending calibration node")

    # Check parent chains for cycles after all nodes are indexed.
    for tree in trees:
        local = {node["id"]: node for node in tree["nodes"]}
        for node_id in local:
            seen: set[str] = set()
            cursor: str | None = node_id
            while cursor is not None:
                if cursor in seen:
                    raise RoadmapError(f"parent cycle detected at node {node_id}")
                seen.add(cursor)
                cursor = local[cursor].get("parent_id")

    return node_index


def build_view_state(state: dict[str, Any]) -> dict[str, Any]:
    """Build a render-only graph while preserving canonical nodes in state."""
    display = state.get("display", {"collapsed_groups": []})
    groups = display.get("collapsed_groups", [])
    group_by_member = {
        node_id: group
        for group in groups
        for node_id in group["member_node_ids"]
    }
    view = dict(state)
    view_trees: list[dict[str, Any]] = []

    for tree in state["trees"]:
        inserted_groups: set[str] = set()
        view_nodes: list[dict[str, Any]] = []
        for node in tree["nodes"]:
            group = group_by_member.get(node["id"])
            if group is not None:
                if group["id"] in inserted_groups:
                    continue
                inserted_groups.add(group["id"])
                members = set(group["member_node_ids"])
                entry = next(
                    item for item in tree["nodes"]
                    if item["id"] in members and item.get("parent_id") not in members
                )
                parent_id = entry.get("parent_id")
                if parent_id in group_by_member:
                    parent_id = group_by_member[parent_id]["id"]
                view_nodes.append(
                    {
                        "id": group["id"],
                        "parent_id": parent_id,
                        "relation": "root" if parent_id is None else entry["relation"],
                        "source": "display_summary",
                        "path_status": "traversed",
                        "title": group["title"],
                        "summary": group["summary"],
                        "display_kind": "collapsed_group",
                        "member_count": len(group["member_node_ids"]),
                    }
                )
                continue

            view_node = dict(node)
            parent_id = view_node.get("parent_id")
            if parent_id in group_by_member:
                view_node["parent_id"] = group_by_member[parent_id]["id"]
            view_nodes.append(view_node)

        view_tree = dict(tree)
        view_tree["nodes"] = view_nodes
        view_trees.append(view_tree)

    view["trees"] = view_trees
    remapped_links = []
    for link in state.get("cross_links", []):
        mapped = dict(link)
        if mapped["from"] in group_by_member:
            mapped["from"] = group_by_member[mapped["from"]]["id"]
        if mapped["to"] in group_by_member:
            mapped["to"] = group_by_member[mapped["to"]]["id"]
        if mapped["from"] != mapped["to"]:
            remapped_links.append(mapped)
    view["cross_links"] = remapped_links
    return view


def node_height(node: dict[str, Any]) -> int:
    title_lines = wrap_text(node["title"], 26, 2)
    summary_lines = wrap_text(node["summary"], 34, 3)
    quote_lines = wrap_text(node.get("quote", ""), 36, 2)
    return max(165, 90 + 25 * len(title_lines) + 23 * len(summary_lines) + 19 * len(quote_lines))


def compute_depths(nodes: list[dict[str, Any]]) -> dict[str, int]:
    index = {node["id"]: node for node in nodes}
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        parent_id = index[node_id].get("parent_id")
        memo[node_id] = 0 if parent_id is None else depth(parent_id) + 1
        return memo[node_id]

    for item in nodes:
        depth(item["id"])
    return memo


def layout_state(state: dict[str, Any]) -> tuple[int, int, dict[str, dict[str, int]], list[dict[str, int]]]:
    positions: dict[str, dict[str, int]] = {}
    tree_layouts: list[dict[str, int]] = []
    y_cursor = TOP_AREA
    max_depth_global = 0

    for tree in state["trees"]:
        depths = compute_depths(tree["nodes"])
        columns: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for order, node in enumerate(tree["nodes"]):
            columns[depths[node["id"]]].append((order, node))
        for entries in columns.values():
            # Input order is intentional: keep the main path before side branches.
            # Status-based sorting can pull a reactivated branch across the main path
            # and create misleading edge crossings.
            entries.sort(key=lambda item: item[0])

        max_depth = max(columns)
        max_depth_global = max(max_depth_global, max_depth)
        column_heights = []
        for depth_value, entries in columns.items():
            total = sum(node_height(node) for _, node in entries) + Y_GAP * max(0, len(entries) - 1)
            column_heights.append(total)
        content_height = max(column_heights)
        tree_content_y = y_cursor + TREE_HEADER_HEIGHT

        for depth_value, entries in columns.items():
            node_y = tree_content_y
            for _, node in entries:
                height = node_height(node)
                positions[node["id"]] = {
                    "x": MARGIN_X + depth_value * (NODE_WIDTH + X_GAP),
                    "y": node_y,
                    "w": NODE_WIDTH,
                    "h": height,
                }
                node_y += height + Y_GAP

        tree_layouts.append(
            {
                "y": y_cursor,
                "content_y": tree_content_y,
                "height": TREE_HEADER_HEIGHT + content_height,
            }
        )
        y_cursor += TREE_HEADER_HEIGHT + content_height + TREE_GAP

    width = MARGIN_X * 2 + (max_depth_global + 1) * NODE_WIDTH + max_depth_global * X_GAP
    width = max(width, 1180)
    height = y_cursor - TREE_GAP + BOTTOM_AREA
    return width, height, positions, tree_layouts


def svg_text_lines(x: int, y: int, lines: list[str], css_class: str, line_height: int) -> str:
    if not lines:
        return ""
    escaped = [html.escape(line) for line in lines]
    tspans = [f'<tspan x="{x}" dy="0">{escaped[0]}</tspan>']
    tspans.extend(f'<tspan x="{x}" dy="{line_height}">{line}</tspan>' for line in escaped[1:])
    return f'<text x="{x}" y="{y}" class="{css_class}">' + "".join(tspans) + "</text>"


def node_style(node: dict[str, Any]) -> dict[str, str]:
    style = dict(SOURCES[node["source"]])
    if node.get("display_kind") == "collapsed_group":
        style.update(stroke_width="2.5", dash="5 5")
        return style
    status = node["path_status"]
    style["stroke_width"] = "2.5"
    style["dash"] = ""
    if status == "current":
        style.update(stroke_width="4")
    elif status == "reactivated":
        style.update(stroke_width="3", dash="3 4")
    elif status in {"unexplored", "shelved"}:
        style.update(dash="8 6")
    return style


def render_svg(state: dict[str, Any], width: int, height: int, positions: dict[str, dict[str, int]], tree_layouts: list[dict[str, int]]) -> str:
    node_index = {node["id"]: node for tree in state["trees"] for node in tree["nodes"]}
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        """<defs>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
    <feDropShadow dx="0" dy="7" stdDeviation="9" flood-color="#15362e" flood-opacity="0.10"/>
  </filter>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
    <path d="M0,0 L10,5 L0,10 Z" fill="#687a74"/>
  </marker>
  <marker id="arrow-pending" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
    <path d="M0,0 L10,5 L0,10 Z" fill="#d59a31"/>
  </marker>
  <style>
    .title{font:700 36px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#17372f}
    .subtitle{font:400 17px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#687a74}
    .tree{font:700 18px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#17372f}
    .badge{font:700 13px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif}
    .node-title{font:700 18px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#17372f}
    .body{font:400 15px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#40564f}
    .quote{font:400 13px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#667771}
    .edge{font:600 12px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#687a74}
    .legend{font:500 13px 'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif;fill:#40564f}
    .card{filter:url(#shadow)}
  </style>
</defs>""",
        f'<rect width="{width}" height="{height}" fill="#f7f8f3"/>',
        f'<circle cx="{width - 80}" cy="70" r="180" fill="#e8f0d7" opacity="0.68"/>',
        svg_text_lines(MARGIN_X, 65, wrap_text(f'{state["title"]} · v{state["map_version"]}', 42, 1), "title", 40),
        svg_text_lines(MARGIN_X, 98, wrap_text(state["topic_summary"], 64, 1), "subtitle", 24),
    ]

    # Legend
    legend_x = max(MARGIN_X + 480, width - 650)
    parts.append(f'<rect x="{legend_x}" y="36" width="580" height="78" rx="18" fill="#ffffff" stroke="#dce5df"/>')
    legend_items = [
        (SOURCES["user_confirmed"]["stroke"], "用户确认"),
        (SOURCES["model_trigger"]["stroke"], "模型触发"),
        (SOURCES["model_inference_pending"]["stroke"], "待确认"),
    ]
    for i, (color, label) in enumerate(legend_items):
        x = legend_x + 26 + i * 132
        y = 62
        parts.append(f'<circle cx="{x}" cy="{y}" r="7" fill="{color}"/>')
        parts.append(f'<text x="{x + 15}" y="{y + 5}" class="legend">{label}</text>')
    parts.append(f'<text x="{legend_x + 26}" y="96" class="legend">路径状态：粗框＝当前　虚线＝未展开／搁置　点划线＝重新激活</text>')

    # Tree headers
    for i, tree in enumerate(state["trees"]):
        layout = tree_layouts[i]
        label_y = layout["y"] + 28
        parts.append(f'<rect x="{MARGIN_X}" y="{layout["y"]}" width="142" height="34" rx="17" fill="#dfeee7"/>')
        parts.append(f'<text x="{MARGIN_X + 20}" y="{label_y}" class="tree">主题树 {i + 1}</text>')
        tree_title = wrap_text(tree["title"], 52, 1)[0]
        parts.append(f'<text x="{MARGIN_X + 166}" y="{label_y}" class="tree">{html.escape(tree_title)}</text>')

    # Primary edges, behind nodes.
    for tree in state["trees"]:
        for node in tree["nodes"]:
            parent_id = node.get("parent_id")
            if parent_id is None:
                continue
            start = positions[parent_id]
            end = positions[node["id"]]
            x1, y1 = start["x"] + start["w"], start["y"] + start["h"] // 2
            x2, y2 = end["x"], end["y"] + end["h"] // 2
            mid = (x1 + x2) // 2
            pending = node["relation"] == "revision_pending" or node["source"] == "model_inference_pending"
            color = "#d59a31" if pending else ("#7c66a8" if node["relation"] == "callback" else "#687a74")
            dash = ' stroke-dasharray="8 7"' if pending or node["relation"] in {"branch", "callback"} else ""
            marker = "arrow-pending" if pending else "arrow"
            parts.append(
                f'<path d="M{x1} {y1} C{mid} {y1}, {mid} {y2}, {x2} {y2}" fill="none" '
                f'stroke="{color}" stroke-width="2.7"{dash} marker-end="url(#{marker})"/>'
            )
            label = RELATION_LABELS.get(node["relation"], node["relation"])
            if label:
                parts.append(f'<text x="{mid}" y="{min(y1, y2) - 9}" text-anchor="middle" class="edge">{html.escape(label)}</text>')

    # Cross links.
    for link in state.get("cross_links", []):
        start = positions[link["from"]]
        end = positions[link["to"]]
        x1, y1 = start["x"] + start["w"] // 2, start["y"] + start["h"]
        x2, y2 = end["x"] + end["w"] // 2, end["y"] + end["h"]
        bend = max(y1, y2) + 34
        color = "#d59a31" if link["status"] == "pending" else "#7c66a8"
        marker = "arrow-pending" if link["status"] == "pending" else "arrow"
        parts.append(
            f'<path d="M{x1} {y1} C{x1} {bend}, {x2} {bend}, {x2} {y2}" fill="none" '
            f'stroke="{color}" stroke-width="2.5" stroke-dasharray="8 7" marker-end="url(#{marker})"/>'
        )
        label = wrap_text(link["label"], 40, 1)[0]
        parts.append(f'<text x="{(x1 + x2) // 2}" y="{bend - 8}" text-anchor="middle" class="edge">{html.escape(label)}</text>')

    # Nodes.
    for node_id, pos in positions.items():
        node = node_index[node_id]
        style = node_style(node)
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        parts.append(f'<g class="card" data-roadmap-id="{html.escape(node_id)}">')
        parts.append(
            f'<rect x="{pos["x"]}" y="{pos["y"]}" width="{pos["w"]}" height="{pos["h"]}" rx="22" '
            f'fill="{style["fill"]}" stroke="{style["stroke"]}" stroke-width="{style["stroke_width"]}"{dash}/>'
        )

        source_label = style["label"]
        if node.get("display_kind") == "collapsed_group":
            source_label = f'{source_label} · {node["member_count"]} 个节点'
        elif node["path_status"] in {"current", "unexplored", "shelved", "reactivated"}:
            source_label = f'{source_label} · {STATUS_LABELS[node["path_status"]]}'
        badge_width = min(244, max(92, 28 + display_width(source_label) * 8))
        parts.append(
            f'<rect x="{pos["x"] + 18}" y="{pos["y"] + 17}" width="{badge_width}" height="27" rx="13" fill="{style["badge_fill"]}"/>'
        )
        parts.append(
            f'<text x="{pos["x"] + 32}" y="{pos["y"] + 36}" class="badge" fill="{style["badge_text"]}">{html.escape(source_label)}</text>'
        )

        text_x = pos["x"] + 20
        cursor_y = pos["y"] + 75
        title_lines = wrap_text(node["title"], 26, 2)
        parts.append(svg_text_lines(text_x, cursor_y, title_lines, "node-title", 24))
        cursor_y += max(1, len(title_lines)) * 24 + 9
        summary_lines = wrap_text(node["summary"], 34, 3)
        parts.append(svg_text_lines(text_x, cursor_y, summary_lines, "body", 22))
        cursor_y += len(summary_lines) * 22 + 9
        if node.get("quote"):
            quote_lines = wrap_text(f'原话：“{node["quote"]}”', 36, 2)
            parts.append(svg_text_lines(text_x, cursor_y, quote_lines, "quote", 18))
        parts.append("</g>")

    footer_y = height - 34
    parts.append(f'<line x1="{MARGIN_X}" y1="{footer_y - 25}" x2="{width - MARGIN_X}" y2="{footer_y - 25}" stroke="#d8e1dc"/>')
    parts.append(f'<text x="{MARGIN_X}" y="{footer_y}" class="subtitle">路标原则：记录已经出现的路，不替用户预画未来方向。</text>')
    parts.append(f'<text x="{width - MARGIN_X}" y="{footer_y}" text-anchor="end" class="subtitle">schema {SCHEMA_VERSION}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def render_markdown(state: dict[str, Any], view_state: dict[str, Any]) -> str:
    current_ids = set(state.get("current_node_ids", []))
    source_mark = {
        "user_confirmed": "U✓",
        "model_trigger": "M→",
        "model_inference_pending": "M?",
        "display_summary": "D…",
    }
    lines = [
        f'# {state["title"]} v{state["map_version"]}',
        "",
        "> 把本文件完整粘贴到新对话即可恢复。用户确认与模型推测必须继续分开处理。",
        "",
        f'- 主题概括：{state["topic_summary"]}',
        f'- 当前节点：{", ".join(state.get("current_node_ids", [])) or "未标记"}',
        "",
    ]
    for tree in view_state["trees"]:
        lines.extend([f'## {tree["title"]}', ""])
        for node in tree["nodes"]:
            current = " · 当前" if node["id"] in current_ids else ""
            lines.append(
                f'- [{source_mark[node["source"]]}][{STATUS_LABELS[node["path_status"]]}{current}] '
                f'`{node["id"]}` {node["title"]}：{node["summary"]}'
            )
            if node.get("display_kind") == "collapsed_group":
                lines.append(f'  - 折叠展示：完整节点仍保存在下方机器可读状态中，共 {node["member_count"]} 个。')
            elif node.get("quote"):
                lines.append(f'  - 原话依据：“{node["quote"]}”')
            if node.get("parent_id"):
                relation = RELATION_LABELS.get(node["relation"], node["relation"])
                lines.append(f'  - 来自：`{node["parent_id"]}` —{relation}→')
        lines.append("")

    if view_state.get("cross_links"):
        lines.extend(["## 跨树或补充连接", ""])
        for link in view_state["cross_links"]:
            lines.append(
                f'- [{link["status"]}] `{link["from"]}` → `{link["to"]}`：{link["label"]}'
            )
        lines.append("")

    if state.get("tensions"):
        lines.extend(["## 仍未解决的张力", ""])
        for tension in state["tensions"]:
            lines.append(f'- [{tension.get("status", "open")}] {tension["summary"]}')
        lines.append("")

    if state.get("pending_calibration"):
        calibration = state["pending_calibration"]
        lines.extend(
            [
                "## 本次待校准",
                "",
                f'- 节点：`{calibration["node_id"]}`',
                f'- 问题：{calibration["question"]}',
                "",
            ]
        )

    lines.extend(
        [
            "## 机器可读状态",
            "",
            "```json",
            json.dumps(state, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def browser_candidates() -> list[str]:
    candidates: list[str] = []
    for command in ["msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"]:
        found = shutil.which(command)
        if found:
            candidates.append(found)
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ]
        )
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in unique and Path(candidate).is_file():
            unique.append(candidate)
    return unique


def render_png(svg_path: Path, png_path: Path, width: int, height: int) -> tuple[bool, str]:
    try:
        import cairosvg  # type: ignore

        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=width, output_height=height)
        if png_path.is_file() and png_path.stat().st_size > 0:
            return True, "CairoSVG"
    except Exception:
        pass

    failures: list[str] = []
    for browser in browser_candidates():
        if png_path.exists():
            png_path.unlink()
        temp_root = Path(tempfile.mkdtemp(prefix="thought-roadmap-browser-"))
        try:
            profile = temp_root / "profile"
            temp_png = temp_root / "roadmap.png"
            command = [
                browser,
                "--headless=new",
                "--no-sandbox",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-component-update",
                "--hide-scrollbars",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                f"--screenshot={temp_png}",
                svg_path.resolve().as_uri(),
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=40,
                check=False,
            )
            deadline = time.time() + 8
            while time.time() < deadline and not temp_png.exists():
                time.sleep(0.2)
            if temp_png.is_file() and temp_png.stat().st_size > 0:
                shutil.copy2(temp_png, png_path)
                return True, Path(browser).name
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            suffix = detail[-1][:180] if detail else "no diagnostic output"
            failures.append(f"{Path(browser).name}: exit {completed.returncode}; {suffix}")
        except Exception as exc:
            failures.append(f"{Path(browser).name}: {exc}")
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
    reason = "; ".join(failures) if failures else "no CairoSVG or supported headless browser found"
    return False, reason


def safe_basename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
    return cleaned or "thought-roadmap"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Roadmap state JSON")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for outputs")
    parser.add_argument("--basename", default="thought-roadmap", help="Output filename stem")
    parser.add_argument("--require-png", action="store_true", help="Fail if PNG cannot be rendered")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        state = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise RoadmapError("input JSON root must be an object")
        validate_state(state)
        view_state = build_view_state(state)
        width, height, positions, tree_layouts = layout_state(view_state)
        svg = render_svg(view_state, width, height, positions, tree_layouts)
        markdown = render_markdown(state, view_state)

        args.output_dir.mkdir(parents=True, exist_ok=True)
        basename = safe_basename(args.basename)
        svg_path = args.output_dir / f"{basename}.svg"
        png_path = args.output_dir / f"{basename}.png"
        md_path = args.output_dir / f"{basename}.md"
        svg_path.write_text(svg, encoding="utf-8")
        md_path.write_text(markdown, encoding="utf-8")
        png_ok, png_engine = render_png(svg_path, png_path, width, height)

        result = {
            "svg": str(svg_path.resolve()),
            "png": str(png_path.resolve()) if png_ok else None,
            "markdown": str(md_path.resolve()),
            "png_engine": png_engine if png_ok else None,
            "dimensions": {"width": width, "height": height},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not png_ok:
            print(f"PNG not generated: {png_engine}", file=sys.stderr)
            return 3 if args.require_png else 0
        return 0
    except (OSError, json.JSONDecodeError, RoadmapError) as exc:
        print(f"Roadmap render failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
