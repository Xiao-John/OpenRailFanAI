#!/usr/bin/env python3
"""Build unscaled native acceptance comparisons and evidence-based indices."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from PIL import Image
from native_acceptance_metrics import absolute_geometry_delta, minimum_touch_size, safe_bottom_target, design_scale, unique_icon_origin
from native_capture_basis import validate_basis
from native_history_visibility import history_footer_visibility
from native_paint_metrics import measure_text_pair
from native_copy_evidence import compare_copy
from native_icon_metrics import measure_icon_pair
from native_harmony_acceptance import evaluate_harmony

ROOT = Path(__file__).resolve().parents[2]
VISUAL = ROOT / "frontend/tests/visual"
OUT = ROOT / "acceptance"
FAILED = OUT / "_failed"
CAPTURE_DIR = ROOT / "android/app/build/native-captures"
VERSION = "2.8.0"
STATES = ["train_schedule", "emu_routing", "query_loading", "batch_partial", "routing_empty", "connection_error", "reading_followup", "query_details", "history"]
ACC_IDS = {state: f"ACC-CT8-{index:02d}" for index, state in enumerate(STATES, 1)}

parser = argparse.ArgumentParser()
parser.add_argument("--failure", help="record missing capture evidence after a build/capture failure")
parser.add_argument("--expected-run-token", help="require capture evidence from the current acceptance invocation")
args = parser.parse_args()
OUT.mkdir(exist_ok=True)
FAILED.mkdir(exist_ok=True)
index_path = OUT / "index.yaml"
failed_path = FAILED / "index.yaml"
index = yaml.safe_load(index_path.read_text()) if index_path.exists() else []
failed = yaml.safe_load(failed_path.read_text()) if failed_path.exists() else []
index = index if isinstance(index, list) else []
failed = failed if isinstance(failed, list) else []
replace_ids = set(ACC_IDS.values())
index = [item for item in index if item.get("acc_id") not in replace_ids]
failed = [item for item in failed if item.get("acc_id") not in replace_ids]
design_document = json.loads((VISUAL / "design-targets.json").read_text())
targets = design_document["states"]
measurement_map = json.loads((VISUAL / "compose-measurement-map.json").read_text())
design_copy = json.loads((VISUAL / "design-copy.json").read_text())
policy_path = VISUAL / "native-acceptance-policy.yaml"
acceptance_policy = yaml.safe_load(policy_path.read_text()) if policy_path.exists() else {}
acceptance_policy = acceptance_policy if isinstance(acceptance_policy, dict) else {}
review_path = ROOT / acceptance_policy.get("review_ref", ".ai/reports/native-harmony-review.yaml")
harmony_review = yaml.safe_load(review_path.read_text()) if review_path.exists() else {}
copy_contract = json.loads((ROOT / "android/app/src/androidTest/assets/native-copy-bindings.json").read_text())
if copy_contract.get("schema_version") != 1 or copy_contract.get("design_copy_ref") != "frontend/tests/visual/design-copy.json":
    raise SystemExit("Invalid native copy bindings contract")
icon_targets_document = json.loads((VISUAL / "native-icon-targets.json").read_text())
if icon_targets_document.get("schema_version") != 1 or not isinstance(icon_targets_document.get("targets"), list):
    raise SystemExit("Invalid native icon targets contract")
icon_targets_by_state: dict[str, list[dict]] = {state: [] for state in STATES}
icon_target_contract_errors: list[str] = []
for icon_target in icon_targets_document["targets"]:
    if isinstance(icon_target, dict) and icon_target.get("state") in icon_targets_by_state:
        icon_targets_by_state[icon_target["state"]].append(icon_target)
    else:
        icon_target_contract_errors.append("malformed target or target with unknown acceptance state")
icon_coverage_remainder = []
for field in ("unmeasured_source_icons", "remaining_visual_targets", "missing_native_shape_targets"):
    for item in icon_targets_document.get(field, []):
        if isinstance(item, dict):
            icon_coverage_remainder.append(item.get("id") or item.get("description") or item.get("reason") or field)
        else:
            icon_coverage_remainder.append(str(item))
now = datetime.now(ZoneInfo("Asia/Shanghai"))
new_entries: list[dict] = []


def write_indices(entries: list[dict]) -> list[dict]:
    updated = index + entries
    current_failures = failed + [item for item in entries if item["status"] == "failed"]
    index_path.write_text(yaml.safe_dump(updated, allow_unicode=True, sort_keys=False))
    failed_path.write_text(yaml.safe_dump(current_failures, allow_unicode=True, sort_keys=False))
    return updated


def measure_icon_target(target: dict, design: dict, design_image: Image.Image, actual: Image.Image,
                        capture: dict, scale: float, basis_errors: list[str]) -> dict:
    """Measure one HR36 semantic icon target using only this capture's evidence."""
    def unmeasured(reason: str) -> dict:
        return {"target_id": target.get("id"), "state": target.get("state"),
                "native_tag": target.get("native_tag"), "status": "unmeasured", "reason": reason}

    tag = target.get("native_tag")
    if not isinstance(tag, str) or not tag:
        return unmeasured(target.get("unmeasured_reason", "design icon has no matching native semantic tag"))
    if basis_errors:
        return unmeasured("capture basis is invalid: " + "; ".join(basis_errors))
    if target.get("design_source") != design.get("source"):
        return unmeasured("icon target source does not match this state design source")
    frame = design.get("frame")
    declared_design_frame = target.get("design_frame")
    declared_frame = declared_design_frame.get("rect_xywh") if isinstance(declared_design_frame, dict) else None
    if (not isinstance(frame, list) or len(frame) != 4 or not isinstance(declared_frame, list)
            or len(declared_frame) != 4 or declared_frame != frame):
        return unmeasured("icon target design frame does not exactly match the state frame")
    roi = target.get("roi")
    if not isinstance(roi, list) or len(roi) != 4:
        return unmeasured("design rawROI is missing or malformed")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in frame + roi) or frame[2] <= 0 or frame[3] <= 0 or roi[2] <= 0 or roi[3] <= 0:
        return unmeasured("design frame or rawROI contains invalid geometry")
    if (roi[0] < frame[0] or roi[1] < frame[1]
            or roi[0] + roi[2] > frame[0] + frame[2]
            or roi[1] + roi[3] > frame[1] + frame[3]):
        return unmeasured("design rawROI is not fully contained in its declared source frame")
    # A glyph may be compared within its explicitly paired control even when
    # that control's page position is calibrated independently to a safe area.
    # This is an internal component comparison, not evidence of page placement.
    safe_bottom_target = (target.get("coordinate_mode") != "component_local"
                          and (tag == "main-send-icon"
                               or str(target.get("id", "")).startswith("history-footer-")))
    safe_bottom_registration = target.get("safe_bottom_registration")
    safe_bottom_declared = (isinstance(safe_bottom_registration, dict)
                            and safe_bottom_registration.get("mode") == "safe_bottom"
                            and safe_bottom_registration.get("design_reference") == "design_frame_bottom"
                            and safe_bottom_registration.get("actual_reference") == "capture_content_bottom")
    if safe_bottom_target and not safe_bottom_declared:
        return unmeasured("safe-bottom calibrated icon lacks an explicit design-frame/content-bottom registration declaration")
    if (target.get("coordinate_mode") == "component_local"
            or (target.get("state") == "query_loading" and target.get("id") != "loading-spinner-54")):
        if not (isinstance(target.get("actual_origin_tag"), str)
                and isinstance(target.get("design_origin_element"), str)):
            return unmeasured("component_local icon has no explicit actual_origin_tag/design_origin_element pair")

    semantics = capture.get("node_semantics", {})
    node = semantics.get(tag) if isinstance(semantics, dict) else None
    measurements = capture.get("measurements", {})
    actual_rect = measurements.get(tag) if isinstance(measurements, dict) else None
    if not isinstance(node, dict) or node.get("match_count") != 1:
        return unmeasured("same-capture semantic tag is missing or not unique")
    if not isinstance(actual_rect, dict):
        return unmeasured("same-capture raw semantic bounds are missing")
    try:
        actual_xywh = [actual_rect[field] for field in ("x", "y", "width", "height")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in actual_xywh) or actual_xywh[2] <= 0 or actual_xywh[3] <= 0:
            return unmeasured("same-capture raw semantic bounds are invalid")
        visible, unclipped = node.get("visible_rect_px"), node.get("unclipped_rect_px")
        if (not isinstance(visible, list) or len(visible) != 4 or not isinstance(unclipped, list)
                or len(unclipped) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v) for v in visible + unclipped)):
            return unmeasured("same-capture visible/unclipped semantic bounds are missing or invalid")
        if any(abs(v - u) > 0.01 for v, u in zip(visible, unclipped)):
            return unmeasured("semantic icon is clipped or visible bounds differ from unclipped bounds")
        measured_ltrb = [actual_xywh[0], actual_xywh[1], actual_xywh[0] + actual_xywh[2],
                         actual_xywh[1] + actual_xywh[3]]
        if any(abs(v - m) > 0.01 for v, m in zip(visible, measured_ltrb)):
            return unmeasured("semantic visible bounds do not match current raw measurement")
        ancestors = node.get("ancestor_tags")
        if not isinstance(ancestors, list):
            return unmeasured("same-capture semantic ancestry is missing")
        if target.get("state") == "history":
            for overlay_tag in ("history-actions-panel", "history-actions-cancel"):
                if overlay_tag in ancestors:
                    continue
                overlay = measurements.get(overlay_tag)
                if isinstance(overlay, dict):
                    overlay_ltrb = [overlay.get("x"), overlay.get("y"),
                                    overlay.get("x", 0) + overlay.get("width", 0),
                                    overlay.get("y", 0) + overlay.get("height", 0)]
                    if all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in overlay_ltrb):
                        if (min(measured_ltrb[2], overlay_ltrb[2]) > max(measured_ltrb[0], overlay_ltrb[0])
                                and min(measured_ltrb[3], overlay_ltrb[3]) > max(measured_ltrb[1], overlay_ltrb[1])):
                            return unmeasured(f"semantic icon is covered by measured opaque overlay {overlay_tag}")
        basis = capture.get("basis", {})
        root_ltrb = basis.get("root_rect_px") if isinstance(basis, dict) else None
        if (not isinstance(root_ltrb, list) or len(root_ltrb) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in root_ltrb
        ) or root_ltrb[2] <= 0 or root_ltrb[3] <= 0):
            return unmeasured("capture root clip bounds are missing or invalid")
        root_box = [root_ltrb[0], root_ltrb[1], root_ltrb[0] + root_ltrb[2], root_ltrb[1] + root_ltrb[3]]
        clip_names = {"main-safe-content", "main-message-list", "history-content",
                      "history-scroll-container", "history-actions-panel",
                      "history-actions-cancel", "query-loading"}
        clip_rects = [{"tag": "native-capture-root", "rect_ltrb_px": root_box}]
        for ancestor in ancestors:
            if ancestor not in clip_names:
                continue
            clip_rect = measurements.get(ancestor)
            if not isinstance(clip_rect, dict) or any(
                isinstance(clip_rect.get(k), bool) or not isinstance(clip_rect.get(k), (int, float))
                or not math.isfinite(clip_rect[k]) for k in ("x", "y", "width", "height")
            ) or clip_rect["width"] <= 0 or clip_rect["height"] <= 0:
                return unmeasured(f"current clipping ancestor bounds are missing or invalid: {ancestor}")
            clip_rects.append({"tag": ancestor, "rect_ltrb_px": [clip_rect["x"], clip_rect["y"],
                              clip_rect["x"] + clip_rect["width"], clip_rect["y"] + clip_rect["height"]]})
        if not any(box["tag"] != "native-capture-root" for box in clip_rects):
            return unmeasured("no measurable same-capture clipping ancestor for this semantic icon")

        expansion = target.get("roi_expansion_px", [0, 0, 0, 0])
        if (not isinstance(expansion, list) or len(expansion) != 4 or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in expansion
        )):
            return unmeasured("ROI expansion declaration must be four non-negative integer pixel values")
        spinner_bleed = (target.get("id") == "loading-spinner-54"
                         and target.get("allow_stroke_bleed") is True)
        if any(expansion) and not spinner_bleed:
            # Canvas bounds are not ink bounds. Sampling a small background
            # margin reads the unchanged capture; semantic visibility and all
            # measured containing boundaries still have to hold below.
            reason = target.get("roi_expansion_reason")
            if max(expansion) > 4 or not isinstance(reason, str) or not reason.strip():
                return unmeasured("icon ROI padding requires an explicit reason and at most 4px per side")
        actual_roi = [actual_xywh[0] - expansion[0], actual_xywh[1] - expansion[1],
                      actual_xywh[2] + expansion[0] + expansion[2],
                      actual_xywh[3] + expansion[1] + expansion[3]]
        actual_ltrb = [actual_roi[0], actual_roi[1], actual_roi[0] + actual_roi[2], actual_roi[1] + actual_roi[3]]
        contact_policy = target.get("clip_contact_policy")
        allow_background_contact = (isinstance(contact_policy, dict)
                                    and contact_policy.get("mode") == "background_only"
                                    and not isinstance(contact_policy.get("min_background_px"), bool)
                                    and contact_policy.get("min_background_px") == 1
                                    and isinstance(contact_policy.get("evidence_ref"), str)
                                    and bool(contact_policy["evidence_ref"].strip()))
        if contact_policy is not None and not allow_background_contact:
            return unmeasured("invalid explicit background-only clip contact declaration")
        background_contacts = []
        for clip in clip_rects:
            left, top, right, bottom = clip["rect_ltrb_px"]
            if (actual_ltrb[0] < left or actual_ltrb[1] < top
                    or actual_ltrb[2] > right or actual_ltrb[3] > bottom):
                return unmeasured(f"actual rawROI is outside measured clip {clip['tag']}")
            if (actual_ltrb[0] <= left or actual_ltrb[1] <= top
                    or actual_ltrb[2] >= right or actual_ltrb[3] >= bottom):
                if not allow_background_contact:
                    return unmeasured(f"actual rawROI touches measured clip edge {clip['tag']}")
                # Equality alone is not ink clipping. Full semantic visibility
                # was checked above; all three source/actual ink masks must
                # still have a full background pixel inside the ROI below.
                background_contacts.append(clip)

        design_elements = design.get("elements", {})
        coordinate_mode = target.get("coordinate_mode")
        if safe_bottom_target:
            origin_tag, design_origin_name = "capture-content-bottom", None
            content_rect = basis.get("content_rect_px")
            if not isinstance(content_rect, list) or len(content_rect) != 4:
                return unmeasured("safe-bottom registration requires valid same-capture content_rect_px")
            actual_origin = [root_ltrb[0], content_rect[1] + content_rect[3]]
            design_origin = [frame[0], frame[1] + frame[3]]
            design_origin_name = "design_frame_bottom"
        elif coordinate_mode == "component_local":
            origin_tag = target["actual_origin_tag"]
            design_origin_name = target["design_origin_element"]
            origin_rect = measurements.get(origin_tag)
            if not isinstance(origin_rect, dict):
                return unmeasured(f"explicit same-capture component origin is missing: {origin_tag}")
            actual_origin = [origin_rect.get("x"), origin_rect.get("y")]
        elif target.get("state") == "query_loading" and target.get("id") == "loading-spinner-54":
            origin_tag, design_origin_name = "query-loading", "loading_card"
            origin_rect = measurements.get(origin_tag)
            if not isinstance(origin_rect, dict):
                return unmeasured("same-capture query-loading component origin is missing")
            actual_origin = [origin_rect.get("x"), origin_rect.get("y")]
        elif target.get("state") == "query_loading":
            return unmeasured("query_loading icon has no supported explicit component-local origin")
        elif coordinate_mode == "app_canvas" and target.get("state") == "train_schedule":
            origin_tag, design_origin_name = target.get("app_origin_tag", "main-topbar"), None
            actual_origin = None
        elif coordinate_mode == "app_canvas" and target.get("state") == "history":
            origin_tag, design_origin_name = target.get("app_origin_tag", "history-header"), None
            actual_origin = None
        else:
            return unmeasured("no explicit coordinate-origin policy for this state and target")
        if actual_origin is None:
            origin_node = semantics.get(origin_tag)
            origin_rect = measurements.get(origin_tag)
            if not unique_icon_origin(capture, origin_tag):
                return unmeasured(f"same-capture coordinate origin is missing or non-unique: {origin_tag}")
            if not isinstance(origin_rect, dict):
                return unmeasured(f"same-capture coordinate origin bounds are missing: {origin_tag}")
            visible, unclipped = origin_node.get("visible_rect_px"), origin_node.get("unclipped_rect_px")
            if (not isinstance(visible, list) or len(visible) != 4 or not isinstance(unclipped, list)
                    or len(unclipped) != 4 or any(abs(v - u) > 0.01 for v, u in zip(visible, unclipped))):
                return unmeasured(f"same-capture coordinate origin is clipped or lacks visible bounds: {origin_tag}")
            if any(isinstance(origin_rect.get(k), bool) or not isinstance(origin_rect.get(k), (int, float))
                   or not math.isfinite(origin_rect[k]) for k in ("x", "y", "width", "height")):
                return unmeasured(f"same-capture coordinate origin bounds are invalid: {origin_tag}")
            if any(abs(v - m) > 0.01 for v, m in zip(visible, [origin_rect["x"], origin_rect["y"],
                        origin_rect["x"] + origin_rect["width"], origin_rect["y"] + origin_rect["height"]])):
                return unmeasured(f"same-capture coordinate origin semantics disagree with measurement: {origin_tag}")
            if origin_rect["width"] <= 0 or origin_rect["height"] <= 0:
                return unmeasured(f"same-capture coordinate origin is empty: {origin_tag}")
            actual_origin = [origin_rect["x"], origin_rect["y"]]
        elif origin_tag != "capture-content-bottom":
            origin_node = semantics.get(origin_tag)
            origin_rect = measurements.get(origin_tag)
            if not unique_icon_origin(capture, origin_tag):
                return unmeasured(f"same-capture coordinate origin is missing or non-unique: {origin_tag}")
            if not isinstance(origin_rect, dict) or any(
                isinstance(origin_rect.get(k), bool) or not isinstance(origin_rect.get(k), (int, float))
                or not math.isfinite(origin_rect[k]) for k in ("x", "y", "width", "height")
            ) or origin_rect["width"] <= 0 or origin_rect["height"] <= 0:
                return unmeasured(f"same-capture coordinate origin bounds are invalid: {origin_tag}")
            visible, unclipped = origin_node.get("visible_rect_px"), origin_node.get("unclipped_rect_px")
            if (not isinstance(visible, list) or len(visible) != 4 or not isinstance(unclipped, list)
                    or len(unclipped) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                        or not math.isfinite(v) for v in visible + unclipped)
                    or any(abs(v - u) > 0.01 for v, u in zip(visible, unclipped))):
                return unmeasured(f"same-capture coordinate origin is clipped or lacks visible bounds: {origin_tag}")
            origin_ltrb = [origin_rect["x"], origin_rect["y"], origin_rect["x"] + origin_rect["width"],
                           origin_rect["y"] + origin_rect["height"]]
            if any(abs(v - m) > 0.01 for v, m in zip(visible, origin_ltrb)):
                return unmeasured(f"same-capture coordinate origin semantics disagree with measurement: {origin_tag}")
            actual_origin = [origin_rect["x"], origin_rect["y"]]
        if design_origin_name == "design_frame_bottom":
            pass
        elif design_origin_name is None:
            design_origin = [frame[0], frame[1]]
        else:
            design_origin_rect = design_elements.get(design_origin_name)
            if not isinstance(design_origin_rect, list) or len(design_origin_rect) != 4:
                return unmeasured(f"design coordinate origin is missing: {design_origin_name}")
            design_origin = design_origin_rect[:2]

        metrics = measure_icon_pair(design_image, actual, roi, actual_roi, scale,
                                    target.get("foreground_class"))
        # The metric helper's centroid delta is relative to each ROI. Preserve
        # it as diagnostics, but use the explicit registered-origin calculation
        # below for the acceptance position criterion.
        metrics["roi_local_cross_profile_diagnostic_centroid_delta_by_pair_px"] = metrics.pop("centroid_delta_by_profile_pair_px")
        metrics["roi_local_cross_profile_diagnostic_max_centroid_delta_px"] = metrics.pop("cross_profile_diagnostic_max_centroid_delta_px")
        metrics["roi_local_acceptance_centroid_delta_by_matched_profile_px"] = metrics.pop("acceptance_centroid_delta_by_matched_profile_px")
        metrics["roi_local_acceptance_conservative_centroid_delta_px"] = metrics.pop("acceptance_conservative_centroid_delta_px")
        metrics.pop("conservative_centroid_delta_px")
        def touches_edge(sample: dict, bounds: list[int]) -> bool:
            ink = sample["bounds_px"]
            return ink[0] <= bounds[0] or ink[1] <= bounds[1] or ink[2] >= bounds[0] + bounds[2] or ink[3] >= bounds[1] + bounds[3]
        for profile in ("strict", "base", "loose"):
            if touches_edge(metrics["profiles"][profile]["design"], roi):
                return {**metrics, "target_id": target.get("id"), "state": target.get("state"),
                        "native_tag": tag, "status": "unmeasured",
                        "reason": f"design rawROI or ink touches an edge in {profile} profile",
                        "coordinate_origin": {"design": "design_frame", "actual": origin_tag},
                        "clip_rects": clip_rects}
            if touches_edge(metrics["profiles"][profile]["actual"], actual_roi):
                return {**metrics, "target_id": target.get("id"), "state": target.get("state"),
                        "native_tag": tag, "status": "unmeasured",
                        "reason": f"actual rawROI or ink touches an edge in {profile} profile",
                        "coordinate_origin": {"design": "design_frame", "actual": origin_tag},
                        "clip_rects": clip_rects}
        center_delta_by_pair = {}
        for design_profile in ("strict", "base", "loose"):
            for actual_profile in ("strict", "base", "loose"):
                d = metrics["profiles"][design_profile]["design"]["centroid_px"]
                a = metrics["profiles"][actual_profile]["actual"]["centroid_px"]
                key = f"design:{design_profile}|actual:{actual_profile}"
                center_delta_by_pair[key] = {
                    axis: round(abs((a[i] - actual_origin[i]) - (d[i] - design_origin[i]) * scale), 4)
                    for i, axis in enumerate(("x", "y"))
                }
        matched_center_delta_by_profile = {
            profile: center_delta_by_pair[f"design:{profile}|actual:{profile}"]
            for profile in ("strict", "base", "loose")
        }
        diagnostic_registered_center_delta = {axis: max(v[axis] for v in center_delta_by_pair.values())
                                              for axis in ("x", "y")}
        registered_center_delta = {axis: max(v[axis] for v in matched_center_delta_by_profile.values())
                                   for axis in ("x", "y")}
        metrics.update({
            "target_id": target.get("id"), "state": target.get("state"), "native_tag": tag,
            "status": "passed" if (metrics["acceptance_conservative_shape_delta_px"] <= 1.0
                and max(metrics["acceptance_conservative_dimension_delta_px"].values()) <= 1.0
                and max(registered_center_delta.values()) <= 1.0) else "failed",
            "coordinate_origin": {"design": design_origin_name or "design_frame", "actual": origin_tag},
            "design_registered_origin_px": design_origin, "actual_registered_origin_px": actual_origin,
            "diagnostic_registered_center_delta_by_cross_profile_pair_px": center_delta_by_pair,
            "diagnostic_registered_center_delta_px": diagnostic_registered_center_delta,
            "acceptance_registered_center_delta_by_matched_profile_px": matched_center_delta_by_profile,
            "acceptance_conservative_registered_center_delta_px": registered_center_delta,
            "conservative_registered_center_delta_px": registered_center_delta,
            "registered_center_rule": "design centroid relative to design component/frame origin, versus actual centroid relative to same-capture component/root origin",
            "clip_rects": clip_rects,
            "background_only_clip_contacts": {
                "policy": contact_policy,
                "contacts": background_contacts,
                "source_and_actual_all_profile_ink_strictly_inside_roi": True,
            } if background_contacts else None,
            "raw_actual_roi_capture_px": actual_roi,
            "used_legacy_native_anchor": False,
            "acceptance_scope": "this mapped icon target only; no whole-state or remaining-icon coverage inference",
        })
        return metrics
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return unmeasured(f"icon measurement evidence invalid: {error}")


if args.failure:
    for state in STATES:
        design = targets[state]
        new_entries.append({
            "acc_id": ACC_IDS[state], "module": state,
            "design_ref": f"{design['source']}:{design['frame']}",
            "impl_ref": "android/app/src/androidTest/java/org/openrailfanai/app/NativeAcceptanceCaptureTest.kt",
            "viewport": {"width": 390, "height": 844, "unit": "physical_px", "density": 1.0, "font_scale": 1.0, "width_dp": 390, "height_dp": 844},
            "diff_px": None, "diff_metric": "max abs rect delta over measured mapped nodes; no geometry available",
            "status": "unmeasured", "file": None, "generated_at": now.isoformat(timespec="seconds"),
            "script_version": VERSION, "capture_run_token": args.expected_run_token,
            "unmeasured": [f"capture/build unavailable: {args.failure}"],
        })
else:
    verification_path = CAPTURE_DIR / "verification.json"
    if not verification_path.exists():
        raise SystemExit(f"Native capture verification is missing: {verification_path}")
    verification = json.loads(verification_path.read_text())
    if args.expected_run_token and (verification.get("capture_run_token") != args.expected_run_token
                                   or verification.get("run_token_verified") is not True):
        raise SystemExit("Native capture evidence does not match this acceptance invocation")
    captures = {item["state"]: item for item in verification.get("results", [])}
    if set(captures) != set(STATES):
        raise SystemExit("Native capture verification does not include exactly the required nine states")
    interactions = verification.get("interactions", [])
    copy_checks = verification.get("copy_checks", {})

    for state in STATES:
        design = targets[state]
        capture = captures[state]
        image_path = ROOT / capture["file"]
        with Image.open(image_path) as actual_source:
            actual = actual_source.convert("RGB")
        if actual.size != (390, 844):
            raise SystemExit(f"Native screenshot for {state} is {actual.size}, expected 390x844")
        x, y, width, height = design["frame"]
        design_path = ROOT / design["source"]
        with Image.open(design_path) as original:
            design_image = original.convert("RGB")
            # The user-approved frame bounds extract a design module; both source and capture remain 1:1.
            module = original.crop((x, y, x + width, y + height)).convert("RGB")
        canvas = Image.new("RGB", (module.width + 16 + actual.width, max(module.height, actual.height)), "white")
        canvas.paste(module, (0, 0))
        canvas.paste(actual, (module.width + 16, 0))
        name = f"{ACC_IDS[state]}__{state.replace('_', '-')}__{now:%Y%m%d}.png"
        output = OUT / name
        canvas.save(output)

        measurements = capture.get("measurements", {})
        state_map = measurement_map["states"].get(state, {})
        frame_x, frame_y, frame_width, frame_height = design["frame"]
        scale = 390 / frame_width
        basis_errors = validate_basis(capture.get("basis"), measurements)
        scale_evidence = None
        try:
            scale, scale_evidence = design_scale(frame_width, capture, state_map.get("scale_basis", {}))
        except ValueError as error:
            basis_errors.append(str(error))
        geometry: list[dict] = []
        diagnostic_geometry: list[dict] = []
        missing_geometry: list[str] = []
        layout_failures: list[dict] = []
        touch_failures: list[dict] = []
        missing_touch_evidence: list[str] = []
        for mapping in state_map.get("elements", []):
            selector = mapping["selector"]
            target_name = mapping["designElement"]
            raw_target = design["elements"].get(target_name)
            actual_rect = measurements.get(selector)
            if raw_target is None or actual_rect is None:
                missing_geometry.append(f"{selector} -> {target_name}: {'design target missing' if raw_target is None else 'Compose bounds missing'}")
                if mapping.get("size_rule") == "minimum":
                    missing_touch_evidence.append(f"{selector}: 触控范围证据缺失")
                continue
            expected = {"x": (raw_target[0] - frame_x) * scale, "y": (raw_target[1] - frame_y) * scale,
                        "width": raw_target[2] * scale, "height": raw_target[3] * scale}
            original_expected = dict(expected)
            reference_evidence = None
            reference = mapping.get("design_reference")
            if reference is not None:
                conflict = design_document.get("knownDesignConflicts", {}).get(reference.get("conflict")) if isinstance(reference, dict) else None
                resolved = {key: reference.get(key) for key in ("state", "element")} if isinstance(reference, dict) else None
                recorded_reference = conflict.get("resolved_reference") if isinstance(conflict, dict) else None
                recorded_pair = {key: recorded_reference.get(key) for key in ("state", "element")} if isinstance(recorded_reference, dict) else None
                if (not isinstance(conflict, dict) or recorded_pair != resolved
                        or reference.get("conflict") != f"{state}.{target_name}"):
                    missing_geometry.append(f"{selector}: design reference lacks a matching documented conflict resolution")
                    continue
                alternate = targets.get(resolved["state"], {})
                alternate_rect = alternate.get("elements", {}).get(resolved["element"])
                alternate_frame = alternate.get("frame")
                if not alternate_rect or not alternate_frame or mapping.get("comparison") != "size":
                    missing_geometry.append(f"{selector}: resolved design reference requires a valid size target")
                    continue
                if recorded_reference.get("source_frame") is not None and recorded_reference["source_frame"] != alternate_frame:
                    missing_geometry.append(f"{selector}: resolved source frame does not match the current design reference")
                    continue
                alternate_scale = 390 / alternate_frame[2]
                expected = {"x": (alternate_rect[0]-alternate_frame[0])*alternate_scale,
                            "y": (alternate_rect[1]-alternate_frame[1])*alternate_scale,
                            "width": alternate_rect[2]*alternate_scale, "height": alternate_rect[3]*alternate_scale}
                reference_evidence = {"conflict": reference["conflict"], "resolution": conflict["resolution"],
                                      "state": resolved["state"], "element": resolved["element"],
                                      "source": alternate["source"], "source_rect": alternate_rect,
                                      "frame": alternate_frame, "scale_to_390": round(alternate_scale, 6)}
            anchor_evidence = None
            actual_values = dict(actual_rect)
            origin_x = 0
            origin_y = 0
            origin_tag = state_map.get("actual_origin_tag")
            if origin_tag:
                origin_rect = measurements.get(origin_tag)
                if origin_rect is None:
                    missing_geometry.append(f"{selector}: actual coordinate origin missing ({origin_tag})")
                    continue
                actual_values["x"] -= origin_rect["x"]
                actual_values["y"] -= origin_rect["y"]
                origin_x = origin_rect["x"]
                origin_y = origin_rect["y"]
            comparison = mapping.get("comparison", "rect")
            if mapping.get("vertical_anchor"):
                if (mapping["vertical_anchor"] != "safe_bottom" or comparison != "rect"
                        or state_map.get("coordinate_mode") != "app_canvas" or basis_errors):
                    missing_geometry.append(f"{selector}: safe-bottom anchor requires app-canvas rect and a valid capture basis")
                    continue
                basis = capture["basis"]
                content = basis["content_rect_px"]
                root = basis["root_rect_px"]
                insets = basis["system_insets_applied_px"]
                if (abs(content[1] - (root[1] + insets[1])) > 1
                        or abs(content[1] + content[3] - (root[1] + root[3] - insets[3])) > 1):
                    missing_geometry.append(f"{selector}: anchor content must match actual system safe top and bottom")
                    continue
                try:
                    expected, anchor_evidence = safe_bottom_target(
                        expected, frame_height * scale, content, origin_y)
                except ValueError as error:
                    missing_geometry.append(f"{selector}: {error}")
                    continue
            design_anchor_name = mapping.get("design_anchor")
            actual_anchor_name = mapping.get("actual_anchor")
            if comparison == "relative_rect":
                anchor_target = design["elements"].get(design_anchor_name)
                anchor_rect = measurements.get(actual_anchor_name)
                if anchor_target is None or anchor_rect is None:
                    missing_geometry.append(f"{selector}: relative anchor missing ({design_anchor_name}/{actual_anchor_name})")
                    continue
                expected = {"x": expected["x"] - (anchor_target[0] - frame_x) * scale,
                            "y": expected["y"] - (anchor_target[1] - frame_y) * scale,
                            "width": expected["width"], "height": expected["height"]}
                actual_values["x"] -= anchor_rect["x"]
                actual_values["y"] -= anchor_rect["y"]
            if comparison == "touch_only" and (mapping.get("kind") != "touch_target" or mapping.get("size_rule") != "minimum"):
                missing_geometry.append(f"{selector}: touch-only comparison requires an explicit minimum touch target")
                continue
            fields = [] if comparison == "touch_only" else mapping.get("compare_fields", ["width", "height"] if comparison in ("size", "painted_size") else ["x", "y", "width", "height"])
            paint_evidence = None
            layout_evidence = None
            if comparison in ("painted_size", "painted_rect"):
                expected_text = mapping.get("expected_text")
                observed_text = capture.get("node_text", {}).get(selector)
                clip = measurements.get(mapping.get("actual_clip_tag"))
                native_node = capture.get("node_semantics", {}).get(selector)
                unclipped = native_node.get("unclipped_rect_px") if isinstance(native_node, dict) else None
                visible = native_node.get("visible_rect_px") if isinstance(native_node, dict) else None
                if (not isinstance(unclipped, list) or len(unclipped) != 4 or not isinstance(visible, list)
                        or len(visible) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                            or not math.isfinite(v) for v in unclipped + visible)
                        or any(abs(a-b) > .01 for a,b in zip(visible, unclipped))):
                    missing_geometry.append(f"{selector}: same-capture unclipped and visible text bounds differ or are missing")
                    continue
                if (basis_errors or mapping.get("kind") != "painted_text" or not expected_text
                        or observed_text != expected_text or clip is None
                        or fields != (["width", "height"] if comparison == "painted_size" else ["x", "y", "width", "height"])):
                    missing_geometry.append(f"{selector}: ink comparison requires valid basis, exact tagged text, clip bounds and declared comparison fields")
                    continue
                if (not isinstance(clip, dict) or any(
                    isinstance(clip.get(field), bool) or not isinstance(clip.get(field), (int, float))
                    or not math.isfinite(clip[field]) for field in ("x", "y", "width", "height")
                ) or clip["width"] <= 0 or clip["height"] <= 0):
                    missing_geometry.append(f"{selector}: invalid measured clipping container")
                    continue
                if not (actual_rect["x"] >= clip["x"] and actual_rect["y"] >= clip["y"]
                        and actual_rect["x"] + actual_rect["width"] <= clip["x"] + clip["width"]
                        and actual_rect["y"] + actual_rect["height"] <= clip["y"] + clip["height"]):
                    missing_geometry.append(f"{selector}: text layout is not fully inside its measured clipping container")
                    continue
                layout_evidence = {"source_target_region_px": raw_target, "source_region_scaled_size_px":
                                   {field: round(expected[field], 2) for field in fields},
                                   "actual_layout_rect_px": actual_rect,
                                   "layout_region_delta_px": absolute_geometry_delta(expected, actual_values, fields),
                                   "acceptance_eligible": False,
                                   "reason": "design selection region and text line box are not glyph ink bounds"}
                try:
                    paint_evidence = measure_text_pair(
                        design_image, actual, dict(zip(("x", "y", "width", "height"), raw_target)),
                        actual_rect, scale, mapping.get("foreground_class"))
                except (ValueError, TypeError, KeyError) as error:
                    missing_geometry.append(f"{selector}: {error}")
                    continue
                if not paint_evidence["stable"]:
                    diagnostic_geometry.append({"selector": selector, "paint": paint_evidence, "layout": layout_evidence,
                                                "acceptance_eligible": False})
                    missing_geometry.append(f"{selector}: foreground threshold variation exceeds 2px; ink size remains unmeasured")
                    continue
                expected = {**expected, **paint_evidence["expected_size_px"]}
                actual_values = {**actual_values, **paint_evidence["actual_size_px"]}
                if comparison == "painted_rect":
                    samples = paint_evidence["profiles"]
                    for field, coordinate_index in (("x", 0), ("y", 1)):
                        design_origin = frame_x if field == "x" else frame_y
                        actual_origin = origin_x if field == "x" else origin_y
                        expected[field] = (samples["base"]["design"]["bounds_px"][coordinate_index] - design_origin) * scale
                        actual_values[field] = samples["base"]["actual"]["bounds_px"][coordinate_index] - actual_origin
                        paint_evidence["conservative_delta_px"][field] = round(max(
                            abs((sample["actual"]["bounds_px"][coordinate_index] - actual_origin)
                                - (sample["design"]["bounds_px"][coordinate_index] - design_origin) * scale)
                            for sample in samples.values()), 2)
                        paint_evidence["cross_profile_diagnostic_delta_px"][field] = round(max(
                            abs((a["actual"]["bounds_px"][coordinate_index] - actual_origin)
                                - (d["design"]["bounds_px"][coordinate_index] - design_origin) * scale)
                            for a in samples.values() for d in samples.values()), 2)
                    paint_evidence["scope"] = "text ink bounding-box position and size only; no font identity or glyph-contour inference"
            delta = (paint_evidence["conservative_delta_px"] if paint_evidence
                     else absolute_geometry_delta(expected, actual_values, fields))
            record = {"selector": selector, "design_element": target_name, "kind": mapping["kind"],
                      "comparison": comparison, "coordinate_origin": state_map.get("coordinate_mode"), "actual_origin_tag": state_map.get("actual_origin_tag"),
                      "expected_px": {key: round(expected[key], 2) for key in fields},
                      "actual_px": {key: round(actual_values[key], 2) for key in fields},
                      "delta_px": delta, "tolerance_px": measurement_map["rules"]["tolerance_px"]}
            if paint_evidence:
                record["paint_measurement"] = paint_evidence
                record["layout_diagnostic"] = layout_evidence
                record["text_leaf_match"] = {"expected": expected_text, "actual": observed_text, "exact": True}
                record["acceptance_scope"] = paint_evidence["scope"]
            if reference_evidence:
                record["design_reference"] = reference_evidence
                record["original_expected_px"] = {key: round(original_expected[key], 2) for key in fields}
            if comparison == "touch_only":
                record["acceptance_scope"] = "explicit minimum touch size only; no visual alignment inference"
            if anchor_evidence:
                record["original_expected_px"] = {key: round(original_expected[key], 2) for key in fields}
                record["vertical_anchor"] = anchor_evidence
            if comparison == "diagnostic_only":
                reason = mapping.get("unmeasured_reason", "same-semantic target remains undefined")
                record["acceptance_eligible"] = False
                record["unmeasured_reason"] = reason
                diagnostic_geometry.append(record)
                missing_geometry.append(f"{selector}: {reason}")
                continue
            if mapping.get("size_rule") == "minimum":
                touch_minimum = mapping.get("minimum_touch_px")
                if (not isinstance(touch_minimum, dict) or any(
                    isinstance(touch_minimum.get(field), bool)
                    or not isinstance(touch_minimum.get(field), (int, float))
                    or not math.isfinite(touch_minimum[field]) or touch_minimum[field] <= 0
                    for field in ("width", "height")
                )):
                    missing_geometry.append(f"{selector}: explicit minimum_touch_px is missing or invalid")
                    missing_touch_evidence.append(f"{selector}: 触控下限定义缺失或无效")
                else:
                    touch_check = minimum_touch_size(touch_minimum, actual_values)
                    touch_check["requirement_source"] = "compose-measurement-map.json:minimum_touch_px"
                    record["minimum_touch_size"] = touch_check
                    if not touch_check["compliant"]:
                        touch_failures.append({"selector": selector, "design_element": target_name, **touch_check})
            geometry.append(record)
            if max(delta.values(), default=0) > record["tolerance_px"]:
                layout_failures.append(record)

        icon_evidence = []
        failed_icons = []
        unmeasured_icons = []
        for icon_target in icon_targets_by_state.get(state, []):
            icon_result = measure_icon_target(icon_target, design, design_image, actual,
                                              capture, scale, basis_errors)
            icon_evidence.append(icon_result)
            if icon_result.get("status") == "failed":
                failed_icons.append(icon_result)
            elif icon_result.get("status") != "passed":
                unmeasured_icons.append(icon_result)

        required_copy = state_map.get("copy_expected", [])
        observed_copy = {item["expected"]: item for item in copy_checks.get(state, [])}
        missing_copy = [text for text in required_copy if text not in observed_copy]
        wrong_copy = [text for text in required_copy if text in observed_copy and not observed_copy[text].get("exact_visible_match")]
        def as_ltrb(rect: dict) -> list:
            return [rect["x"], rect["y"], rect["x"]+rect["width"], rect["y"]+rect["height"]]
        copy_clips = {tag: as_ltrb(measurements[tag]) for tag in (
            "main-message-list", "main-safe-content", "history-content", "history-scroll-container") if tag in measurements}
        if not basis_errors:
            root_rect = capture["basis"]["root_rect_px"]
            copy_clips["capture_root"] = [root_rect[0], root_rect[1], root_rect[0]+root_rect[2], root_rect[1]+root_rect[3]]
        copy_overlays = {tag: as_ltrb(measurements[tag]) for tag in (
            "history-actions-panel", "history-actions-cancel") if tag in measurements} if state == "history" else {}
        copy_leaf_evidence = compare_copy(design_copy["states"][state], copy_contract["states"].get(state, []),
                                          capture.get("node_semantics", {}), not basis_errors, copy_clips, copy_overlays)
        required_interactions = state_map.get("interaction_expected", [])
        missing_interactions = [expected for expected in required_interactions if not any(expected in event for event in interactions)]

        design_elements = design.get("elements", {})
        mapped_names = {entry["designElement"] for entry in state_map.get("elements", [])}
        unmapped_design = sorted(set(design_elements) - mapped_names)
        unmeasured = []
        unmeasured.extend(basis_errors)
        if not geometry:
            unmeasured.append("no mapped Compose geometry was measured")
        unmeasured.extend(missing_geometry)
        unmeasured.extend(f"icon target {item.get('target_id')}: {item.get('reason')}"
                          for item in unmeasured_icons)
        if not icon_targets_by_state.get(state):
            unmeasured.append("HR36 defines no individually mapped icon targets for this state; icon coverage remains unmeasured")
        if icon_target_contract_errors:
            unmeasured.extend(f"native icon target contract: {error}" for error in icon_target_contract_errors)
        if icon_coverage_remainder:
            unmeasured.append("global icon coverage remains incomplete: " + ", ".join(icon_coverage_remainder))
        unmeasured.extend(measurement_map.get("unmeasured_evidence", []))
        unmeasured.extend(state_map.get("unmeasured_evidence", []))
        unmeasured.extend(f"design target not mapped: {element}" for element in unmapped_design)
        unmeasured.extend(f"copy evidence missing: {text}" for text in missing_copy)
        unmeasured.extend(copy_leaf_evidence["unmeasured"])
        unmeasured.extend(f"interaction evidence missing: {event}" for event in missing_interactions)

        history_visibility = None
        visibility_failures = list(copy_leaf_evidence.get("failed_visibility", []))
        if state == "history":
            history_visibility = history_footer_visibility(
                capture.get("history_diagnostics"), capture.get("basis") if not basis_errors else None, measurements)
            unmeasured.extend(history_visibility["unmeasured"])
            visibility_failures.extend(history_visibility["failed_nodes"])

        if (layout_failures or touch_failures or wrong_copy or copy_leaf_evidence["failed"]
                or visibility_failures or failed_icons):
            status = "failed"
        elif unmeasured:
            status = "unmeasured"
        else:
            status = "passed"
        observed_deltas = [delta for record in geometry for delta in record["delta_px"].values()]
        entry = {
            "acc_id": ACC_IDS[state], "module": state,
            "design_ref": f"{design['source']}:{design['frame']}", "impl_ref": capture["file"],
            "viewport": {"width": capture["width_px"], "height": capture["height_px"], "unit": "physical_px",
                         "density": capture["density"], "font_scale": capture["font_scale"],
                         "width_dp": capture["width_dp"], "height_dp": capture["height_dp"]},
            "diff_px": round(max(observed_deltas), 2) if observed_deltas else None,
            "diff_metric": "maximum absolute x/y/width/height delta among listed mapped Compose rectangles only; not whole-image pixel difference",
            "status": status, "file": f"acceptance/{name}", "generated_at": now.isoformat(timespec="seconds"),
            "script_version": VERSION, "capture_run_token": verification.get("capture_run_token"),
            "measurement": {"coordinate_mode": state_map.get("coordinate_mode"), "frame_width_scale": round(scale, 6),
                            "capture_basis": capture.get("basis"),
                            "design_scale_basis": scale_evidence,
                            "copy_leaf_evidence": copy_leaf_evidence,
                            "history_footer_visibility": history_visibility,
                            "geometry": geometry, "diagnostic_geometry": diagnostic_geometry,
                            "icon_evidence": icon_evidence, "failed_icons": failed_icons,
                            "unmeasured_icons": unmeasured_icons,
                            "icon_coverage_remainder": icon_coverage_remainder,
                            "copy_expected_count": len(required_copy),
                            "copy_exact_matches": len(required_copy) - len(missing_copy) - len(wrong_copy),
                            "interaction_expected": required_interactions,
                            "interaction_observed": [event for event in interactions if any(key in event for key in required_interactions)]},
            "failed_layout": layout_failures,
            "failed_touch_targets": touch_failures,
            "failed_copy": wrong_copy,
            "failed_copy_leaves": copy_leaf_evidence["failed"],
            "failed_visibility": visibility_failures,
            "failed_icons": failed_icons,
            "unmeasured": unmeasured,
        }
        if acceptance_policy.get("mode") == "harmony_first" and state in acceptance_policy.get("states", []):
            # Keep historical pixel findings intact. Only the expressly relaxed
            # final visual gate changes; current functional/visibility gaps block.
            hard_missing = (basis_errors + missing_touch_evidence + copy_leaf_evidence["unmeasured"]
                            + [f"关键文案证据缺失：{text}" for text in missing_copy]
                            + [f"操作证据缺失：{event}" for event in missing_interactions])
            if history_visibility:
                hard_missing += history_visibility["unmeasured"]
            review_result = evaluate_harmony(
                state, image_path, acceptance_policy, harmony_review,
                touch_failures + wrong_copy + copy_leaf_evidence["failed"] + visibility_failures,
                hard_missing,
            )
            entry["strict_fidelity"] = {"status": status, "unmeasured": unmeasured,
                                        "scope": "旧版像素结果保留为诊断，未经改写。"}
            entry["harmony_acceptance"] = review_result
            entry["status"] = review_result["status"]
            entry["unmeasured"] = review_result["unmeasured"]
        new_entries.append(entry)

# Historical WebUI records used a device-shell baseline. Preserve them, but remove them from current evidence.
for item in index:
    if item.get("acc_id") in {"ACC-T03", "ACC-T04", "ACC-T11"} and item.get("status") != "superseded":
        item["prior_status"] = item.get("status")
        item["status"] = "superseded"
        item["superseded_reason"] = "WebUI/device-shell baseline is not valid evidence for current native app-content acceptance."
failed = [item for item in failed if item.get("acc_id") not in {"ACC-T03", "ACC-T04", "ACC-T11"}]
write_indices(new_entries)
summary = {"index": str(index_path), "failed_index": str(failed_path),
           "generated": [item["file"] for item in new_entries],
           "states_with_unmeasured_evidence": sum(bool(item.get("unmeasured")) for item in new_entries),
           "status": {name: sum(item["status"] == name for item in new_entries) for name in ("passed", "failed", "unmeasured", "superseded")}}
print(json.dumps(summary, ensure_ascii=False))
if any(item["status"] != "passed" for item in new_entries):
    raise SystemExit(1)
