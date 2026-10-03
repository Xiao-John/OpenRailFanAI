"""Focused tests for the build consumer's icon-evidence gate, without running it."""
from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from native_icon_metrics import measure_icon_pair
from native_acceptance_metrics import unique_icon_origin


SCRIPT = Path(__file__).with_name("build-native-acceptance.py")
TREE = ast.parse(SCRIPT.read_text())
FUNCTION = next(node for node in TREE.body
                if isinstance(node, ast.FunctionDef) and node.name == "measure_icon_target")
NAMESPACE = {"math": math, "Image": Image, "measure_icon_pair": measure_icon_pair,
             "unique_icon_origin": unique_icon_origin}
exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), str(SCRIPT), "exec"), NAMESPACE)
measure_target = NAMESPACE["measure_icon_target"]


def rect(image, xy, color=(11, 23, 56)):
    ImageDraw.Draw(image).rectangle(xy, fill=color)


def fixture(offset=0):
    design_image = Image.new("RGB", (20, 20), "white")
    actual_image = Image.new("RGB", (20, 20), "white")
    rect(design_image, (7, 7, 9, 9))
    rect(actual_image, (7 + offset, 9, 9 + offset, 11))
    design = {"source": "desi1.png", "frame": [0, 0, 20, 20], "elements": {}}
    target = {"id": "schedule-date-calendar-18", "state": "train_schedule", "design_source": "desi1.png",
              "design_frame": {"rect_xywh": [0, 0, 20, 20]}, "roi": [5, 5, 8, 8],
              "native_tag": "icon-tag", "foreground_class": "dark", "coordinate_mode": "app_canvas",
              # Deliberately stale and conflicting: consumer must ignore it.
              "native_anchor": {"rect_capture_px": [1, 1, 4, 4]}}
    rect_measurement = {"x": 5, "y": 7, "width": 8, "height": 8}
    capture = {
        "basis": {"root_rect_px": [0, 0, 20, 20]},
        "measurements": {"icon-tag": rect_measurement,
                         "main-safe-content": {"x": 0, "y": 0, "width": 20, "height": 20},
                         "main-message-list": {"x": 0, "y": 4, "width": 20, "height": 16},
                         "main-topbar": {"x": 0, "y": 2, "width": 20, "height": 5}},
        "node_semantics": {
            "icon-tag": {"match_count": 1, "visible_rect_px": [5, 7, 13, 15],
                         "unclipped_rect_px": [5, 7, 13, 15],
                         # Body ancestry deliberately excludes the topbar origin.
                         "ancestor_tags": ["main-message-list", "main-safe-content", "native-capture-root"]},
            "main-topbar": {"match_count": 1, "visible_rect_px": [0, 2, 20, 7],
                            "unclipped_rect_px": [0, 2, 20, 7], "ancestor_tags": ["main-safe-content"]},
        },
    }
    return target, design, design_image, actual_image, capture


class IconConsumerGateTest(unittest.TestCase):
    def call_measure(self, values):
        target, design, design_image, actual_image, capture = values
        return measure_target(target, design, design_image, actual_image, capture, 1.0, [])

    def boundary_fixture(self):
        values = fixture()
        values[-1]["measurements"]["main-message-list"] = {"x": 5, "y": 7, "width": 8, "height": 8}
        return values

    def declare_background_contact(self, values):
        values[0]["clip_contact_policy"] = {"mode": "background_only", "min_background_px": 1, "evidence_ref": "verified-source-and-capture"}

    def test_default_canvas_clip_contact_remains_unmeasured(self):
        result = self.call_measure(self.boundary_fixture())
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("touches measured clip edge", result["reason"])

    def test_explicit_background_contact_requires_all_ink_inside(self):
        values = self.boundary_fixture()
        self.declare_background_contact(values)
        result = self.call_measure(values)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["background_only_clip_contacts"]["source_and_actual_all_profile_ink_strictly_inside_roi"])
        self.assertEqual(1, len(result["background_only_clip_contacts"]["contacts"]))

    def test_background_contact_never_allows_ink_touching_roi_edge(self):
        values = self.boundary_fixture()
        self.declare_background_contact(values)
        rect(values[3], (5, 9, 6, 11))
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("actual rawROI or ink touches", result["reason"])

    def test_background_contact_never_allows_roi_outside_clip(self):
        values = self.boundary_fixture()
        self.declare_background_contact(values)
        values[-1]["measurements"]["main-message-list"]["x"] = 6
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("outside measured clip", result["reason"])

    def test_background_contact_rejects_boolean_margin(self):
        values = self.boundary_fixture()
        self.declare_background_contact(values)
        values[0]["clip_contact_policy"]["min_background_px"] = True
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("invalid explicit", result["reason"])

    def test_current_unique_semantic_bounds_pass_and_legacy_anchor_is_ignored(self):
        result = self.call_measure(fixture())
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["used_legacy_native_anchor"])
        self.assertEqual(result["raw_actual_roi_capture_px"], [5, 7, 8, 8])
        self.assertEqual(result["coordinate_origin"], {"design": "design_frame", "actual": "main-topbar"})
        self.assertEqual(result["acceptance_registered_center_delta_by_matched_profile_px"], {
            "strict": {"x": 0.0, "y": 0.0},
            "base": {"x": 0.0, "y": 0.0},
            "loose": {"x": 0.0, "y": 0.0},
        })
        self.assertEqual(result["conservative_registered_center_delta_px"], {"x": 0.0, "y": 0.0})

    def test_profile_drift_is_reported_without_failing_same_raster(self):
        values = fixture()
        target, _, design_image, actual_image, _ = values
        # Add the same loose-only fringe at matching icon-local coordinates.
        rect(design_image, (10, 8, 11, 10), color=(110, 140, 185))
        rect(actual_image, (10, 10, 11, 12), color=(110, 140, 185))
        result = self.call_measure(values)
        self.assertEqual(result["status"], "passed")
        self.assertGreater(result["cross_profile_diagnostic_max_shape_delta_px"], 1.0)
        self.assertGreater(result["profile_variation_px"]["design"]["centered_contour_hausdorff_px"], 1.0)
        self.assertEqual(result["acceptance_conservative_shape_delta_px"], 0.0)
        self.assertEqual(result["acceptance_conservative_dimension_delta_px"], {
            "width": 0.0, "height": 0.0,
        })

    def test_history_popup_uses_its_own_measured_panel_not_background_page(self):
        values = fixture()
        target, _, _, _, capture = values
        target.update(state="history", id="history-rename-edit-20")
        capture["measurements"]["history-header"] = capture["measurements"]["main-topbar"]
        capture["node_semantics"]["history-header"] = capture["node_semantics"]["main-topbar"]
        capture["node_semantics"]["icon-tag"]["ancestor_tags"] = ["history-actions-panel", "native-capture-root"]
        capture["measurements"]["history-actions-panel"] = {"x": 0, "y": 0, "width": 20, "height": 20}
        result = self.call_measure(values)
        self.assertEqual(result["status"], "passed")
        self.assertIn("history-actions-panel", [clip["tag"] for clip in result["clip_rects"]])
        del capture["measurements"]["history-actions-panel"]
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("history-actions-panel", result["reason"])

    def test_topbar_center_uses_design_and_current_capture_header_origins(self):
        values = fixture()
        target, design, design_image, actual_image, capture = values
        design["elements"]["topbar"] = [0, 0, 20, 5]
        target["native_tag"] = "header-icon"
        target["roi"] = [5, 5, 8, 8]
        # Translate both screenshot ROI and glyph by the measured header origin.
        actual_image = Image.new("RGB", (20, 20), "white")
        rect(actual_image, (7, 12, 9, 14))
        actual_rect = {"x": 5, "y": 10, "width": 8, "height": 8}
        capture["measurements"].update({
            "header-icon": actual_rect,
            "main-topbar": {"x": 0, "y": 5, "width": 20, "height": 5},
            "main-safe-content": {"x": 0, "y": 5, "width": 20, "height": 20},
        })
        capture["node_semantics"] = {
            "header-icon": {"match_count": 1, "visible_rect_px": [5, 10, 13, 18],
                            "unclipped_rect_px": [5, 10, 13, 18],
                            "ancestor_tags": ["main-topbar", "main-safe-content", "native-capture-root"]},
            "main-topbar": {"match_count": 1, "visible_rect_px": [0, 5, 20, 10],
                            "unclipped_rect_px": [0, 5, 20, 10], "ancestor_tags": ["main-safe-content"]},
        }
        result = measure_target(target, design, design_image, actual_image, capture, 1.0, [])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["coordinate_origin"], {"design": "design_frame", "actual": "main-topbar"})

    def test_body_icon_uses_state_header_origin_without_header_ancestry(self):
        result = self.call_measure(fixture())
        self.assertEqual(result["coordinate_origin"]["actual"], "main-topbar")
        self.assertEqual(result["conservative_registered_center_delta_px"]["y"], 0)

    def test_history_body_icon_uses_history_header_without_header_ancestry(self):
        values = fixture()
        target, design, design_image, actual_image, capture = values
        target["state"] = "history"
        capture["measurements"]["history-scroll-container"] = {"x": 0, "y": 4, "width": 20, "height": 16}
        capture["node_semantics"]["icon-tag"]["ancestor_tags"] = [
            "history-scroll-container", "native-capture-root"]
        capture["measurements"]["history-header"] = {"x": 0, "y": 2, "width": 20, "height": 5}
        capture["node_semantics"]["history-header"] = {
            "match_count": 1, "visible_rect_px": [0, 2, 20, 7],
            "unclipped_rect_px": [0, 2, 20, 7], "ancestor_tags": ["history-scroll-container"]}
        result = self.call_measure(values)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["coordinate_origin"], {"design": "design_frame", "actual": "history-header"})

    def test_explicit_component_local_origin_works_in_any_state(self):
        values = fixture()
        target, design, design_image, actual_image, capture = values
        target.update({"state": "query_details", "coordinate_mode": "component_local",
                       "actual_origin_tag": "details-card-anchor",
                       "design_origin_element": "details_card"})
        design["elements"]["details_card"] = [4, 4, 12, 12]
        capture["measurements"]["details-card-anchor"] = {"x": 4, "y": 6, "width": 12, "height": 12}
        capture["node_semantics"]["details-card-anchor"] = {
            "match_count": 1, "visible_rect_px": [4, 6, 16, 18],
            "unclipped_rect_px": [4, 6, 16, 18], "ancestor_tags": ["main-message-list"]}
        result = self.call_measure(values)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["coordinate_origin"], {
            "design": "details_card", "actual": "details-card-anchor"})

    def test_component_local_origin_requires_fresh_semantics(self):
        values = fixture()
        target, design, design_image, actual_image, capture = values
        target.update({"state": "query_details", "coordinate_mode": "component_local",
                       "actual_origin_tag": "details-card-anchor",
                       "design_origin_element": "details_card"})
        design["elements"]["details_card"] = [4, 4, 12, 12]
        capture["measurements"]["details-card-anchor"] = {"x": 4, "y": 6, "width": 12, "height": 12}
        capture["node_semantics"]["details-card-anchor"] = {
            "match_count": 2, "visible_rect_px": [4, 6, 16, 18],
            "unclipped_rect_px": [4, 6, 16, 18], "ancestor_tags": ["main-message-list"]}
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("non-unique", result["reason"])

    def test_send_can_use_an_explicit_component_pair_without_claiming_safe_bottom_position(self):
        values = fixture()
        target, design, _, _, capture = values
        target.update(native_tag="main-send-icon", coordinate_mode="component_local",
                      actual_origin_tag="main-fixed-input", design_origin_element="fixed_input")
        capture["node_semantics"]["main-send-icon"] = capture["node_semantics"].pop("icon-tag")
        capture["measurements"]["main-send-icon"] = capture["measurements"].pop("icon-tag")
        capture["measurements"]["main-fixed-input"] = {"x": 4, "y": 6, "width": 12, "height": 12}
        capture["node_semantics"]["main-fixed-input"] = {
            "match_count": 1, "visible_rect_px": [4, 6, 16, 18],
            "unclipped_rect_px": [4, 6, 16, 18], "ancestor_tags": ["main-safe-content"]}
        design["elements"]["fixed_input"] = [4, 4, 12, 12]
        result = self.call_measure(values)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["coordinate_origin"], {"design": "fixed_input", "actual": "main-fixed-input"})

    def test_declared_sampling_margin_still_requires_fresh_visibility_and_parent_bounds(self):
        values = fixture()
        target = values[0]
        target.update(roi_expansion_px=[1, 1, 1, 1], roi_expansion_reason="保留画布外背景余量")
        self.assertEqual(self.call_measure(values)["status"], "passed")
        target.pop("roi_expansion_reason")
        self.assertEqual(self.call_measure(values)["status"], "unmeasured")
        target["roi_expansion_reason"] = "保留画布外背景余量"
        values[4]["measurements"]["main-message-list"]["x"] = 5
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("outside measured clip", result["reason"])

    def test_missing_or_non_unique_tag_never_falls_back_to_old_anchor(self):
        values = fixture()
        values[0]["native_tag"] = "missing-tag"
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("not unique", result["reason"])
        values = fixture()
        values[4]["node_semantics"]["icon-tag"]["match_count"] = 2
        self.assertEqual(self.call_measure(values)["status"], "unmeasured")

    def test_query_stop_without_explicit_component_anchor_stays_unmeasured(self):
        values = fixture()
        values[0]["id"] = "loading-stop-18"
        values[0]["state"] = "query_loading"
        values[0]["native_tag"] = "main-stop-icon"
        self.assertIn("no explicit actual_origin_tag/design_origin_element", self.call_measure(values)["reason"])

    def test_safe_bottom_send_without_adaptation_declaration_stays_unmeasured(self):
        values = fixture()
        values[0]["id"] = "fixed-input-send-22"
        values[0]["native_tag"] = "main-send-icon"
        self.assertIn("safe-bottom calibrated", self.call_measure(values)["reason"])

    def test_clipping_or_mismatched_visible_measurement_is_unmeasured(self):
        values = fixture()
        values[4]["node_semantics"]["icon-tag"]["visible_rect_px"] = [5, 7, 12, 15]
        self.assertEqual(self.call_measure(values)["status"], "unmeasured")
        values = fixture()
        values[4]["measurements"]["main-safe-content"] = {"x": 0, "y": 0, "width": 12, "height": 20}
        self.assertIn("outside measured clip", self.call_measure(values)["reason"])

    def test_missing_clip_ancestry_is_unmeasured(self):
        values = fixture()
        values[4]["node_semantics"]["icon-tag"]["ancestor_tags"] = ["native-capture-root"]
        self.assertIn("no measurable", self.call_measure(values)["reason"])

    def test_registered_center_deviation_fails_after_valid_shape_measurement(self):
        result = self.call_measure(fixture(offset=2))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["acceptance_conservative_shape_delta_px"], 0)
        self.assertGreater(result["acceptance_conservative_registered_center_delta_px"]["x"], 1)
        self.assertGreater(result["diagnostic_registered_center_delta_px"]["x"], 1)

    def test_matched_contour_deviation_over_one_pixel_fails(self):
        values = fixture()
        design_image, actual_image = values[2], values[3]
        # Preserve the registered translation and outer bounds while adding a
        # 2x2 inner contour to the design only.
        rect(design_image, (6, 7, 10, 11))
        rect(actual_image, (6, 9, 10, 13))
        rect(design_image, (8, 9, 9, 10), color="white")
        result = self.call_measure(values)
        self.assertEqual(result["status"], "failed")
        self.assertGreater(result["acceptance_conservative_shape_delta_px"], 1.0)

    def test_design_ink_touching_roi_edge_is_unmeasured(self):
        values = fixture()
        values[0]["roi"] = [7, 7, 3, 3]
        result = self.call_measure(values)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("touches an edge", result["reason"])

    def test_source_roi_touching_frame_is_allowed_when_ink_has_roi_padding(self):
        values = fixture()
        target, design, design_image, actual_image, capture = values
        design["frame"] = [5, 0, 15, 20]
        target["design_frame"]["rect_xywh"] = [5, 0, 15, 20]
        target["roi"] = [5, 5, 8, 8]  # ROI begins exactly at source-frame x.
        capture["measurements"]["main-topbar"] = {"x": 5, "y": 2, "width": 15, "height": 5}
        capture["node_semantics"]["main-topbar"]["visible_rect_px"] = [5, 2, 20, 7]
        capture["node_semantics"]["main-topbar"]["unclipped_rect_px"] = [5, 2, 20, 7]
        result = self.call_measure(values)
        self.assertEqual(result["status"], "passed")

    def test_design_only_target_stays_unmeasured(self):
        values = fixture()
        values[0]["native_tag"] = None
        values[0]["unmeasured_reason"] = "no native glyph"
        self.assertEqual(self.call_measure(values)["status"], "unmeasured")


if __name__ == "__main__":
    unittest.main()
