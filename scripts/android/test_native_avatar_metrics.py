from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from native_avatar_metrics import measure_avatar_background_pair


class AvatarBackgroundMetricsTest(unittest.TestCase):
    @staticmethod
    def circle_image(size: int = 56, center: tuple[int, int] = (28, 28), radius: int = 17,
                     glyph: bool = True) -> Image.Image:
        image = Image.new("RGB", (size, size), (250, 251, 252))
        draw = ImageDraw.Draw(image)
        draw.ellipse((center[0] - radius, center[1] - radius,
                      center[0] + radius, center[1] + radius), fill=(220, 233, 254))
        if glyph:
            # Saturated blue foreground is distinct from the pale container fill.
            draw.line((center[0] - 5, center[1], center[0] + 5, center[1]),
                      fill=(24, 102, 240), width=3)
            draw.line((center[0], center[1] - 5, center[0], center[1] + 5),
                      fill=(24, 102, 240), width=3)
        return image

    def measure(self, design: Image.Image, actual: Image.Image):
        roi = [4, 4, 48, 48]
        return measure_avatar_background_pair(design, actual, roi, roi, 1.0)

    def test_same_circle_with_saturated_glyph_reports_pixel_diagnostics(self):
        image = self.circle_image()
        result = self.measure(image, image.copy())
        self.assertEqual(result["status"], "measured_diagnostics_only")
        self.assertEqual(set(result["profiles"]), {"strict", "base", "loose"})
        for profile in result["profiles"].values():
            self.assertAlmostEqual(profile["diagnostics"]["diameter_delta_px"], 0, delta=0.2)
            self.assertEqual(profile["diagnostics"]["fill_rgb_delta"], [0, 0, 0])
        self.assertIn("no pass threshold", result["acceptance"])

    def test_different_filled_diameter_is_reported_without_acceptance_claim(self):
        result = self.measure(self.circle_image(radius=17), self.circle_image(radius=14))
        self.assertEqual(result["status"], "measured_diagnostics_only")
        self.assertLess(result["profiles"]["base"]["diagnostics"]["diameter_delta_px"], -4)
        self.assertEqual(result["acceptance"], "not evaluated; this module has no pass threshold")

    def test_circle_touching_sampling_edge_is_unmeasured(self):
        clipped = self.circle_image(size=40, center=(7, 20), radius=13)
        reference = self.circle_image()
        result = measure_avatar_background_pair(reference, clipped, [4, 4, 48, 48],
                                                [0, 0, 40, 40], 1.0)
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("touches sampling ROI edge", result["reason"])

    def test_non_circle_pale_region_is_unmeasured(self):
        image = Image.new("RGB", (56, 56), (250, 251, 252))
        ImageDraw.Draw(image).rectangle((11, 11, 45, 45), fill=(220, 233, 254))
        result = self.measure(image, image.copy())
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("not reliably circular", result["reason"])

    def test_transparent_sampling_region_is_unmeasured(self):
        source = self.circle_image().convert("RGBA")
        source.putpixel((4, 4), (250, 251, 252, 0))
        result = self.measure(source, self.circle_image())
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("transparency", result["reason"])

    def test_glyph_like_saturated_blue_region_is_not_mistaken_for_fill(self):
        image = Image.new("RGB", (56, 56), (250, 251, 252))
        ImageDraw.Draw(image).ellipse((11, 11, 45, 45), fill=(30, 90, 240))
        result = self.measure(image, image.copy())
        self.assertEqual(result["status"], "unmeasured")
        self.assertIn("distinguishable", result["reason"])


if __name__ == "__main__":
    unittest.main()
