#!/usr/bin/env python3
"""Fixed numeric regression cases for native acceptance geometry/touch metrics."""
import unittest

from native_acceptance_metrics import absolute_geometry_delta, minimum_touch_size, safe_bottom_target, design_scale


class AcceptanceMetricsTest(unittest.TestCase):
    def scale_fixture(self):
        return {"basis": {"root_rect_px": [0, 0, 390, 844], "content_rect_px": [0, 24, 390, 772],
                          "system_insets_applied_px": [0, 24, 0, 48]},
                "measurements": {"canvas": {"x": 15, "y": 24, "width": 360, "height": 772}},
                "node_semantics": {"canvas": {"match_count": 1, "visible_rect_px": [15, 24, 375, 796],
                                              "unclipped_rect_px": [15, 24, 375, 796]}}}

    def test_standalone_module_uses_page_content_width_not_full_capture_width(self):
        scale, trace = design_scale(454, self.scale_fixture(), {"mode": "component_canvas", "actual_canvas_tag": "canvas"})
        self.assertAlmostEqual(scale, 360 / 454)
        self.assertEqual(trace["actual_canvas_rect_px"], [15, 24, 360, 772])

    def test_application_frame_cannot_exclude_page_padding(self):
        with self.assertRaisesRegex(ValueError, "page padding"):
            design_scale(438, self.scale_fixture(), {"mode": "application_window", "actual_canvas_tag": "canvas"})

    def test_scale_requires_same_capture_unique_unclipped_canvas(self):
        for mutation in ("missing", "duplicate", "clipped"):
            with self.subTest(mutation=mutation):
                capture = self.scale_fixture()
                if mutation == "missing": capture["node_semantics"].clear()
                elif mutation == "duplicate": capture["node_semantics"]["canvas"]["match_count"] = 2
                else: capture["node_semantics"]["canvas"]["visible_rect_px"][0] = 16
                with self.assertRaises(ValueError):
                    design_scale(454, capture, {"mode": "component_canvas", "actual_canvas_tag": "canvas"})

    def test_application_scale_rejects_invalid_root_and_insets(self):
        for key, value in (("root_rect_px", [0, 0, None, 844]),
                           ("root_rect_px", [0, 0, True, 844]),
                           ("system_insets_applied_px", [0, 24, float("nan"), 48]),
                           ("system_insets_applied_px", [0, -1, 0, 48])):
            capture = self.scale_fixture()
            capture["measurements"]["canvas"] = {"x": 0, "y": 24, "width": 390, "height": 772}
            capture["node_semantics"]["canvas"].update(visible_rect_px=[0, 24, 390, 796], unclipped_rect_px=[0, 24, 390, 796])
            capture["basis"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                design_scale(438, capture, {"mode": "application_window", "actual_canvas_tag": "canvas"})

    def test_explicit_touch_minimum_does_not_replace_visual_geometry(self):
        visual_expected = {"width": 358.77, "height": 44.74}
        actual = {"width": 313, "height": 44}
        self.assertTrue(minimum_touch_size({"width": 44, "height": 44}, actual)["compliant"])
        self.assertEqual(absolute_geometry_delta(visual_expected, actual, ["width", "height"]),
                         {"width": 45.77, "height": 0.74})
        self.assertFalse(minimum_touch_size({"width": 44, "height": 44}, {"width": 313, "height": 40})["compliant"])

    def test_safe_bottom_preserves_design_size_and_independent_origin(self):
        scale = 390 / 406
        expected = {"x": 0, "y": 770 * scale, "width": 404 * scale, "height": 58 * scale}
        adapted, trace = safe_bottom_target(expected, 828 * scale, [0, 24, 390, 772], 24)
        self.assertAlmostEqual(adapted["y"] + 24 + adapted["height"], 796)
        self.assertEqual({key: adapted[key] for key in ("x", "width", "height")},
                         {key: expected[key] for key in ("x", "width", "height")})
        self.assertEqual(trace["design_bottom_gap_px"], 0)
        self.assertEqual(trace["shift_y_px"], -23.37)
        # Remaining product error must not disappear into the adapted target.
        actual = {"x": 0, "y": 706, "width": 390, "height": 58}
        self.assertEqual(absolute_geometry_delta(adapted, actual, ["y", "height"]),
                         {"y": 10.29, "height": 2.29})

    def test_safe_bottom_retains_suggestion_to_input_design_gap_across_insets(self):
        scale = 390 / 406
        base_input = {"x": 0, "y": 770 * scale, "width": 404 * scale, "height": 58 * scale}
        base_suggestions = {"x": 78 * scale, "y": 710 * scale, "width": 261 * scale, "height": 44 * scale}
        for bottom_inset in (0, 24, 48):
            with self.subTest(bottom_inset=bottom_inset):
                content = [0, 24, 390, 844 - 24 - bottom_inset]
                entry, _ = safe_bottom_target(base_input, 828 * scale, content, 24)
                suggestions, _ = safe_bottom_target(base_suggestions, 828 * scale, content, 24)
                self.assertAlmostEqual(entry["y"] + 24 + entry["height"], 844 - bottom_inset)
                self.assertAlmostEqual(entry["y"] - suggestions["y"] - suggestions["height"], 16 * scale)
                self.assertEqual(suggestions["width"], base_suggestions["width"])

    def test_safe_bottom_rejects_invalid_or_unfittable_targets(self):
        expected = {"x": 0, "y": 770, "width": 404, "height": 58}
        for content in ([0, 24, 390, 20], [0, 24, 390, -1], [0, 24, 390, float("nan")]):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    safe_bottom_target(expected, 828, content, 24)
        with self.assertRaises(ValueError):
            safe_bottom_target(expected, 800, [0, 24, 390, 772], 24)

    def test_position_offsets_in_both_directions_are_absolute(self):
        expected = {"x": 20, "y": 30, "width": 100, "height": 48}
        self.assertEqual(absolute_geometry_delta(expected, {**expected, "x": 31}, ["x"]), {"x": 11.0})
        self.assertEqual(absolute_geometry_delta(expected, {**expected, "y": 22}, ["y"]), {"y": 8.0})

    def test_oversized_touch_target_still_has_visual_delta_but_is_touch_compliant(self):
        expected = {"x": 20, "y": 30, "width": 100, "height": 48}
        actual = {"x": 20, "y": 30, "width": 112, "height": 55}
        self.assertEqual(absolute_geometry_delta(expected, actual, ["width", "height"]),
                         {"width": 12.0, "height": 7.0})
        self.assertEqual(minimum_touch_size(expected, actual)["deficit_px"], {"width": 0.0, "height": 0.0})
        self.assertTrue(minimum_touch_size(expected, actual)["compliant"])

    def test_undersized_touch_target_has_visual_delta_and_size_deficit(self):
        expected = {"x": 20, "y": 30, "width": 100, "height": 48}
        actual = {"x": 20, "y": 30, "width": 92, "height": 40}
        self.assertEqual(absolute_geometry_delta(expected, actual, ["width", "height"]),
                         {"width": 8.0, "height": 8.0})
        touch = minimum_touch_size(expected, actual)
        self.assertEqual(touch["deficit_px"], {"width": 8.0, "height": 8.0})
        self.assertFalse(touch["compliant"])


if __name__ == "__main__":
    unittest.main()
