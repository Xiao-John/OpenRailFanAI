#!/usr/bin/env python3
"""Derive code-native paths from explicitly classified application glyphs.

Original design PNGs are read only. No screenshot or comparison image is made.
The output is an editable Compose vector path and its reproducible provenance.
"""
from pathlib import Path
import hashlib
import json
import sys
from collections import Counter
from PIL import Image

from native_icon_metrics import _foreground

ROOT = Path(__file__).resolve().parents[2]
TARGETS = {
    "trainSearch": {"source": "desi2.png", "roi": [1226, 160, 108, 80], "color": "muted"},
    "querySpinner": {"source": "desi2.png", "roi": [89, 182, 58, 58], "color": "blue"},
    "brandTrain": {"source": "desi1.png", "roi": [202, 174, 38, 40], "color": "blue", "enclosed_fill": True},
    "assistantTrain": {"source": "desi1.png", "roi": [71, 308, 30, 32], "color": "blue", "enclosed_fill": True},
    "historyClose": {"source": "desi1.png", "roi": [1382, 183, 27, 28], "color": "dark"},
    "historySearch": {"source": "desi1.png", "roi": [1022, 238, 25, 26], "color": "muted"},
    "historyEdit": {"source": "desi1.png", "roi": [1034, 790, 25, 25], "color": "dark", "coverage_underlay": True, "pixel_hairlines": True},
    "historyTrash": {"source": "desi1.png", "roi": [1033, 844, 27, 27], "color": "red"},
}


def boundary_paths(mask):
    edges = set()
    for x, y in mask:
        if (x, y-1) not in mask: edges.add((x, y, x+1, y))
        if (x+1, y) not in mask: edges.add((x+1, y, x+1, y+1))
        if (x, y+1) not in mask: edges.add((x+1, y+1, x, y+1))
        if (x-1, y) not in mask: edges.add((x, y+1, x, y))
    outgoing = {}
    for edge in edges: outgoing.setdefault(edge[:2], set()).add(edge)
    direction = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    paths = []
    while edges:
        edge = min(edges)
        start = edge[:2]
        points = [start]
        while True:
            edges.remove(edge)
            outgoing[edge[:2]].remove(edge)
            end = edge[2:]
            points.append(end)
            if end == start: break
            candidates = outgoing.get(end, set())
            if not candidates: raise ValueError("Open glyph contour")
            old = direction[(edge[2]-edge[0], edge[3]-edge[1])]
            # Keep foreground on the right at diagonal pixel junctions.
            turns = {1: 0, 0: 1, 3: 2, 2: 3}
            edge = min(candidates, key=lambda e: turns[(direction[(e[2]-e[0], e[3]-e[1])]-old) % 4])
        points = points[:-1]
        simple = []
        for i, p in enumerate(points):
            before, after = points[i-1], points[(i+1) % len(points)]
            if (p[0]-before[0]) * (after[1]-p[1]) != (p[1]-before[1]) * (after[0]-p[0]):
                simple.append(p)
        paths.append(simple)
    return paths


def source_layers(image, spec):
    x, y, w, h = spec["roi"]
    previous = set()
    layers = []
    for profile in ("strict", "base", "loose"):
        mask = {(ix, iy) for iy in range(h) for ix in range(w)
                if _foreground(image.getpixel((x+ix, y+iy)), spec["color"], profile)}
        if not mask or any(ix in (0, w-1) or iy in (0, h-1) for ix, iy in mask):
            raise ValueError("Application glyph needs a complete contour inside its background margin")
        if not previous <= mask:
            raise ValueError("Source foreground bands must be nested")
        band = mask - previous
        if band:
            colors = Counter(image.getpixel((x+ix, y+iy)) for ix, iy in band)
            color = min(colors, key=lambda rgb: (-colors[rgb], rgb))
            layers.append((profile, mask, color))
        previous = mask
    return list(reversed(layers))


def source_paint_cells(image, spec):
    """Retain enclosed window/headlight colours without painting the ROI background."""
    x, y, w, h = spec["roi"]
    ink = {(ix, iy) for iy in range(h) for ix in range(w)
           if _foreground(image.getpixel((x+ix, y+iy)), spec["color"], "loose")}
    if not spec.get("enclosed_fill"): return ink
    exterior = set()
    pending = [(ix, iy) for iy in range(h) for ix in range(w)
               if ix in (0, w-1) or iy in (0, h-1)]
    while pending:
        cell = pending.pop()
        if cell in exterior or cell in ink: continue
        ix, iy = cell
        if not (0 <= ix < w and 0 <= iy < h): continue
        exterior.add(cell)
        pending.extend(((ix-1, iy), (ix+1, iy), (ix, iy-1), (ix, iy+1)))
    return {(ix, iy) for iy in range(h) for ix in range(w)} - exterior


def source_palette_layers(image, spec, bucket_size=4):
    """Keep source colour variation; representative RGB is an actual source colour.

    Adjacent source cells of one shade become closed vector contours. Grouping
    limits each channel's change to three levels, without merging different ink
    memberships. The source's antialias shades are retained in the palette.
    """
    x, y, w, h = spec["roi"]
    groups = {}
    painted_cells = source_paint_cells(image, spec)
    for iy in range(h):
        for ix in range(w):
            rgb = image.getpixel((x+ix, y+iy))
            membership = tuple(_foreground(rgb, spec["color"], p) for p in ("strict", "base", "loose"))
            if (ix, iy) not in painted_cells: continue
            key = (tuple(c // bucket_size for c in rgb), membership)
            cells, colors = groups.setdefault(key, (set(), Counter()))
            cells.add((ix, iy))
            colors[rgb] += 1
    layers = []
    for key, (cells, colors) in sorted(groups.items()):
        color = min(colors, key=lambda rgb: (-colors[rgb], rgb))
        layers.append((key[1], cells, color))
    return layers


def source_hairlines(image, spec):
    """Find complete, isolated, rectangular one-source-pixel ink strips."""
    if not spec.get("pixel_hairlines"): return []
    x, y, _, _ = spec["roi"]
    mask = source_paint_cells(image, spec)
    strips = []
    for contour in boundary_paths(mask):
        if len(contour) != 4: continue
        left, right = min(p[0] for p in contour), max(p[0] for p in contour)
        top, bottom = min(p[1] for p in contour), max(p[1] for p in contour)
        width, height = right-left, bottom-top
        if min(width, height) != 1 or max(width, height) < 6: continue
        cells = {(ix, iy) for iy in range(top, bottom) for ix in range(left, right)}
        if not cells <= mask: continue  # Never fill a negative aperture.
        colors = Counter(image.getpixel((x+ix, y+iy)) for ix, iy in cells)
        rgb = min(colors, key=lambda c: (-colors[c], c))
        strips.append({"rect": [left, top, width, height], "source_rgb": rgb})
    return strips


def main():
    output = ["// Generated by scripts/android/generate-design-paths.py; edit the source specification to regenerate.",
              "package org.openrailfanai.app", "", "import androidx.compose.ui.graphics.Path", "import androidx.compose.ui.graphics.Color", "",
              "internal data class DesignInkLayer(val path: Path, val color: Color, val tintable: Boolean = true)",
              "internal data class DesignHairline(val x: Float, val y: Float, val width: Float, val height: Float, val color: Color)",
              "internal data class DesignGlyph(val width: Float, val height: Float, val layers: List<DesignInkLayer>, val sourceShadedCells: Boolean = false, val tintPath: Path? = null, val coverageUnderlay: Color? = null, val hairlines: List<DesignHairline> = emptyList())", "",
              "/** Application glyphs only; original references remain unchanged. */",
              "internal object NativeDesignPaths {"]
    manifest = {"schema_version": 3, "method": "source RGB palette in four-level channel buckets, separate ink memberships; closed colour-region vector paths", "maximum_rgb_channel_error": 3, "glyphs": {}}
    for name, spec in TARGETS.items():
        path = ROOT / spec["source"]
        image = Image.open(path).convert("RGB")
        x, y, w, h = spec["roi"]
        source_layers(image, spec)  # Validate complete source glyph and its holes.
        layers = source_palette_layers(image, spec)
        output.append(f"    // {spec['source']} ROI {spec['roi']}; viewport {w}×{h}.")
        output.append(f"    val {name}: DesignGlyph by lazy {{ DesignGlyph({w}f, {h}f, listOf(")
        evidence = []
        for membership, mask, color in layers:
            paths = boundary_paths(mask)
            encoded = "|".join(";".join(f"{px},{py}" for px, py in points) for points in paths)
            argb = "FF" + "".join(f"{c:02X}" for c in color)
            tintable = "true" if membership[-1] else "false"
            output.append(f'        DesignInkLayer(contours("{encoded}"), Color(0x{argb}), tintable = {tintable}),')
            evidence.append({"ink_membership": membership, "source_rgb": color, "foreground_pixels": len(mask), "closed_contours": len(paths)})
        ink = {(ix, iy) for iy in range(h) for ix in range(w)
               if _foreground(image.getpixel((x+ix, y+iy)), spec["color"], "loose")}
        tint_contours = "|".join(";".join(f"{px},{py}" for px, py in points) for points in boundary_paths(ink))
        underlay_arg = ""
        underlay_rgb = None
        if spec.get("coverage_underlay"):
            strict_colors = Counter(image.getpixel((x+ix, y+iy)) for ix, iy in ink
                                    if _foreground(image.getpixel((x+ix, y+iy)), spec["color"], "strict"))
            underlay_rgb = min(strict_colors, key=lambda rgb: (-strict_colors[rgb], rgb))
            underlay_arg = ", coverageUnderlay = Color(0xFF" + "".join(f"{c:02X}" for c in underlay_rgb) + ")"
        hairlines = source_hairlines(image, spec)
        hairline_arg = ""
        if hairlines:
            values = ["DesignHairline(" + ", ".join(f"{v}f" for v in strip["rect"]) + ", Color(0xFF" +
                      "".join(f"{c:02X}" for c in strip["source_rgb"]) + "))" for strip in hairlines]
            hairline_arg = ", hairlines = listOf(" + ", ".join(values) + ")"
        output.append(f'    ), sourceShadedCells = true, tintPath = contours("{tint_contours}"){underlay_arg}{hairline_arg}) }}')
        manifest["glyphs"][name] = {**spec, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                   "layers": evidence, "coverage_underlay_source_rgb": underlay_rgb, "pixel_hairlines": hairlines,
                                   "classification": "application_ui", "acceptance_status": "unverified"}
    output += ["    private fun contours(data: String): Path = Path().apply {",
               "        data.split('|').forEach { contour ->",
               "            contour.split(';').forEachIndexed { index, point ->",
               "                val xy = point.split(',')",
               "                val x = xy[0].toFloat(); val y = xy[1].toFloat()",
               "                if (index == 0) moveTo(x, y) else lineTo(x, y)",
               "            }",
               "            close()",
               "        }", "    }", "}", ""]
    target = ROOT / "android/app/src/main/java/org/openrailfanai/app/NativeDesignPaths.kt"
    target.write_text("\n".join(output))
    (ROOT / "frontend/assets/icons/native-design-paths.provenance.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(target.relative_to(ROOT)), "glyphs": len(TARGETS)}, ensure_ascii=False))


if __name__ == "__main__": main()
