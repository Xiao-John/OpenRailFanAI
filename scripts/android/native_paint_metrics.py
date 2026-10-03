"""Read text ink from existing formal PNGs, without creating or altering images."""
from __future__ import annotations

import math
from typing import Mapping

from PIL import Image

PROFILES = ("strict", "base", "loose")


def _foreground(rgb: tuple[int, int, int], color: str, profile: str) -> bool:
    r, g, b = rgb
    level = PROFILES.index(profile)
    if color == "red":
        return r > 1.4 * g and r > 1.4 * b and g < (160, 180, 200)[level]
    if color == "dark":
        return r < (80, 100, 120)[level] and g < (110, 130, 150)[level] and b < (155, 175, 190)[level]
    if color == "muted":
        blue = (b - r > (18, 12, 8)[level] and g - r > (4, 2, 0)[level]
                and r < (170, 190, 220)[level])
        dark = (r < (80, 110, 120)[level] and g < (110, 130, 150)[level]
                and b < (155, 170, 190)[level])
        return blue or dark
    raise ValueError("Unknown text foreground class")


def _roi(image: Image.Image, rect: Mapping[str, float]) -> tuple[int, int, int, int]:
    values = [rect.get(field) for field in ("x", "y", "width", "height")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Text ROI requires finite numeric geometry")
    x, y, width, height = values
    if width <= 0 or height <= 0:
        raise ValueError("Text ROI requires positive dimensions")
    bounds = (math.floor(x), math.floor(y), math.ceil(x + width), math.ceil(y + height))
    if bounds[0] < 0 or bounds[1] < 0 or bounds[2] > image.width or bounds[3] > image.height:
        raise ValueError("Text ROI is not fully inside the formal PNG")
    return bounds


def _ink(image: Image.Image, bounds: tuple[int, int, int, int], color: str, profile: str) -> dict:
    pixels = image.load()
    left, top, right, bottom = bounds
    min_x, min_y, max_x, max_y, count = right, bottom, left - 1, top - 1, 0
    for y in range(top, bottom):
        for x in range(left, right):
            if _foreground(pixels[x, y], color, profile):
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
                count += 1
    if not count:
        raise ValueError(f"No text foreground pixels in {profile} profile")
    return {"bounds_px": [min_x, min_y, max_x + 1, max_y + 1],
            "width": max_x + 1 - min_x, "height": max_y + 1 - min_y,
            "foreground_pixels": count}


def measure_text_pair(design: Image.Image, actual: Image.Image,
                      design_rect: Mapping[str, float], actual_rect: Mapping[str, float],
                      scale: float, color: str) -> dict:
    """Compare ink sizes under the same three masks, keeping all ROI/layout evidence."""
    if color not in ("dark", "muted", "red"):
        raise ValueError("Text foreground class must be explicitly declared")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Text comparison requires a positive source-frame scale")
    design_roi, actual_roi = _roi(design, design_rect), _roi(actual, actual_rect)
    design_rgb, actual_rgb = design.convert("RGB"), actual.convert("RGB")
    samples = {
        profile: {"design": _ink(design_rgb, design_roi, color, profile),
                  "actual": _ink(actual_rgb, actual_roi, color, profile)}
        for profile in PROFILES
    }
    # Apply the same foreground definition to both sides. All three matched
    # profiles must pass; cross-profile differences remain diagnostic evidence.
    expected = {field: samples["base"]["design"][field] * scale for field in ("width", "height")}
    measured = {field: samples["base"]["actual"][field] for field in ("width", "height")}
    delta = {field: round(max(abs(samples[p]["actual"][field] - samples[p]["design"][field] * scale)
                             for p in PROFILES), 2)
             for field in ("width", "height")}
    cross_delta = {field: round(max(abs(samples[a]["actual"][field] - samples[d]["design"][field] * scale)
                                   for a in PROFILES for d in PROFILES), 2)
                   for field in ("width", "height")}
    variation = {
        side: {field: round((max(samples[p][side][field] for p in PROFILES)
                            - min(samples[p][side][field] for p in PROFILES))
                           * (scale if side == "design" else 1), 2)
               for field in ("width", "height")}
        for side in ("design", "actual")
    }
    return {
        "method": "common foreground masks; half-open pixel bounds; worst of all three matched profiles",
        "method_version": 2, "color_class": color, "profiles": samples,
        "threshold_rules": {
            "dark": "R<(80,100,120), G<(110,130,150), B<(155,175,190)",
            "muted": "(B-R>(18,12,8), G-R>(4,2,0), R<(170,190,220)) OR (R<(80,110,120), G<(110,130,150), B<(155,170,190))",
            "red": "R>1.4G AND R>1.4B AND G<(160,180,200)",
            "profile_order": list(PROFILES),
        },
        "design_roi_px": list(design_roi), "actual_roi_px": list(actual_roi),
        "source_scale_to_390": scale, "expected_size_px": expected,
        "actual_size_px": measured, "conservative_delta_px": delta,
        "cross_profile_diagnostic_delta_px": cross_delta,
        "threshold_variation_px": variation,
        "stable": max(v for fields in variation.values() for v in fields.values()) <= 2,
        "scope": "text ink size only; no font identity, glyph contour, position, or whole-state inference",
    }
