import assert from "node:assert/strict";
import { themeCommand, fileCommand, fileHistory, inputTypes, pickTextFile, downloadArtifact } from "../src/agent.js";

for (const [input, expected] of [
  ["切换到深色模式", "dark"], ["请把主题改成浅色", "light"],
  ["主题跟随系统", "auto"], ["撤销刚才的主题切换", "undo"],
  ["不要切换深色模式", null], ["如何开启深色模式？", null],
  ["“开启深色模式”是什么意思", null], ["G1明天时刻表", null],
]) assert.equal(themeCommand(input), expected, input);
assert.equal(fileCommand("查询G1和G3明天的票价"), false);
assert.equal(fileCommand("把这段行程生成日历"), true);
assert.equal(fileCommand("请将已导入文件整理成 CSV 表格"), true);
assert.throws(() => downloadArtifact({ name: "../bad.csv" }));
const history = fileHistory([
  {role: "assistant", content: "", meta: {displayResults: [{train: "G1"}], sources: ["12306"]}},
  {role: "assistant", content: "半截回复", meta: {stopped: true}},
]);
assert.equal(history.length, 1);
assert.ok(history[0].content.includes('"G1"') && history[0].content.includes('12306'));
assert.deepEqual(inputTypes(), ["text"]);
assert.deepEqual(inputTypes({input_types: ["text", "image"]}), ["text", "image"]);
assert.deepEqual(inputTypes({input_types: "image"}), ["text"]);

let picker;
globalThis.document = {createElement: () => (picker = {click() {}})};
let selection = pickTextFile(true);
assert.equal(picker.accept, ".png,.jpg,.jpeg,.webp,.gif");
picker.files = [{size: 8*1024*1024+1}]; picker.onchange();
await assert.rejects(selection, /8 MiB/);
selection = pickTextFile(false);
assert.ok(picker.accept.includes(".docx") && picker.accept.includes(".tex") && picker.accept.includes(".rtf"));
picker.oncancel();
assert.equal(await selection, null);

selection = pickTextFile(true);
globalThis.FileReader = class {
  readAsDataURL() { this.result = "data:image/png;base64,aW1hZ2U="; this.onload(); }
};
picker.files = [{name: "test.png", size: 5, type: "image/png"}]; picker.onchange();
assert.deepEqual(await selection, {name: "test.png", content_base64: "aW1hZ2U=", mime: "image/png"});

let downloaded;
globalThis.document = { createElement: () => ({ click() { downloaded = this.download; } }) };
const timer = globalThis.setTimeout;
globalThis.setTimeout = fn => fn();
try {
  downloadArtifact({name: "railfan-draft.csv", content_base64: btoa("train\nG1"), mime: "text/csv"});
  assert.equal(downloaded, "railfan-draft.csv");
} finally { globalThis.setTimeout = timer; }
console.log("Agent Web checks: 23 passed (commands, input types, picker, history and download adapter)");
