"""Focused regression tests for native history viewport and footer visibility evidence."""
from __future__ import annotations

import unittest

from native_history_visibility import history_footer_visibility


class HistoryVisibilityViewportTest(unittest.TestCase):
    def capture(self):
        basis = {
            "schema_version": 1,
            "coordinate_origin": "capture_root",
            "phase": "before_interaction",
            "root_rect_px": [0, 0, 390, 844],
            "content_rect_px": [0, 24, 390, 772],
            "system_insets_platform_px": [0, 66, 0, 132],
            "system_insets_applied_px": [0, 24, 0, 48],
            "platform_density": 2.75,
            "local_density": 1,
            "font_scale": 1,
            "content_tag": "history-content",
            "scroll_mode": "continuous",
            "scroll": {
                "scroll_offset_px": 0,
                "scroll_max_px": 0,
                "viewport_start_px": 24,
                "viewport_end_px": 734,
            },
        }
        measurements = {
            "history-content": {"x": 0, "y": 24, "width": 390, "height": 772},
            "history-scroll-container": {"x": 15, "y": 24, "width": 360, "height": 710},
            "history-actions-panel": {"x": 15, "y": 555, "width": 360, "height": 137},
            "history-actions-cancel": {"x": 15, "y": 699, "width": 360, "height": 51},
            "history-settings-label": {"x": 48, "y": 755, "width": 70, "height": 20},
            "history-help-label": {"x": 312, "y": 755, "width": 56, "height": 20},
            "history-settings-icon": {"x": 22, "y": 756, "width": 19, "height": 19},
            "history-help-icon": {"x": 286, "y": 756, "width": 19, "height": 19},
            "history-footer-divider": {"x": 202, "y": 756, "width": 1, "height": 19},
        }
        diagnostics = {
            "scroll_kind": "continuous_vertical_scroll",
            "scroll_offset_px": 0,
            "scroll_max_px": 0,
            "viewport_bounds_px": [15, 24, 375, 734],
            "safe_content_bounds_px": [0, 24, 390, 796],
            "footer_visible_labels": [
                {"selector": "history-settings-label", "bounds_px": [48, 755, 118, 775]},
                {"selector": "history-help-label", "bounds_px": [312, 755, 368, 775]},
            ],
            "footer_visible_icons": [
                {"selector": "history-settings-icon", "bounds_px": [22, 756, 41, 775]},
                {"selector": "history-help-icon", "bounds_px": [286, 756, 305, 775]},
            ],
            "footer_divider_bounds_px": [202, 756, 203, 775],
        }
        return diagnostics, basis, measurements

    def test_accepts_360px_viewport_inside_390px_safe_content_when_measurement_matches(self):
        diagnostics, basis, measurements = self.capture()
        evidence = history_footer_visibility(diagnostics, basis, measurements)
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["safe_content_ltrb_px"], [0, 24, 390, 796])
        self.assertTrue(all(node["fully_inside_safe_content"] for node in evidence["nodes"]))
        self.assertEqual(evidence["failed_nodes"], [])
        self.assertEqual(evidence["unmeasured"], [])

    def test_rejects_viewport_that_disagrees_with_measured_scroll_container(self):
        diagnostics, basis, measurements = self.capture()
        diagnostics["viewport_bounds_px"] = [0, 24, 390, 734]
        evidence = history_footer_visibility(diagnostics, basis, measurements)
        self.assertEqual(evidence["status"], "unmeasured")
        self.assertIn("disagrees with measured history-scroll-container", evidence["unmeasured"][0])

    def test_rejects_matching_viewport_outside_safe_content(self):
        diagnostics, basis, measurements = self.capture()
        diagnostics["viewport_bounds_px"] = [10, 20, 370, 730]
        measurements["history-scroll-container"] = {"x": 10, "y": 20, "width": 360, "height": 710}
        diagnostics["safe_content_bounds_px"] = [0, 24, 390, 796]
        evidence = history_footer_visibility(diagnostics, basis, measurements)
        self.assertEqual(evidence["status"], "unmeasured")
        self.assertIn("contained in measured safe content", evidence["unmeasured"][0])

    def test_rejects_diagnostic_safe_rect_that_disagrees_with_capture_basis(self):
        diagnostics, basis, measurements = self.capture()
        diagnostics["safe_content_bounds_px"] = [15, 24, 375, 796]
        evidence = history_footer_visibility(diagnostics, basis, measurements)
        self.assertEqual(evidence["status"], "unmeasured")
        self.assertIn("safe content disagrees with the capture basis", evidence["unmeasured"][0])


if __name__ == "__main__":
    unittest.main()
