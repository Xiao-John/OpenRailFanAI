"""Small, deterministic metrics used by the native acceptance index builder."""
from __future__ import annotations

from typing import Mapping, Sequence
import math


def unique_icon_origin(capture: Mapping, tag: str) -> bool:
    """Accept a real unique node or the explicitly evidenced reading union."""
    nodes = capture.get("node_semantics", {})
    measurements = capture.get("measurements", {})
    node = nodes.get(tag)
    if not isinstance(node, Mapping):
        return False
    if node.get("evidence_kind") != "derived_visible_bounds_union":
        return node.get("match_count") == 1 and node.get("semantic_tag_present") is not False
    tags = ["result-followup-1-visual", "result-followup-2-visual"]
    sources = node.get("source_semantic_nodes")
    if (tag != "reading-followup-visual-union" or node.get("semantic_tag_present") is not False
            or node.get("source_tags") != tags or not isinstance(sources, list) or len(sources) != 2):
        return False
    rects, ids = [], []
    for source, source_tag in zip(sources, tags):
        real = nodes.get(source_tag)
        measured = measurements.get(source_tag)
        if (not isinstance(source, Mapping) or source.get("source_tag") != source_tag
                or source.get("match_count") != 1 or not isinstance(real, Mapping)
                or real.get("match_count") != 1 or not isinstance(measured, Mapping)):
            return False
        node_id = source.get("node_id")
        if isinstance(node_id, bool) or not isinstance(node_id, int) or node_id != real.get("node_id"):
            return False
        ids.append(node_id)
        visible, unclipped = source.get("visible_rect_px"), source.get("unclipped_rect_px")
        if any(not isinstance(r, list) or len(r) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in r
        ) for r in (visible, unclipped)):
            return False
        if visible[2] <= visible[0] or visible[3] <= visible[1]:
            return False
        if any(abs(v-u) > .01 for v, u in zip(visible, unclipped)):
            return False
        if any(source.get(field) != real.get(field) for field in
               ("visible_rect_px", "unclipped_rect_px", "ancestor_tags")):
            return False
        values = [measured.get(k) for k in ("x", "y", "width", "height")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            return False
        x, y, w, h = values
        if min(w, h) <= 0 or any(abs(v-u) > .01 for v, u in zip(visible, [x, y, x+w, y+h])):
            return False
        rects.append(visible)
    if len(set(ids)) != 2:
        return False
    union = [min(r[0] for r in rects), min(r[1] for r in rects),
             max(r[2] for r in rects), max(r[3] for r in rects)]
    return node.get("visible_rect_px") == union and node.get("unclipped_rect_px") == union


def design_scale(frame_width: float, capture: Mapping, policy: Mapping) -> tuple[float, dict]:
    """Map a full app frame or a standalone module to its declared canvas.

    A module illustration is not a screenshot of an entire application window.
    Its available canvas is the measured page content, inside application
    padding. All coordinates come from the same formal capture.
    """
    mode, tag = policy.get("mode"), policy.get("actual_canvas_tag")
    if mode not in ("application_window", "component_canvas") or not isinstance(tag, str):
        raise ValueError("design scale requires an explicit canvas policy")
    if isinstance(frame_width, bool) or not isinstance(frame_width, (int, float)) or not math.isfinite(frame_width) or frame_width <= 0:
        raise ValueError("design scale requires a positive source width")
    rect = capture.get("measurements", {}).get(tag)
    node = capture.get("node_semantics", {}).get(tag)
    if not isinstance(rect, Mapping) or not isinstance(node, Mapping) or node.get("match_count") != 1:
        raise ValueError("design scale canvas lacks unique fresh measurements")
    values = [rect.get(k) for k in ("x", "y", "width", "height")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values) or min(values[2:]) <= 0:
        raise ValueError("design scale canvas has invalid bounds")
    x, y, width, height = values
    box = [x, y, x + width, y + height]
    visible, unclipped = node.get("visible_rect_px"), node.get("unclipped_rect_px")
    for bounds in (visible, unclipped):
        if not isinstance(bounds, list) or len(bounds) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            or abs(v - wanted) > .01 for v, wanted in zip(bounds, box)
        ):
            raise ValueError("design scale canvas is clipped or inconsistent")
    content = capture.get("basis", {}).get("content_rect_px")
    if not isinstance(content, list) or len(content) != 4 or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in content
    ) or min(content[2:]) <= 0:
        raise ValueError("design scale canvas lacks safe content bounds")
    if x < content[0] or y < content[1] or x + width > content[0] + content[2] or y + height > content[1] + content[3]:
        raise ValueError("design scale canvas exceeds measured safe content")
    if mode == "application_window":
        root = capture.get("basis", {}).get("root_rect_px")
        insets = capture.get("basis", {}).get("system_insets_applied_px")
        if (not isinstance(root, list) or len(root) != 4 or not isinstance(insets, list) or len(insets) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in root + insets)
                or min(root[2:]) <= 0 or min(insets) < 0
                or abs(width - content[2]) > .01
                or abs(width - (root[2] - insets[0] - insets[2])) > 1
                or abs(x - (root[0] + insets[0])) > 1):
            raise ValueError("full application scale cannot discard horizontal page padding")
    return width / frame_width, {"mode": mode, "actual_canvas_tag": tag,
                                 "source_frame_width_px": frame_width,
                                 "actual_canvas_rect_px": values,
                                 "scale": width / frame_width,
                                 "scope": "source geometry conversion only; capture PNG, density and font scale are unchanged"}


def safe_bottom_target(expected: Mapping[str, float], design_height_px: float,
                       content_rect_px: Sequence[float], origin_y: float) -> tuple[dict[str, float], dict]:
    """Keep design size/bottom gap, anchor only y to independently measured safe content."""
    values = [design_height_px, origin_y, *content_rect_px, *expected.values()]
    if len(content_rect_px) != 4 or any(isinstance(value, bool) or not isinstance(value, (int, float))
                                         or not math.isfinite(value) for value in values):
        raise ValueError("safe-bottom anchor requires finite measured coordinates")
    if design_height_px <= 0 or content_rect_px[3] <= 0 or expected["height"] <= 0:
        raise ValueError("safe-bottom anchor requires positive heights")
    gap = design_height_px - expected["y"] - expected["height"]
    if gap < -1e-6 or expected["y"] < 0:
        raise ValueError("safe-bottom design target exceeds its application frame")
    gap = max(0.0, gap)
    bottom = content_rect_px[1] + content_rect_px[3]
    root_y = bottom - gap - expected["height"]
    if root_y < content_rect_px[1]:
        raise ValueError("safe-bottom target cannot fit inside measured content")
    adjusted = {**expected, "y": root_y - origin_y}
    return adjusted, {
        "mode": "safe_bottom",
        "design_frame_height_px": round(design_height_px, 2),
        "design_bottom_gap_px": round(gap, 2),
        "content_rect_root_px": list(content_rect_px),
        "content_bottom_root_px": round(bottom, 2),
        "actual_origin_root_y_px": origin_y,
        "expected_root_y_px": round(root_y, 2),
        "shift_y_px": round(adjusted["y"] - expected["y"], 2),
    }


def absolute_geometry_delta(expected: Mapping[str, float], actual: Mapping[str, float], fields: Sequence[str]) -> dict[str, float]:
    """Return symmetric absolute geometry error for each compared rectangle field."""
    return {field: round(abs(float(actual[field]) - float(expected[field])), 2) for field in fields}


def minimum_touch_size(expected: Mapping[str, float], actual: Mapping[str, float]) -> dict[str, object]:
    """Check only touch-target width/height minima; never treats position as a size rule."""
    deficits = {
        field: round(max(0.0, float(expected[field]) - float(actual[field])), 2)
        for field in ("width", "height")
    }
    return {"minimum_px": {field: round(float(expected[field]), 2) for field in ("width", "height")},
            "actual_px": {field: round(float(actual[field]), 2) for field in ("width", "height")},
            "deficit_px": deficits, "compliant": all(value == 0 for value in deficits.values())}
