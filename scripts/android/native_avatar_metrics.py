"""Pixel-only measurements for filled avatar backgrounds, separate from glyph ink."""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from numbers import Real
from statistics import median
from typing import Any

from PIL import Image

PROFILES = ("strict", "base", "loose")
# Minimum per-channel darkening from the local neutral outside background.
# These are segmentation profiles, not acceptance tolerances.
_PROFILE_DELTA = {"strict": 8, "base": 7, "loose": 6}


def _rect(image: Image.Image, values: Any, label: str) -> tuple[int, int, int, int]:
    if (not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != 4
            or any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(float(v))
                   for v in values)):
        raise ValueError(f"{label} sampling ROI must contain four finite xywh numbers")
    x, y, w, h = map(float, values)
    if w < 7 or h < 7:
        raise ValueError(f"{label} sampling ROI is too small for a fill boundary and background halo")
    bounds = (math.floor(x), math.floor(y), math.ceil(x + w), math.ceil(y + h))
    if bounds[0] < 0 or bounds[1] < 0 or bounds[2] > image.width or bounds[3] > image.height:
        raise ValueError(f"{label} sampling ROI must remain inside the original image")
    return bounds


def _background(rgb: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float, float] | None:
    l, t, r, b = box
    ring = [rgb.getpixel((x, y)) for y in range(t, b) for x in range(l, r)
            if x in (l, r - 1) or y in (t, b - 1)]
    # A neutral local background provides the reference for the tinted fill.
    neutral = [p for p in ring if max(p) - min(p) <= 12 and min(p) >= 180]
    if len(neutral) < max(8, len(ring) // 3):
        return None
    return tuple(float(median(p[i] for p in neutral)) for i in range(3))


def _fill_mask(rgb: Image.Image, box: tuple[int, int, int, int], bg: tuple[float, float, float],
               delta: int) -> set[tuple[int, int]]:
    l, t, r, b = box
    out: set[tuple[int, int]] = set()
    pixels = rgb.load()
    for y in range(t, b):
        for x in range(l, r):
            red, green, blue = pixels[x, y]
            # Circle fill is a pale, neutral blue. Excluding low-red/saturated
            # pixels separates the person/train glyph without erasing fill holes.
            if (red >= 120 and green >= 135 and blue >= 145
                    and bg[0] - red >= delta and bg[1] - green >= delta * 0.55
                    and blue >= red + 1):
                out.add((x, y))
    return out


def _exterior_background(mask: set[tuple[int, int]], box: tuple[int, int, int, int]
                         ) -> set[tuple[int, int]]:
    l, t, r, b = box
    exterior: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque()
    for x in range(l, r):
        for p in ((x, t), (x, b - 1)):
            if p not in mask and p not in exterior:
                exterior.add(p); queue.append(p)
    for y in range(t + 1, b - 1):
        for p in ((l, y), (r - 1, y)):
            if p not in mask and p not in exterior:
                exterior.add(p); queue.append(p)
    while queue:
        x, y = queue.popleft()
        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if l <= q[0] < r and t <= q[1] < b and q not in mask and q not in exterior:
                exterior.add(q); queue.append(q)
    return exterior


def _solve3(a: list[list[float]], b: list[float]) -> tuple[float, float, float] | None:
    m = [row[:] + [rhs] for row, rhs in zip(a, b)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(m[row][col]))
        if abs(m[pivot][col]) < 1e-10:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        factor = m[col][col]
        m[col] = [v / factor for v in m[col]]
        for row in range(3):
            if row == col:
                continue
            factor = m[row][col]
            m[row] = [m[row][i] - factor * m[col][i] for i in range(4)]
    return m[0][3], m[1][3], m[2][3]


def _fit_circle(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(points) < 20:
        return None
    # Algebraic least-squares circle fit; glyph-hole edges are excluded before this.
    rhs = [0.0] * 3
    n = [[0.0] * 3 for _ in range(3)]
    for x, y in points:
        v = [x, y, 1.0]
        z = x * x + y * y
        for i in range(3):
            rhs[i] += v[i] * z
            for j in range(3):
                n[i][j] += v[i] * v[j]
    fit = _solve3(n, rhs)
    if fit is None:
        return None
    aa, bb, cc = fit
    cx, cy = aa / 2.0, bb / 2.0
    radius_sq = cc + cx * cx + cy * cy
    if radius_sq <= 0:
        return None
    radius = math.sqrt(radius_sq)
    residual = median(abs(math.hypot(x - cx, y - cy) - radius) for x, y in points)
    return cx, cy, radius, residual


def _profile(image: Image.Image, box: tuple[int, int, int, int], delta: int) -> dict[str, Any]:
    if "A" in image.getbands():
        alpha = image.getchannel("A").crop(box)
        if alpha.getextrema()[0] < 255:
            raise ValueError("sampling ROI contains transparency; filled boundary is not fully observed")
    rgb = image.convert("RGB")
    bg = _background(rgb, box)
    if bg is None:
        raise ValueError("outside ROI does not contain enough neutral background samples")
    mask = _fill_mask(rgb, box, bg, delta)
    if not mask:
        raise ValueError("pale filled region was not distinguishable from background")
    l, t, r, b = box
    if any(x in (l, r - 1) or y in (t, b - 1) for x, y in mask):
        raise ValueError("filled-region mask touches sampling ROI edge; outside halo is incomplete")
    exterior = _exterior_background(mask, box)
    boundary = sorted((x + 0.5, y + 0.5) for x, y in mask
                      if any((x + dx, y + dy) in exterior
                             for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))))
    fit = _fit_circle(boundary)
    if fit is None:
        raise ValueError("insufficient external fill boundary for a circle fit")
    cx, cy, radius, residual = fit
    if radius < 3 or residual > 1.5:
        raise ValueError("external fill boundary is incomplete or not reliably circular")
    # Color is sampled from a middle annulus, avoiding both anti-aliased edge
    # pixels and the central glyph. The 3-channel median is diagnostic only.
    annulus = [rgb.getpixel((x, y)) for x, y in mask
               if 0.48 * radius <= math.hypot(x + 0.5 - cx, y + 0.5 - cy) <= 0.78 * radius]
    if len(annulus) < 12:
        raise ValueError("too few fill-color pixels outside the central glyph")
    fill_rgb = [int(round(median(p[i] for p in annulus))) for i in range(3)]
    return {"center_px": [round(cx, 4), round(cy, 4)], "radius_px": round(radius, 4),
            "diameter_px": round(2 * radius, 4), "median_boundary_residual_px": round(residual, 4),
            "external_boundary_points": len(boundary), "fill_rgb_median": fill_rgb,
            "fill_color_samples": len(annulus), "background_rgb_median": [round(v, 2) for v in bg]}


def measure_avatar_background_pair(design: Image.Image, actual: Image.Image,
                                   design_sampling_roi_xywh: Any,
                                   actual_sampling_roi_xywh: Any,
                                   scale: float) -> dict[str, Any]:
    """Return three-profile circle geometry/color diagnostics or a conservative unmeasured result.

    Both ROIs must include the entire fill plus visible outside-background halo.
    ``scale`` maps source-image pixels to actual capture pixels. No semantic
    bounds or shape declarations are used as pixel evidence.
    """
    if not isinstance(design, Image.Image) or not isinstance(actual, Image.Image):
        raise ValueError("avatar measurement requires readable Pillow images")
    if (isinstance(scale, bool) or not isinstance(scale, Real)
            or not math.isfinite(float(scale)) or scale <= 0):
        raise ValueError("avatar measurement requires a positive finite scale")
    dbox = _rect(design, design_sampling_roi_xywh, "design")
    abox = _rect(actual, actual_sampling_roi_xywh, "actual")
    results: dict[str, Any] = {}
    try:
        for profile in PROFILES:
            delta = _PROFILE_DELTA[profile]
            results[profile] = {"design": _profile(design, dbox, delta),
                               "actual": _profile(actual, abox, delta)}
    except ValueError as exc:
        return {"status": "unmeasured", "reason": str(exc), "profiles": results,
                "method_version": 1, "segmentation_profiles": dict(_PROFILE_DELTA)}
    for profile, pair in results.items():
        d, a = pair["design"], pair["actual"]
        pair["diagnostics"] = {
            "diameter_delta_px": round(a["diameter_px"] - d["diameter_px"] * float(scale), 4),
            "center_delta_px": [round((a["center_px"][i] - abox[i]) -
                                       (d["center_px"][i] - dbox[i]) * float(scale), 4)
                                for i in range(2)],
            "fill_rgb_delta": [a["fill_rgb_median"][i] - d["fill_rgb_median"][i] for i in range(3)],
        }
    return {"status": "measured_diagnostics_only", "profiles": results,
            "method_version": 1, "segmentation_profiles": dict(_PROFILE_DELTA),
            "acceptance": "not evaluated; this module has no pass threshold"}
