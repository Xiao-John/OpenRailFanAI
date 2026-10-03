#!/usr/bin/env node
// Show original design pixels and real Edge page rectangles without altering either image.
import { promises as fs } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../../..");
const specs = JSON.parse(await fs.readFile(path.join(import.meta.dirname, "design-targets.json"), "utf8"));
const verification = JSON.parse(await fs.readFile(path.join(import.meta.dirname, "screenshots/verification.json"), "utf8"));
const output = path.join(import.meta.dirname, "page-comparisons");
await fs.mkdir(output, { recursive: true });
const imageSize = { "desi1.png": [1476, 1065], "desi2.png": [1536, 1024] };
const esc = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");

for (const state of verification.results.filter((entry) => entry.viewport && specs.states[entry.state])) {
  const spec = specs.states[state.state];
  const [fx, fy, fw, fh] = spec.frame;
  const [iw, ih] = imageSize[spec.source];
  const scale = 390 / fw;
  const design = await fs.readFile(path.join(root, spec.source));
  const page = await fs.readFile(state.path);
  const rects = state.checks.filter((check) => check.element && check.targetRaw && check.measuredViewport);
  const designBoxes = rects.map((check, index) => {
    const [x, y, width, height] = check.targetRaw;
    const color = check.result === "pass" ? "#16a34a" : "#e11d48";
    return `<rect x="${x}" y="${y}" width="${width}" height="${height}" fill="none" stroke="${color}" stroke-width="1.3"/><text x="${x + 2}" y="${y + 12}" fill="${color}" font-size="11" font-family="sans-serif">${index + 1}</text>`;
  }).join("");
  const pageBoxes = rects.map((check, index) => {
    const { x, y, width, height } = check.measuredViewport;
    const color = check.result === "pass" ? "#16a34a" : "#e11d48";
    return `<rect x="${430 + x}" y="${28 + y}" width="${width}" height="${height}" fill="none" stroke="${color}" stroke-width="1.3"/><text x="${432 + x}" y="${40 + y}" fill="${color}" font-size="11" font-family="sans-serif">${index + 1}</text>`;
  }).join("");
  const labels = rects.map((check, index) => `${index + 1} ${check.element}: ${check.result}`).join(" · ");
  const height = Math.max(890, 28 + fh * scale + 35);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="820" height="${height}" viewBox="0 0 820 ${height}">
    <rect width="820" height="${height}" fill="#f3f6fa"/>
    <text x="8" y="19" font-size="14" font-family="sans-serif">${esc(state.state)} · design ${esc(spec.source)} (${fw}px → 390px)</text>
    <text x="438" y="19" font-size="14" font-family="sans-serif">Microsoft Edge · 390×844 CSS px</text>
    <svg x="0" y="28" width="390" height="${fh * scale}" viewBox="${fx} ${fy} ${fw} ${fh}" overflow="hidden">
      <image x="0" y="0" width="${iw}" height="${ih}" href="data:image/png;base64,${design.toString("base64")}"/>
      ${designBoxes}
    </svg>
    <image x="430" y="28" width="390" height="844" href="data:image/png;base64,${page.toString("base64")}"/>
    ${pageBoxes}
    <text x="8" y="${height - 10}" font-size="10" font-family="sans-serif">${esc(labels)}</text>
  </svg>`;
  await fs.writeFile(path.join(output, `${state.state}.svg`), svg);
}
console.log(JSON.stringify({ comparisons: verification.results.filter((entry) => entry.viewport).length, output }));
