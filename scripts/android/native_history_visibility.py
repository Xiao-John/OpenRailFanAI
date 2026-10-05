"""Check history footer layout containment and opaque-menu occlusion, not painted ink."""
from __future__ import annotations

import math
from typing import Mapping

from native_capture_basis import validate_basis


def _ltrb(value: object) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("rectangle must contain four LTRB coordinates")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError("rectangle coordinates must be finite numbers")
    left, top, right, bottom = value
    if right <= left or bottom <= top:
        raise ValueError("rectangle must have positive width and height")
    return list(value)


def _xywh(value: Mapping[str, float]) -> list[float]:
    if not isinstance(value, Mapping):
        raise ValueError("rectangle must be an XYWH mapping")
    coordinates = [value.get(key) for key in ("x", "y", "width", "height")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in coordinates):
        raise ValueError("XYWH coordinates must be finite numbers")
    x, y, width, height = coordinates
    if width <= 0 or height <= 0:
        raise ValueError("XYWH rectangle must have positive width and height")
    return _ltrb([x, y, x + width, y + height])


def _area(a: list[float], b: list[float]) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def history_footer_visibility(diagnostics: object, basis: object,
                              measurements: Mapping[str, Mapping[str, float]]) -> dict:
    """Consume a same-capture diagnostic; incomplete evidence never becomes a pass."""
    result = {"status": "unmeasured", "scope": "layout containment and opaque-menu occlusion only",
              "coordinate_origin": "capture_root", "nodes": [], "failed_nodes": [], "unmeasured": []}
    errors = result["unmeasured"]
    if not isinstance(diagnostics, dict) or not isinstance(basis, dict):
        errors.append("history footer diagnostics or capture basis missing")
        return result
    try:
        schema_version = basis.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
            raise ValueError("capture basis schema_version must be integer 1")
        basis_errors = validate_basis(basis, dict(measurements))
        if basis_errors:
            raise ValueError("; ".join(basis_errors))
        if basis.get("phase") != "before_interaction" or basis.get("scroll_mode") != "continuous":
            raise ValueError("history visibility requires a before-interaction continuous-scroll basis")
        content = basis["content_rect_px"]
        safe = _ltrb([content[0], content[1], content[0] + content[2], content[1] + content[3]])
        scroll = basis["scroll"]
        viewport = _ltrb(diagnostics["viewport_bounds_px"])
        diagnostic_safe = _ltrb(diagnostics["safe_content_bounds_px"])
        if any(abs(a - b) > 0.01 for a, b in zip(diagnostic_safe, safe)):
            raise ValueError("history diagnostic safe content disagrees with the capture basis")
        measured_viewport = _xywh(measurements.get("history-scroll-container"))
        if any(abs(a - b) > 0.01 for a, b in zip(viewport, measured_viewport)):
            raise ValueError("history diagnostic viewport disagrees with measured history-scroll-container")
        if (viewport[0] < safe[0] or viewport[1] < safe[1]
                or viewport[2] > safe[2] or viewport[3] > safe[3]):
            raise ValueError("history diagnostic viewport must be contained in measured safe content")
        if any(isinstance(diagnostics.get(field), bool) or not isinstance(diagnostics.get(field), int)
               for field in ("scroll_offset_px", "scroll_max_px")):
            raise ValueError("history diagnostic scroll values must be integers, not booleans")
        if (diagnostics.get("scroll_kind") != "continuous_vertical_scroll"
                or diagnostics.get("scroll_offset_px") != scroll["scroll_offset_px"]
                or diagnostics.get("scroll_max_px") != scroll["scroll_max_px"]
                or viewport[1] != scroll["viewport_start_px"]
                or viewport[3] != scroll["viewport_end_px"]):
            raise ValueError("history diagnostics do not match the formal capture scroll basis")
        menu_state = diagnostics.get("menu_state", "open")
        if menu_state == "closed":
            if any(tag in measurements for tag in ("history-actions-panel", "history-actions-cancel")):
                raise ValueError("closed drawer capture contains action-menu measurements")
            overlays = {}
        elif menu_state == "open":
            overlays = {tag: _xywh(measurements[tag]) for tag in
                        ("history-actions-panel", "history-actions-cancel")}
        else:
            raise ValueError("unknown history menu state")
    except (KeyError, TypeError, ValueError, IndexError) as error:
        errors.append(f"history footer capture context invalid: {error}")
        return result

    result["safe_content_ltrb_px"] = safe
    result["opaque_overlay_ltrb_px"] = overlays
    node_bounds: dict[str, object] = {}
    for field, required in (
        ("footer_visible_labels", ("history-settings-label", "history-help-label")),
        ("footer_visible_icons", ("history-settings-icon", "history-help-icon")),
    ):
        records = diagnostics.get(field)
        if not isinstance(records, list):
            errors.append(f"history footer diagnostic list missing: {field}")
            continue
        for tag in required:
            matching = [record for record in records if isinstance(record, dict) and record.get("selector") == tag]
            if len(matching) != 1:
                errors.append(f"{tag}: expected one diagnostic record, found {len(matching)}")
            else:
                node_bounds[tag] = matching[0].get("bounds_px")
    node_bounds["history-footer-divider"] = diagnostics.get("footer_divider_bounds_px")
    for tag, raw in node_bounds.items():
        try:
            bounds = _ltrb(raw)
            if tag in measurements:
                measured = _xywh(measurements[tag])
                if any(abs(a - b) > 0.01 for a, b in zip(bounds, measured)):
                    raise ValueError("diagnostic bounds disagree with the same-capture NATIVE_RECT")
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{tag}: {error}")
            continue
        contained = (bounds[0] >= safe[0] and bounds[1] >= safe[1]
                     and bounds[2] <= safe[2] and bounds[3] <= safe[3])
        overlaps = {overlay: _area(bounds, rect) for overlay, rect in overlays.items()}
        passed = contained and all(area == 0 for area in overlaps.values())
        record = {"selector": tag, "layout_bounds_ltrb_px": bounds,
                  "fully_inside_safe_content": contained, "opaque_overlap_area_px2": overlaps,
                  "result": "passed" if passed else "failed"}
        result["nodes"].append(record)
        if not passed:
            result["failed_nodes"].append(record)
    result["status"] = "failed" if result["failed_nodes"] else "unmeasured" if errors else "passed"
    return result
