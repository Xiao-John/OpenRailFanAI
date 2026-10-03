"""Pair every design copy selector with a same-capture native semantics leaf."""
from __future__ import annotations

import math


def _rect_valid(rect: object) -> bool:
    return (isinstance(rect, list) and len(rect) == 4
            and all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in rect)
            and rect[2] > rect[0] and rect[3] > rect[1])


def compare_copy(design_entries: list[dict], bindings: list[dict], nodes: dict,
                 basis_valid: bool, clip_rects: dict | None = None,
                 occluders: dict | None = None) -> dict:
    """Exact copy content only. Geometry, occlusion and glyph fidelity are separate gates."""
    checked, unmeasured, failed, failed_visibility, excluded = [], [], [], [], []
    keys = lambda entries: [(e.get("selector"), e.get("mode"), e.get("expected")) for e in entries]
    if keys(bindings) != keys(design_entries) or len(set(keys(design_entries))) != len(design_entries):
        return {"scope": "exact native copy content only", "checked": [], "failed": [],
                "unmeasured": ["native copy bindings must preserve every unique design selector, mode and expected text in order"]}
    if not basis_valid:
        return {"scope": "exact native copy content only", "checked": [], "failed": [],
                "unmeasured": ["native copy leaf pairing requires a valid same-capture basis"]}
    for design_entry, binding in zip(design_entries, bindings):
        selector, tag = binding["selector"], binding.get("native_tag")
        record = {"design_selector": selector, "design_mode": binding["mode"],
                  "expected": binding["expected"], "native_tag": tag,
                  "native_property": binding.get("native_property")}
        if design_entry.get("applicability") == "excluded_by_user_resolution":
            reason = design_entry.get("exclusion_reason")
            if (isinstance(reason, str) and reason.strip()
                    and binding.get("applicability") == "excluded_by_user_resolution"
                    and binding.get("exclusion_reason") == reason):
                excluded.append({**record, "reason": reason, "status": "excluded_by_user_resolution"})
                continue
            unmeasured.append(f"{selector}: user-authorized exclusion is not identically declared in both contracts")
            continue
        if not tag:
            unmeasured.append(f"{selector}: {binding.get('unmeasured_reason', 'native leaf binding missing')}")
            continue
        node = nodes.get(tag)
        if (not isinstance(node, dict) or node.get("match_count") != 1
                or isinstance(node.get("node_id"), bool) or not isinstance(node.get("node_id"), int)
                or not _rect_valid(node.get("visible_rect_px"))
                or not _rect_valid(node.get("unclipped_rect_px"))
                or node.get("unclipped_unit") != "local_dp_at_density_1"):
            unmeasured.append(f"{selector}: unique native leaf with same-capture visible/unclipped bounds missing ({tag})")
            continue
        ancestor = binding.get("required_ancestor")
        if ancestor and ancestor not in node.get("ancestor_tags", []):
            unmeasured.append(f"{selector}: leaf {tag} is not in its required {ancestor} context")
            continue
        prop = binding.get("native_property")
        value = node.get(prop)
        if prop == "text":
            if node.get("text_children_count") != 0 or not isinstance(value, list) or len(value) != 1:
                unmeasured.append(f"{selector}: exact one-text leaf required; aggregated/container text is not sufficient")
                continue
            value = value[0]
        elif prop == "content_description":
            if not isinstance(value, list) or len(value) != 1:
                unmeasured.append(f"{selector}: exact one-description node required")
                continue
            value = value[0]
        elif prop != "editable_text" or not isinstance(value, str):
            unmeasured.append(f"{selector}: requested native property is missing or unsupported")
            continue
        record.update(actual=value, exact=value == binding["expected"], node_id=node["node_id"],
                      ancestor_tags=node["ancestor_tags"], visible_rect_px=node["visible_rect_px"],
                      unclipped_rect_px=node["unclipped_rect_px"])
        visible, full = node["visible_rect_px"], node["unclipped_rect_px"]
        visibility_reasons = []
        if max(abs(a-b) for a, b in zip(visible, full)) > .01:
            visibility_reasons.append("native leaf bounds are clipped")
        clips = {tag: rect for tag, rect in (clip_rects or {}).items()
                 if tag == "capture_root" or tag in node["ancestor_tags"]}
        if "capture_root" not in clips or any(not _rect_valid(rect) for rect in clips.values()):
            unmeasured.append(f"{selector}: same-capture viewport/clip bounds missing")
        else:
            for clip_tag, rect in clips.items():
                if any((full[0] < rect[0]-.01, full[1] < rect[1]-.01,
                        full[2] > rect[2]+.01, full[3] > rect[3]+.01)):
                    visibility_reasons.append(f"native leaf extends beyond {clip_tag}")
            for overlay_tag, rect in (occluders or {}).items():
                if overlay_tag in node["ancestor_tags"]:
                    continue
                if not _rect_valid(rect):
                    unmeasured.append(f"{selector}: invalid opaque overlay bounds ({overlay_tag})")
                    continue
                area = max(0, min(full[2], rect[2])-max(full[0], rect[0])) * max(0, min(full[3], rect[3])-max(full[1], rect[1]))
                if area > 0:
                    visibility_reasons.append(f"native leaf is covered by {overlay_tag}: {area:.2f}px²")
        record["visibility"] = {"clip_rects_px": clips, "occluders_px": occluders or {},
                                "complete": not visibility_reasons and "capture_root" in clips,
                                "reasons": visibility_reasons}
        if visibility_reasons:
            # A clipped line box can include only blank font padding. Keep it unproved;
            # do not claim that glyph pixels are hidden without same-image ink evidence.
            unmeasured.append(f"{selector}: complete text visibility not proven: {'; '.join(visibility_reasons)}")
        checked.append(record)
        if not record["exact"]:
            failed.append(record)
    return {"scope": "exact native copy content plus same-capture clipping/declared opaque-overlay checks; no typography or glyph inference",
            "expected_count": len(design_entries), "applicable_count": len(design_entries) - len(excluded),
            "excluded": excluded, "checked": checked, "failed": failed,
            "failed_visibility": failed_visibility, "unmeasured": unmeasured}
