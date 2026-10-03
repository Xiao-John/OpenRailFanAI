"""Measure application-icon ink against unmodified 390px Edge captures.

Source rectangles are bounded in icon-targets.json. Device illustrations and
design annotations are excluded by the target list and application-frame origin.
This script rescales the original crop, extracts colored icon ink, and overlays
it with screenshot ink. It reports both edge and centroid error; unresolved
masks never pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = HERE / "icon-comparisons"
OUT.mkdir(exist_ok=True)
TARGETS = json.loads((HERE / "icon-targets.json").read_text())
VERIFICATION = json.loads((HERE / "screenshots/verification.json").read_text())
DESIGN = json.loads((HERE / "design-targets.json").read_text())
RESULTS = {r["state"]: r for r in VERIFICATION["results"] if "iconInventory" in r}


def central_component(mask: np.ndarray) -> np.ndarray:
    """Ignore white page corners around a white icon on a round blue button."""
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    groups = []
    for start_y, start_x in zip(*np.nonzero(mask)):
        if visited[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        pixels = []
        while stack:
            y, x = stack.pop()
            pixels.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < height and 0 <= xx < width and mask[yy, xx] and not visited[yy, xx]:
                        visited[yy, xx] = True
                        stack.append((yy, xx))
        groups.append(pixels)
    if not groups:
        return mask
    interior = [group for group in groups if not any(y in (0, height-1) or x in (0, width-1) for y, x in group)]
    pool = interior or groups
    selected = [group for group in pool if len(group) >= 2]
    if not selected:
        selected = [min(pool, key=lambda group: (
            (sum(x for _, x in group) / len(group) - (width-1)/2)**2
            + (sum(y for y, _ in group) / len(group) - (height-1)/2)**2,
        ))]
    result = np.zeros_like(mask, dtype=bool)
    for group in selected:
        for y, x in group:
            result[y, x] = True
    return result


def mask_for(image: Image.Image, kind: str, *, actual=False) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB")).astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    if kind == "green":
        return (g > r + 45) & (g > b + 2) & (g > 110)
    if kind == "amber":
        return (r > 170) & (g > 95) & (g < r - 35) & (b < 100)
    if actual and kind == "white":
        return central_component((r > 110) & (g > 155) & (b > 190))
    if actual and kind == "red":
        return (r > g + 50) & (r > b + 30) & (r > 115)
    if actual:
        return ((r < 145) & (g < 155) & (b < 170)) | ((b > r + 25) & (b > g + 5) & (b > 95))
    if kind == "blue":
        return (b > r + 38) & (b > g + 12) & (b > 95)
    if kind == "red":
        return (r > g + 65) & (r > b + 45) & (r > 140)
    if kind == "white":
        return central_component((r > 110) & (g > 155) & (b > 190))
    if kind == "muted":
        return (b > r + 9) & (b > g - 5) & (r < 160) & (b < 205)
    if kind == "dark":
        return (r < 105) & (g < 115) & (b < 145)
    raise ValueError(kind)


def ink(mask: np.ndarray, x: float, y: float):
    yy, xx = np.nonzero(mask)
    if not len(xx):
        return None
    return {
        "edges": [round(float(x + xx.min()), 2), round(float(y + yy.min()), 2),
                  round(float(x + xx.max() + 1), 2), round(float(y + yy.max() + 1), 2)],
        "center": [round(float(x + xx.mean()), 2), round(float(y + yy.mean()), 2)],
        "pixels": int(len(xx)),
    }


def frame_for(target):
    state = target["state"]
    frame = DESIGN["states"][state]["frame"]
    return frame, 390 / frame[2]


def expected_origin(target, result, scale):
    if "pageRect" in target:
        frame = DESIGN["states"][target["state"]]["frame"]
        return -frame[0] * scale, -frame[1] * scale
    frame = DESIGN["states"][target["state"]]["frame"]
    if target["basis"] == "phone":
        return -frame[0] * scale, -frame[1] * scale
    checks = result["checks"]
    if target["basis"] == "component" or target["state"] in ("query_loading", "batch_partial"):
        check = checks[0]
    else:
        asset = target["asset"]
        key = "followup_actions" if asset in ("search.svg", "calendar.svg") else (
            "return_to_bottom_cue" if asset == "down.svg" else "fixed_input_and_send")
        check = next((c for c in checks if c.get("element") == key), None)
        if check is None:
            return None
    raw = check.get("targetRaw")
    page = check.get("measuredViewport")
    if not raw or not page:
        return None
    return page["x"] - raw[0] * scale, page["y"] - raw[1] * scale


def compare(target, ordinal):
    state = target["state"]
    result = RESULTS[state]
    candidates = [i for i in result["iconInventory"] if i["asset"] == target["asset"]
                  and (not target.get("parent") or i["parent"] == target["parent"])
                  and i["visible"] and i["y"] >= 0 and i["y"] < 844]
    index = target.get("index", 0)
    label = f"{state}-{ordinal:02d}-{target['asset'][:-4]}"
    if index >= len(candidates) and "pageRect" not in target:
        return {"id": label, "result": "unresolved", "reason": "visible icon asset not found"}
    actual_box = candidates[index] if index < len(candidates) else dict(
        x=target["pageRect"][0], y=target["pageRect"][1],
        width=target["pageRect"][2], height=target["pageRect"][3], asset=target["asset"])
    source_file = DESIGN["states"][state]["source"]
    source = Image.open(ROOT / source_file).convert("RGB")
    screenshot = Image.open(result["path"]).convert("RGB")
    frame, scale = frame_for(target)
    origin = expected_origin(target, result, scale)
    if origin is None:
        return {"id": label, "result": "unresolved", "reason": "local coordinate mapping absent"}
    sx, sy, sw, sh = target["sourceRect"]
    source_crop = source.crop((sx, sy, sx + sw, sy + sh))
    scaled_size = (max(1, round(sw * scale)), max(1, round(sh * scale)))
    source_scaled = source_crop.resize(scaled_size, Image.Resampling.LANCZOS)
    expected_x, expected_y = origin[0] + sx * scale, origin[1] + sy * scale
    ax, ay = actual_box["x"], actual_box["y"]
    aw, ah = actual_box["width"], actual_box["height"]
    if target["asset"] in ("stop.svg", "up.svg"):
        # These are independent module sketches. Register to the icon's own
        # display box; the full page footer is measured separately.
        expected_x = ax + (aw - scaled_size[0]) / 2
        expected_y = ay + (ah - scaled_size[1]) / 2
    source_ink = ink(mask_for(source_scaled, target["mask"]), expected_x, expected_y)
    actual_crop = screenshot.crop((round(ax), round(ay), round(ax + aw), round(ay + ah)))
    actual_ink = ink(mask_for(actual_crop, target["mask"], actual=True), round(ax), round(ay))
    if source_ink is None or actual_ink is None:
        return {"id": label, "result": "unresolved", "reason": "icon ink mask empty",
                "sourceInk": source_ink, "actualInk": actual_ink}
    edge_delta = [round(a - b, 2) for a, b in zip(actual_ink["edges"], source_ink["edges"])]
    center_delta = [round(a - b, 2) for a, b in zip(actual_ink["center"], source_ink["center"])]
    passed = max(abs(d) for d in edge_delta + center_delta) <= 1
    bounds = source_ink["edges"] + actual_ink["edges"]
    left, top = max(0, int(min(bounds[0], bounds[2], bounds[4], bounds[6]) - 10)), max(0, int(min(bounds[1], bounds[3], bounds[5], bounds[7]) - 10))
    right, bottom = min(390, int(max(bounds[0], bounds[2], bounds[4], bounds[6]) + 10)), min(844, int(max(bounds[1], bounds[3], bounds[5], bounds[7]) + 10))
    overlay = Image.new("RGB", (max(1, right-left), max(1, bottom-top)), "white")
    draw = ImageDraw.Draw(overlay)
    source_mask = mask_for(source_scaled, target["mask"])
    actual_mask = mask_for(actual_crop, target["mask"], actual=True)
    for yy, xx in zip(*np.nonzero(source_mask)):
        px, py = round(expected_x + xx) - left, round(expected_y + yy) - top
        if 0 <= px < overlay.width and 0 <= py < overlay.height:
            overlay.putpixel((px, py), (46, 112, 249))
    for yy, xx in zip(*np.nonzero(actual_mask)):
        px, py = round(ax + xx) - left, round(ay + yy) - top
        if 0 <= px < overlay.width and 0 <= py < overlay.height:
            old = overlay.getpixel((px, py))
            overlay.putpixel((px, py), (80, 40, 160) if old != (255, 255, 255) else (239, 75, 69))
    for rect, color in ((source_ink["edges"], "#2563eb"), (actual_ink["edges"], "#ef4444")):
        draw.rectangle((rect[0]-left, rect[1]-top, rect[2]-left, rect[3]-top), outline=color, width=1)
    path = OUT / f"{label}.png"
    overlay.save(path)
    return {"id": label, "source": source_file, "sourceRect": target["sourceRect"],
            "asset": target["asset"], "pageImageBox": actual_box, "scale": round(scale, 8),
            "sourceInk": source_ink, "actualInk": actual_ink,
            "edgeDeltaPx": edge_delta, "centerDeltaPx": center_delta,
            "overlay": str(path), "result": "pass" if passed else "fail"}


items = [compare(t, n + 1) for n, t in enumerate(TARGETS["targets"])]
report = {"method": "Original design icon crop normalized to 390 CSS px; colored ink mask on original and Edge screenshot; boundary and centroid tolerance 1px. Blue=design, red=Edge, purple=overlap.",
          "items": items,
          "summary": {k: sum(i["result"] == k for i in items) for k in ("pass", "fail", "unresolved")}}
(OUT / "measurements.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report["summary"]))
