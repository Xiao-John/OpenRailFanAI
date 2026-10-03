"""Verify code-vector tracing retains all source foreground cells and holes."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("design_paths", Path(__file__).with_name("generate-design-paths.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def signed_area(path):
    return sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(path, path[1:] + path[:1])) / 2


class BoundaryPathTests(unittest.TestCase):
    def check_mask(self, mask, contours):
        paths = module.boundary_paths(mask)
        self.assertEqual(contours, len(paths))
        self.assertEqual(len(mask), sum(signed_area(path) for path in paths))
        return paths

    def test_solid_block(self):
        paths = self.check_mask({(x, y) for x in range(3) for y in range(2)}, 1)
        self.assertEqual(4, len(paths[0]))

    def test_window_hole_is_preserved(self):
        paths = self.check_mask({(x, y) for x in range(3) for y in range(3)} - {(1, 1)}, 2)
        self.assertTrue(any(signed_area(p) < 0 for p in paths))

    def test_diagonal_junction_keeps_two_contours(self):
        self.check_mask({(0, 0), (1, 1)}, 2)

    def test_enclosed_fill_retains_window_without_exterior_background(self):
        image = module.Image.new("RGB", (5, 5), (255, 255, 255))
        ring = {(x, y) for x in range(1, 4) for y in range(1, 4)} - {(2, 2)}
        for point in ring: image.putpixel(point, (20, 90, 240))
        target = {"roi": [0, 0, 5, 5], "color": "blue"}
        self.assertEqual(ring, module.source_paint_cells(image, target))
        target["enclosed_fill"] = True
        self.assertEqual(ring | {(2, 2)}, module.source_paint_cells(image, target))

    def test_concave_shape_preserves_area(self):
        self.check_mask({(0, 0), (1, 0), (0, 1)}, 1)

    def test_hairline_contains_only_complete_source_ink(self):
        target = module.TARGETS["historyEdit"]
        image = module.Image.open(module.ROOT / target["source"]).convert("RGB")
        strips = module.source_hairlines(image, target)
        self.assertEqual([[3, 21, 19, 1]], [s["rect"] for s in strips])
        mask = module.source_paint_cells(image, target)
        for strip in strips:
            x, y, w, h = strip["rect"]
            self.assertTrue({(ix, iy) for iy in range(y, y+h) for ix in range(x, x+w)} <= mask)
            self.assertTrue(module._foreground(strip["source_rgb"], target["color"], "strict"))
        self.assertEqual([], module.source_hairlines(image, {**target, "pixel_hairlines": False}))

    def test_source_layers_preserve_original_profile_membership(self):
        for name, target in module.TARGETS.items():
            image = module.Image.open(module.ROOT / target["source"]).convert("RGB")
            layers = module.source_layers(image, target)
            for profile, mask, color in layers:
                self.assertTrue(module._foreground(color, target["color"], profile), name)
                for stricter in ("strict", "base", "loose"):
                    if stricter == profile: break
                    self.assertFalse(module._foreground(color, target["color"], stricter), name)
                self.assertEqual(len(mask), sum(signed_area(p) for p in module.boundary_paths(mask)), name)

    def test_palette_preserves_source_shades_and_complete_ink(self):
        for name, target in module.TARGETS.items():
            image = module.Image.open(module.ROOT / target["source"]).convert("RGB")
            x, y, w, h = target["roi"]
            seen = set()
            for membership, cells, color in module.source_palette_layers(image, target):
                self.assertEqual(membership, tuple(module._foreground(color, target["color"], p) for p in ("strict", "base", "loose")))
                self.assertFalse(seen & cells)
                seen |= cells
                for ix, iy in cells:
                    original = image.getpixel((x+ix, y+iy))
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(original,color)), 3, name)
            expected = module.source_paint_cells(image, target)
            self.assertEqual(expected, seen, name)


if __name__ == "__main__": unittest.main()
