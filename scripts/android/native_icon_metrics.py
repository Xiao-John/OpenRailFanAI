"""Read-only foreground icon contour measurements from design/formal PNGs."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from PIL import Image

PROFILES = ("strict", "base", "loose")
COLOR_CLASSES = ("dark", "muted", "blue", "white", "red", "gray")


def _foreground(rgb: tuple[int, int, int], color_class: str, profile: str) -> bool:
    r, g, b = rgb
    i = PROFILES.index(profile)
    if color_class == "dark":
        return r < (80, 100, 120)[i] and g < (110, 130, 150)[i] and b < (155, 175, 190)[i]
    if color_class == "muted":
        blueish = (b - r > (18, 12, 8)[i] and g - r > (4, 2, 0)[i]
                   and r < (170, 190, 220)[i])
        dark = (r < (80, 110, 120)[i] and g < (110, 130, 150)[i]
                and b < (155, 170, 190)[i])
        return blueish or dark
    if color_class == "blue":
        # Keeps saturated blue glyphs distinct from the muted gray-blue class.
        return (b - r > (90, 75, 60)[i] and b - g > (55, 45, 35)[i]
                and b > (145, 135, 125)[i])
    if color_class == "white":
        # Use only for a glyph ROI inside a blue Canvas background; the high
        # channel floor excludes the blue Canvas itself.
        return (min(r, g, b) > (220, 210, 200)[i]
                and max(r, g, b) - min(r, g, b) < (24, 32, 40)[i])
    if color_class == "red":
        return r > 1.4 * g and r > 1.4 * b and g < (160, 180, 200)[i]
    if color_class == "gray":
        # Neutral mid/dark ink; bright neutral surfaces belong to the ROI, not ink.
        return (max(r, g, b) - min(r, g, b) <= (14, 19, 24)[i]
                and min(r, g, b) > (18, 14, 10)[i]
                and max(r, g, b) < (185, 210, 235)[i])
    raise ValueError("Unknown icon foreground class")


def _rect_bounds(image: Image.Image, rect_xywh: Any, label: str) -> tuple[int, int, int, int]:
    if isinstance(rect_xywh, Mapping):
        values = [rect_xywh.get(k) for k in ("x", "y", "width", "height")]
    elif isinstance(rect_xywh, Sequence) and not isinstance(rect_xywh, (str, bytes)):
        values = list(rect_xywh)
    else:
        raise ValueError(f"{label} ROI must be xywh geometry")
    if len(values) != 4 or any(isinstance(v, bool) or not isinstance(v, Real)
                              or not math.isfinite(float(v)) for v in values):
        raise ValueError(f"{label} ROI requires four finite numeric values")
    x, y, width, height = map(float, values)
    if width <= 0 or height <= 0:
        raise ValueError(f"{label} ROI requires positive dimensions")
    right_edge, bottom_edge = x + width, y + height
    if not math.isfinite(right_edge) or not math.isfinite(bottom_edge):
        raise ValueError(f"{label} ROI edge is not finite")
    bounds = (math.floor(x), math.floor(y), math.ceil(right_edge), math.ceil(bottom_edge))
    if (bounds[0] < 0 or bounds[1] < 0 or bounds[2] > image.width or bounds[3] > image.height
            or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
        raise ValueError(f"{label} ROI must be non-empty and fully inside its image")
    return bounds


def _white_roi_edge_background(candidates: set[tuple[int, int]],
                               bounds: tuple[int, int, int, int]) -> set[tuple[int, int]]:
    """Return white-mask pixels 4-connected to the ROI perimeter.

    White glyph pixels that connect to the ROI exterior cannot be separated
    safely from white canvas background; they are removed and may leave the
    profile empty, which the caller reports as unmeasured.
    """
    left, top, right, bottom = bounds
    pending = [(x, y) for x, y in candidates
               if x == left or x == right - 1 or y == top or y == bottom - 1]
    connected = set(pending)
    while pending:
        x, y = pending.pop()
        for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if neighbor in candidates and neighbor not in connected:
                connected.add(neighbor)
                pending.append(neighbor)
    return connected


def _measure_ink(image: Image.Image, bounds: tuple[int, int, int, int],
                 color_class: str, profile: str) -> dict[str, Any]:
    left, top, right, bottom = bounds
    pixels = image.load()
    candidates = {(x, y) for y in range(top, bottom) for x in range(left, right)
                  if _foreground(pixels[x, y], color_class, profile)}
    edge_background = (_white_roi_edge_background(candidates, bounds)
                       if color_class == "white" else set())
    ink = candidates - edge_background
    if not ink:
        if color_class == "white":
            raise ValueError(f"No icon foreground pixels in {profile} profile after removing "
                             f"{len(edge_background)} ROI-edge white background pixels")
        raise ValueError(f"No icon foreground pixels in {profile} profile")
    min_x, min_y, max_x, max_y = right, bottom, left - 1, top - 1
    sum_x = sum_y = 0.0
    for x, y in ink:
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x), max(max_y, y)
        sum_x += x + 0.5
        sum_y += y + 0.5
    count = len(ink)
    centroid = (sum_x / count, sum_y / count)
    boundary = sorted((x, y) for x, y in ink
                      if any((x + dx, y + dy) not in ink
                             for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))))
    # Coordinates are absolute image pixel centers; points along hole edges are included.
    return {
        "bounds_px": [min_x, min_y, max_x + 1, max_y + 1],
        "width_px": max_x + 1 - min_x,
        "height_px": max_y + 1 - min_y,
        "foreground_pixels": count,
        "roi_edge_background_removed_pixels": len(edge_background),
        "centroid_px": [round(centroid[0], 4), round(centroid[1], 4)],
        "centroid_local_390_px": [round((centroid[0] - left), 4),
                                   round((centroid[1] - top), 4)],
        "boundary_points_px": [[x + 0.5, y + 0.5] for x, y in boundary],
    }


def _centered_boundary(sample: dict[str, Any], roi: tuple[int, int, int, int],
                       factor: float) -> list[tuple[float, float]]:
    cx, cy = sample["centroid_px"]
    left, top, _, _ = roi
    return [((x - cx) * factor, (y - cy) * factor)
            for x, y in sample["boundary_points_px"]]


def _hausdorff(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    def directed(source: list[tuple[float, float]], target: list[tuple[float, float]]) -> float:
        largest = 0.0
        for x, y in source:
            closest_sq = min((x - tx) ** 2 + (y - ty) ** 2 for tx, ty in target)
            largest = max(largest, closest_sq)
        return math.sqrt(largest)
    return max(directed(a, b), directed(b, a))


def measure_icon_pair(design: Image.Image, actual: Image.Image,
                      design_rect_xywh: Any, actual_rect_xywh: Any,
                      scale: float, color_class: str) -> dict[str, Any]:
    """Compare icon ink contours in three shared masks.

    Rectangles are half-open xywh pixel geometry. ``scale`` maps design-image
    pixels into the 390-wide comparison coordinate space; actual pixels are
    already in that space. ``white`` is intended only for a glyph ROI inside a
    blue Canvas. The returned values are measurements, never whole-screen pass.
    """
    if color_class not in COLOR_CLASSES:
        raise ValueError("Icon foreground class must be explicitly declared")
    if (isinstance(scale, bool) or not isinstance(scale, Real)
            or not math.isfinite(float(scale)) or scale <= 0):
        raise ValueError("Icon comparison requires a positive finite scale")
    if not isinstance(design, Image.Image) or not isinstance(actual, Image.Image):
        raise ValueError("Icon inputs must be readable Pillow images")
    d_roi = _rect_bounds(design, design_rect_xywh, "design")
    a_roi = _rect_bounds(actual, actual_rect_xywh, "actual")
    d_rgb, a_rgb = design.convert("RGB"), actual.convert("RGB")
    profiles: dict[str, Any] = {
        profile: {"design": _measure_ink(d_rgb, d_roi, color_class, profile),
                  "actual": _measure_ink(a_rgb, a_roi, color_class, profile)}
        for profile in PROFILES
    }

    shape_by_pair: dict[str, float] = {}
    dimension_by_pair: dict[str, dict[str, float]] = {}
    centroid_by_pair: dict[str, dict[str, float]] = {}
    for dp in PROFILES:
        for ap in PROFILES:
            key = f"design:{dp}|actual:{ap}"
            ds, ac = profiles[dp]["design"], profiles[ap]["actual"]
            d_contour = _centered_boundary(ds, d_roi, float(scale))
            a_contour = _centered_boundary(ac, a_roi, 1.0)
            shape_by_pair[key] = round(_hausdorff(d_contour, a_contour), 4)
            dimension_by_pair[key] = {
                axis: round(abs(ac[f"{axis}_px"] - ds[f"{axis}_px"] * float(scale)), 4)
                for axis in ("width", "height")
            }
            dc = ds["centroid_local_390_px"]
            acent = ac["centroid_local_390_px"]
            centroid_by_pair[key] = {
                axis: round(abs(acent[i] - dc[i] * float(scale)), 4)
                for i, axis in enumerate(("x", "y"))
            }

    # Keep all cross-profile combinations as diagnostics, but compare only
    # corresponding masks for acceptance. This prevents threshold drift within
    # one raster from being counted as a drawing difference.
    acceptance_shape_by_profile = {
        profile: shape_by_pair[f"design:{profile}|actual:{profile}"] for profile in PROFILES
    }
    acceptance_dimension_by_profile = {
        profile: dimension_by_pair[f"design:{profile}|actual:{profile}"] for profile in PROFILES
    }
    acceptance_centroid_by_profile = {
        profile: centroid_by_pair[f"design:{profile}|actual:{profile}"] for profile in PROFILES
    }

    def variation(side: str) -> dict[str, Any]:
        factor = float(scale) if side == "design" else 1.0
        samples = [profiles[p][side] for p in PROFILES]
        scalar_fields = ("width_px", "height_px")
        result: dict[str, Any] = {
            field: round((max(s[field] for s in samples) - min(s[field] for s in samples))
                         * factor, 4)
            for field in scalar_fields
        }
        result["foreground_pixel_count_range"] = [
            min(s["foreground_pixels"] for s in samples),
            max(s["foreground_pixels"] for s in samples),
        ]
        result["centroid_local_390_px"] = {
            axis: round((max(s["centroid_local_390_px"][i] for s in samples)
                         - min(s["centroid_local_390_px"][i] for s in samples)) * factor, 4)
            for i, axis in enumerate(("x", "y"))
        }
        contours = [_centered_boundary(profiles[p][side], d_roi if side == "design" else a_roi,
                                       factor) for p in PROFILES]
        pairwise = (_hausdorff(contours[i], contours[j])
                    for i in range(len(contours)) for j in range(i + 1, len(contours)))
        result["centered_contour_hausdorff_px"] = round(max(pairwise, default=0.0), 4)
        return result

    return {
        "method": "three shared foreground masks; white class removes ROI-edge-connected 4-neighbor background; half-open ROI; pixel-center 4-neighbor boundary incl. holes; centered bidirectional Hausdorff",
        "method_version": 2,
        "color_class": color_class,
        "profiles": profiles,
        "threshold_rules": {
            "dark": "R<(80,100,120), G<(110,130,150), B<(155,175,190)",
            "muted": "blue-ish: B-R>(18,12,8), G-R>(4,2,0), R<(170,190,220) OR dark rule",
            "blue": "B-R>(90,75,60), B-G>(55,45,35), B>(145,135,125)",
            "white": "min(R,G,B)>(220,210,200), channel spread<(24,32,40); remove 4-connected candidate pixels seeded at ROI edges; blue Canvas glyph ROI only",
            "red": "R>1.4G AND R>1.4B AND G<(160,180,200)",
            "gray": "channel spread<=(14,19,24), min>(18,14,10), max<(185,210,235)",
            "profile_order": list(PROFILES),
        },
        "design_roi_px": list(d_roi),
        "actual_roi_px": list(a_roi),
        "source_scale_to_390": float(scale),
        "shape_tolerance_px": 1.0,
        "shape_delta_by_profile_pair_px": shape_by_pair,
        "cross_profile_diagnostic_max_shape_delta_px": max(shape_by_pair.values()),
        "acceptance_shape_delta_by_matched_profile_px": acceptance_shape_by_profile,
        "acceptance_conservative_shape_delta_px": max(acceptance_shape_by_profile.values()),
        "conservative_shape_delta_px": max(acceptance_shape_by_profile.values()),
        "dimension_delta_by_profile_pair_px": dimension_by_pair,
        "cross_profile_diagnostic_max_dimension_delta_px": {
            axis: max(v[axis] for v in dimension_by_pair.values()) for axis in ("width", "height")
        },
        "acceptance_dimension_delta_by_matched_profile_px": acceptance_dimension_by_profile,
        "acceptance_conservative_dimension_delta_px": {
            axis: max(v[axis] for v in acceptance_dimension_by_profile.values())
            for axis in ("width", "height")
        },
        "conservative_dimension_delta_px": {
            axis: max(v[axis] for v in acceptance_dimension_by_profile.values())
            for axis in ("width", "height")
        },
        "centroid_delta_by_profile_pair_px": centroid_by_pair,
        "cross_profile_diagnostic_max_centroid_delta_px": {
            axis: max(v[axis] for v in centroid_by_pair.values()) for axis in ("x", "y")
        },
        "acceptance_centroid_delta_by_matched_profile_px": acceptance_centroid_by_profile,
        "acceptance_conservative_centroid_delta_px": {
            axis: max(v[axis] for v in acceptance_centroid_by_profile.values()) for axis in ("x", "y")
        },
        "conservative_centroid_delta_px": {
            axis: max(v[axis] for v in acceptance_centroid_by_profile.values()) for axis in ("x", "y")
        },
        "profile_variation_px": {side: variation(side) for side in ("design", "actual")},
        "scope": "icon ink geometry only; measurements do not establish glyph identity, semantic mapping, or whole-state/screen acceptance",
    }
