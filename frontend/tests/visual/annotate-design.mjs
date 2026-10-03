#!/usr/bin/env node
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const targets = JSON.parse(await fs.readFile(path.join(root,"frontend/tests/visual/design-targets.json"),"utf8"));
const output = path.join(root,"frontend/tests/visual/design-overlays");
await fs.mkdir(output,{recursive:true});
for (const [state, spec] of Object.entries(targets.states)) {
  const source = path.join(root,spec.source);
  const image = await fs.readFile(source);
  const [width,height] = spec.source === "desi1.png" ? [1476,1065] : [1536,1024];
  const svg = [`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    `<image href="data:image/png;base64,${image.toString("base64")}" width="${width}" height="${height}"/>`,
    `<style>text{font:16px sans-serif;fill:#ff1744;paint-order:stroke;stroke:#fff;stroke-width:3px}.box{fill:none;stroke:#ff1744;stroke-width:2}</style>`];
  for (const [name,[x,y,w,h]] of Object.entries(spec.elements)) {
    svg.push(`<rect class="box" x="${x}" y="${y}" width="${w}" height="${h}"/>`);
    svg.push(`<text x="${x+2}" y="${Math.max(16,y-3)}">${name} [${x},${y},${w},${h}]</text>`);
  }
  svg.push("</svg>");
  await fs.writeFile(path.join(output,`${state}.svg`),svg.join("\n"));
}
console.log(JSON.stringify({states:Object.keys(targets.states).length,output}));
