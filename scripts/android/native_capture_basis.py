"""Validate the coordinate evidence emitted with a native screenshot."""
from __future__ import annotations

import math


def validate_basis(basis: object, measurements: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(basis, dict):
        return ["capture basis missing: NATIVE_BASIS was not emitted for this state"]
    if basis.get("schema_version") != 1:
        return ["capture basis has missing or unsupported schema_version"]

    def numbers(key: str, size: int) -> list[float] | None:
        value = basis.get(key)
        if not isinstance(value, list) or len(value) != size or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value
        ):
            problems.append(f"capture basis invalid: {key}")
            return None
        return value

    root = numbers("root_rect_px", 4)
    content = numbers("content_rect_px", 4)
    raw_insets = numbers("system_insets_platform_px", 4)
    applied = numbers("system_insets_applied_px", 4)
    densities: dict[str, float] = {}
    for key in ("platform_density", "local_density", "font_scale"):
        value = basis.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            problems.append(f"capture basis invalid: {key}")
        else:
            densities[key] = value
    if basis.get("coordinate_origin") != "capture_root":
        problems.append("capture basis coordinate_origin must be capture_root")
    if root and (root != [0, 0, 390, 844]):
        problems.append("capture basis root must match the 390x844 screenshot")
    if any(x < 0 for values in (raw_insets, applied) if values for x in values):
        problems.append("capture basis insets cannot be negative")
    if root and content:
        x, y, width, height = content
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > root[2] + 1 or y + height > root[3] + 1:
            problems.append("capture basis content bounds exceed capture root")
    if raw_insets and applied and all(key in densities for key in ("platform_density", "local_density")):
        factor = densities["local_density"] / densities["platform_density"]
        if any(abs(raw * factor - local) > 1 for raw, local in zip(raw_insets, applied)):
            problems.append("capture basis system inset density conversion is inconsistent")
    content_tag = basis.get("content_tag")
    rect = measurements.get(content_tag) if isinstance(content_tag, str) else None
    if not isinstance(rect, dict):
        problems.append("capture basis content_tag has no measured Compose bounds")
    elif content:
        if any(not isinstance(rect.get(key), (int, float)) or isinstance(rect.get(key), bool)
               or not math.isfinite(rect[key]) or abs(rect[key] - value) > 1
               for key, value in zip(("x", "y", "width", "height"), content)):
            problems.append("capture basis content bounds differ from measured Compose bounds")
    scroll_mode = basis.get("scroll_mode")
    scroll = basis.get("scroll")
    if scroll_mode not in ("list", "continuous", "not_scrollable"):
        problems.append("capture basis scroll_mode is missing or unsupported")
    if scroll_mode == "not_scrollable":
        if scroll is not None:
            problems.append("capture basis not_scrollable must have null scroll")
    elif scroll is None:
        problems.append("capture basis scroll missing for a scrollable container")
    elif not isinstance(scroll, dict):
        problems.append("capture basis scroll must be an object or null")
    else:
        fields = ["viewport_start_px", "viewport_end_px"]
        if scroll_mode == "list":
            fields += ["first_visible_item_index", "first_visible_item_offset_px"]
        elif scroll_mode == "continuous":
            fields += ["scroll_offset_px", "scroll_max_px"]
        for key in fields:
            value = scroll.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"capture basis scroll field invalid: {key}")
        if scroll_mode == "list":
            visible = scroll.get("visible_item_indices")
            if not isinstance(visible, list) or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in visible):
                problems.append("capture basis scroll visible_item_indices invalid")
            for key in ("first_visible_item_index", "first_visible_item_offset_px"):
                value = scroll.get(key)
                if isinstance(value, int) and value < 0:
                    problems.append(f"capture basis scroll field cannot be negative: {key}")
        elif scroll_mode == "continuous":
            for key in ("first_visible_item_index", "first_visible_item_offset_px", "visible_item_indices"):
                if key in scroll:
                    problems.append(f"capture basis continuous scroll must not declare list field: {key}")
            offset, maximum = scroll.get("scroll_offset_px"), scroll.get("scroll_max_px")
            if isinstance(offset, int) and isinstance(maximum, int) and not 0 <= offset <= maximum:
                problems.append("capture basis continuous scroll offset is outside its range")
        start, end = scroll.get("viewport_start_px"), scroll.get("viewport_end_px")
        if isinstance(start, int) and isinstance(end, int) and end <= start:
            problems.append("capture basis scroll viewport is empty")
        if scroll_mode == "continuous" and root and isinstance(start, int) and isinstance(end, int):
            if start < 0 or end > root[3]:
                problems.append("capture basis continuous viewport exceeds capture root")
    if basis.get("phase") != "before_interaction":
        problems.append("capture basis must describe the screenshot before interaction")
    return problems
