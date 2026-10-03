"""Text foreground comparison invariants; does not write screenshot artifacts."""
import unittest
from PIL import Image, ImageDraw
from native_paint_metrics import measure_text_pair


class TextMaskTests(unittest.TestCase):
    def sample(self, extra=0):
        image = Image.new("RGB", (40, 24), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 15 + extra, 15), fill=(10, 20, 40))
        draw.line((7, 8, 7, 15), fill=(90, 100, 120))
        draw.line((16 + extra, 8, 16 + extra, 15), fill=(90, 100, 120))
        return image

    def measure(self, design, actual):
        roi = {"x": 0, "y": 0, "width": 40, "height": 24}
        return measure_text_pair(design, actual, roi, roi, 1, "dark")

    def test_self_comparison_is_zero_under_every_matched_profile(self):
        result = self.measure(self.sample(), self.sample())
        self.assertEqual({"width": 0, "height": 0}, result["conservative_delta_px"])
        self.assertEqual(2, result["cross_profile_diagnostic_delta_px"]["width"])
        self.assertTrue(result["stable"])

    def test_actual_three_pixel_width_error_still_fails_two_pixel_rule(self):
        result = self.measure(self.sample(), self.sample(3))
        self.assertEqual(3, result["conservative_delta_px"]["width"])
        self.assertTrue(result["stable"])

    def test_missing_foreground_is_not_a_pass(self):
        with self.assertRaises(ValueError):
            self.measure(self.sample(), Image.new("RGB", (40, 24), "white"))

    def test_invalid_scale_is_rejected(self):
        roi = {"x": 0, "y": 0, "width": 40, "height": 24}
        for scale in (True, 0, float("nan")):
            with self.assertRaises(ValueError):
                measure_text_pair(self.sample(), self.sample(), roi, roi, scale, "dark")


if __name__ == "__main__":
    unittest.main()
