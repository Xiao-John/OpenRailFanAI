"""Standalone regression tests for native_icon_metrics.py."""
from __future__ import annotations

import json
import unittest

from PIL import Image, ImageDraw

from native_icon_metrics import PROFILES, measure_icon_pair


def canvas(size=(24, 24), background=(255, 255, 255)):
    return Image.new("RGB", size, background)


def rect(image, xy, fill):
    ImageDraw.Draw(image).rectangle(xy, fill=fill)


class IconMetricsTest(unittest.TestCase):
    def test_identical_shape_is_profiled_and_has_zero_shape_delta(self):
        design, actual = canvas(), canvas()
        rect(design, (5, 6, 10, 13), (11, 23, 56))
        rect(actual, (5, 6, 10, 13), (11, 23, 56))
        result = measure_icon_pair(design, actual, (0, 0, 24, 24), (0, 0, 24, 24), 1, "dark")
        self.assertEqual(set(result["profiles"]), set(PROFILES))
        self.assertEqual(result["conservative_shape_delta_px"], 0)
        self.assertEqual(result["conservative_dimension_delta_px"], {"width": 0.0, "height": 0.0})
        self.assertEqual(result["profiles"]["base"]["design"]["foreground_pixels"], 48)
        self.assertEqual(result["shape_tolerance_px"], 1.0)
        self.assertIn("boundary_points_px", result["profiles"]["base"]["design"])
        json.dumps(result, allow_nan=False)

    def test_same_source_profile_drift_is_diagnostic_and_matched_self_comparison_is_zero(self):
        image = canvas()
        rect(image, (5, 6, 10, 13), (11, 23, 56))
        # This connected fringe is selected only by the loose dark threshold.
        rect(image, (11, 8, 14, 11), (110, 140, 185))
        result = measure_icon_pair(image, image, (0, 0, 24, 24), (0, 0, 24, 24), 1, "dark")
        self.assertEqual(result["method_version"], 2)
        self.assertGreater(result["cross_profile_diagnostic_max_shape_delta_px"], 1.0)
        self.assertGreater(result["profile_variation_px"]["design"]["centered_contour_hausdorff_px"], 1.0)
        self.assertEqual(result["acceptance_shape_delta_by_matched_profile_px"], {
            "strict": 0.0, "base": 0.0, "loose": 0.0,
        })
        self.assertEqual(result["acceptance_conservative_shape_delta_px"], 0.0)
        self.assertEqual(result["acceptance_conservative_dimension_delta_px"], {
            "width": 0.0, "height": 0.0,
        })
        self.assertEqual(result["acceptance_conservative_centroid_delta_px"], {"x": 0.0, "y": 0.0})

    def test_matched_profile_dimension_deviation_over_one_pixel_is_retained(self):
        design, actual = canvas(), canvas()
        rect(design, (4, 5, 8, 10), (11, 23, 56))
        rect(actual, (4, 5, 10, 10), (11, 23, 56))
        result = measure_icon_pair(design, actual, (0, 0, 24, 24), (0, 0, 24, 24), 1, "dark")
        self.assertGreater(result["acceptance_conservative_dimension_delta_px"]["width"], 1.0)

    def test_translation_changes_centroid_but_not_centered_shape_or_size(self):
        design, actual = canvas(), canvas()
        rect(design, (4, 5, 8, 9), (11, 23, 56))
        rect(actual, (8, 5, 12, 9), (11, 23, 56))
        result = measure_icon_pair(design, actual, (0, 0, 24, 24), (0, 0, 24, 24), 1, "dark")
        self.assertEqual(result["conservative_shape_delta_px"], 0)
        self.assertEqual(result["conservative_dimension_delta_px"], {"width": 0.0, "height": 0.0})
        self.assertEqual(result["conservative_centroid_delta_px"]["x"], 4.0)

    def test_missing_hole_boundary_changes_shape(self):
        design, actual = canvas(), canvas()
        rect(design, (3, 3, 11, 11), (11, 23, 56))
        # Make a 3x3 hole; the inner contour must remain in the design boundary set.
        rect(design, (6, 6, 8, 8), (255, 255, 255))
        rect(actual, (3, 3, 11, 11), (11, 23, 56))
        result = measure_icon_pair(design, actual, (0, 0, 24, 24), (0, 0, 24, 24), 1, "dark")
        design_points = result["profiles"]["base"]["design"]["boundary_points_px"]
        self.assertGreater(len(design_points), len(result["profiles"]["base"]["actual"]["boundary_points_px"]))
        self.assertGreater(result["conservative_shape_delta_px"], 1.0)
        # Same outer dimensions, but the missing inner contour still changes shape.
        self.assertEqual(result["conservative_dimension_delta_px"], {"width": 0.0, "height": 0.0})

    def test_scaled_equivalent_shape_uses_390_coordinate_units(self):
        design, actual = canvas(), canvas((48, 48))
        rect(design, (5, 6, 8, 10), (11, 23, 56))
        rect(actual, (10, 12, 17, 21), (11, 23, 56))
        result = measure_icon_pair(design, actual, (0, 0, 24, 24), (0, 0, 48, 48), 2, "dark")
        self.assertEqual(result["conservative_dimension_delta_px"], {"width": 0.0, "height": 0.0})
        self.assertLessEqual(result["conservative_shape_delta_px"], 1.0)

    def test_empty_invalid_and_out_of_bounds_roi_rejected(self):
        image = canvas()
        for roi in ((0, 0, 0, 2), (0, 0, float("nan"), 2), (-1, 0, 2, 2), (0, 0, 30, 2)):
            with self.subTest(roi=roi):
                with self.assertRaises(ValueError):
                    measure_icon_pair(image, image, roi, (0, 0, 24, 24), 1, "dark")
        with self.assertRaisesRegex(ValueError, "No icon foreground"):
            measure_icon_pair(image, image, (0, 0, 24, 24), (0, 0, 24, 24), 1, "dark")
        with self.assertRaises(ValueError):
            measure_icon_pair(image, image, (0, 0, 24, 24), (0, 0, 24, 24), 0, "dark")

    def test_shared_color_masks_for_all_supported_classes(self):
        samples = {
            "dark": (11, 23, 56),
            "muted": (104, 122, 156),
            "blue": (37, 99, 235),
            "white": (255, 255, 255),
            "red": (240, 82, 79),
            "gray": (120, 120, 120),
        }
        for color_class, ink in samples.items():
            with self.subTest(color_class=color_class):
                # The paired images use the same sample and same ROI, so every
                # profile must select identical foreground pixels on both sides.
                background = (37, 99, 235) if color_class == "white" else (255, 255, 255)
                design, actual = canvas(background=background), canvas(background=background)
                rect(design, (7, 7, 11, 11), ink)
                rect(actual, (7, 7, 11, 11), ink)
                result = measure_icon_pair(design, actual, (0, 0, 24, 24), (0, 0, 24, 24), 1, color_class)
                for profile in PROFILES:
                    d = result["profiles"][profile]["design"]
                    a = result["profiles"][profile]["actual"]
                    self.assertEqual(d["foreground_pixels"], a["foreground_pixels"])
                    self.assertEqual(d["bounds_px"], a["bounds_px"])
                self.assertEqual(result["conservative_shape_delta_px"], 0)

    def test_muted_gray_blue_is_not_classified_as_saturated_blue(self):
        image = canvas()
        rect(image, (7, 7, 11, 11), (104, 122, 156))
        with self.assertRaisesRegex(ValueError, "No icon foreground"):
            measure_icon_pair(image, image, (0, 0, 24, 24), (0, 0, 24, 24), 1, "blue")

    def test_white_mask_excludes_blue_canvas_background(self):
        image = canvas(background=(37, 99, 235))
        rect(image, (8, 8, 12, 12), (255, 255, 255))
        result = measure_icon_pair(image, image, (0, 0, 24, 24), (0, 0, 24, 24), 1, "white")
        self.assertEqual(result["profiles"]["base"]["design"]["foreground_pixels"], 25)

    def test_green_disc_white_check_discards_outer_white_background(self):
        design, actual = canvas((32, 32)), canvas((32, 32))
        for image in (design, actual):
            draw = ImageDraw.Draw(image)
            draw.ellipse((7, 7, 24, 24), fill=(30, 170, 85))
            draw.line(((11, 16), (15, 20), (21, 11)), fill=(255, 255, 255), width=2)
        result = measure_icon_pair(design, actual, (0, 0, 32, 32), (0, 0, 32, 32), 1, "white")
        for profile in PROFILES:
            d = result["profiles"][profile]["design"]
            a = result["profiles"][profile]["actual"]
            self.assertGreater(d["roi_edge_background_removed_pixels"], d["foreground_pixels"])
            self.assertGreater(d["foreground_pixels"], 0)
            self.assertEqual(d["foreground_pixels"], a["foreground_pixels"])

    def test_all_white_roi_is_empty_after_edge_background_removal(self):
        image = canvas((16, 16), (255, 255, 255))
        with self.assertRaisesRegex(ValueError, "after removing .* ROI-edge white background pixels"):
            measure_icon_pair(image, image, (0, 0, 16, 16), (0, 0, 16, 16), 1, "white")

    def test_blue_button_white_arrow_survives_outer_white_removal(self):
        image = canvas((32, 32), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, 25, 25), radius=5, fill=(37, 99, 235))
        draw.line(((12, 16), (20, 16)), fill=(255, 255, 255), width=2)
        draw.line(((17, 13), (20, 16), (17, 19)), fill=(255, 255, 255), width=2)
        result = measure_icon_pair(image, image, (0, 0, 32, 32), (0, 0, 32, 32), 1, "white")
        sample = result["profiles"]["base"]["design"]
        self.assertGreater(sample["roi_edge_background_removed_pixels"], 0)
        self.assertGreater(sample["foreground_pixels"], 0)
        self.assertLess(sample["foreground_pixels"], 32 * 32)

    def test_white_glyph_connected_to_roi_edge_is_not_accepted(self):
        image = canvas((16, 16), (37, 99, 235))
        draw = ImageDraw.Draw(image)
        draw.rectangle((4, 6, 9, 8), fill=(255, 255, 255))
        draw.rectangle((0, 7, 4, 7), fill=(255, 255, 255))
        with self.assertRaisesRegex(ValueError, "No icon foreground pixels"):
            measure_icon_pair(image, image, (0, 0, 16, 16), (0, 0, 16, 16), 1, "white")


if __name__ == "__main__":
    unittest.main()
