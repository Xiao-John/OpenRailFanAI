// RailFanAI · Markdown 渲染测试（node 直跑，不需要浏览器）
//
// 重点钉住两件事：
//   1) 表格能渲染 —— 用户实测反馈"模型输出的表格无法被渲染"，而车次/余票这类回答
//      基本都以表格形式给出，渲染不出来整条回答就没法读；
//   2) 不把不该动的搞坏 —— 代码块里的表格示例、单独一行 `---`、正文里的 `|`
//      都不该被当成表格，而 HTML 转义必须一直有效。
//
// 用法：node frontend/tests/markdown.test.mjs

import { renderMarkdown, renderTables } from "../src/markdown.js";

let pass = 0, fail = 0;
const ok = (cond, msg) => { cond ? (pass++, console.log("[PASS] " + msg)) : (fail++, console.log("[FAIL] " + msg)); };

// ---------- 1. 基本表格 ----------
const tb = "北京南到上海虹桥的车次：\n"
  + "| 车次 | 发→到 | 二等 |\n"
  + "|---|---|---|\n"
  + "| G547 | 06:18—12:11 | 有 |\n"
  + "| G1 | 06:30—11:24 | 有 |\n"
  + "以上为部分结果。";
const h1 = renderMarkdown(tb);
ok(h1.includes("<table>") && h1.includes("</table>"), "表格被渲染成 <table>");
ok(h1.includes('<div class="table-wrap">'), "外层带横向滚动容器（窄屏车次表必须能滚）");
ok(h1.includes("<thead>") && h1.includes("<tbody>"), "表头与表体分开");
ok(h1.includes("<th>车次</th>") && h1.includes("<th>发→到</th>"), "表头单元格正确");
ok((h1.match(/<tr>/g) || []).length === 3, "总计 3 行（1 表头 + 2 数据行）");
ok(h1.includes("<td>G547</td>") && h1.includes("<td>06:18—12:11</td>"), "数据单元格正确");
ok(h1.includes("北京南到上海虹桥的车次"), "表格前面的正文保留");
ok(h1.includes("以上为部分结果"), "表格后面的正文保留");
ok(!h1.includes("|---|"), "分隔行不再作为文本泄漏到页面上");

// ---------- 2. 对齐 ----------
const h2 = renderMarkdown("| a | b | c |\n|:--|--:|:-:|\n| 1 | 2 | 3 |");
ok(h2.includes('<th style="text-align:left">a</th>'), "左对齐");
ok(h2.includes('<th style="text-align:right">b</th>'), "右对齐");
ok(h2.includes('<th style="text-align:center">c</th>'), "居中");
ok(h2.includes('<td style="text-align:right">2</td>'), "数据行沿用同一对齐");

// ---------- 3. 单元格里的行内语法 ----------
const h3 = renderMarkdown("| 名称 | 说明 |\n|---|---|\n| **粗体** | `代码` |");
ok(h3.includes("<strong>粗体</strong>"), "单元格内的 **粗体** 生效");
ok(h3.includes("<code>代码</code>"), "单元格内的 `代码` 生效");

// ---------- 4. 不该被当成表格的情况 ----------
const h4 = renderMarkdown("```\n| a | b |\n|---|---|\n| 1 | 2 |\n```");
ok(!h4.includes("<table>"), "代码块里的表格示例**不**被渲染成表格");
ok(h4.includes('<pre class="code">'), "代码块本身照常渲染");

const h5 = renderMarkdown("北京 | 上海\n---\n下一段");
ok(!h5.includes("<table>"), "单独一行 --- 是分割线，不是表格分隔行");

const h6 = renderMarkdown("| a | b |\n| 1 | 2 |");
ok(!h6.includes("<table>"), "没有分隔行就不是表格");

const h7 = renderMarkdown("今天 06:18 | 12:11 到达");
ok(!h7.includes("<table>"), "正文里出现一个竖线不会被误判");

// ---------- 5. 转义必须一直有效 ----------
const h8 = renderMarkdown("<img src=x onerror=alert(1)>");
ok(!h8.includes("<img"), "正文里的 HTML 被转义");
const h9 = renderMarkdown("| <b>x</b> | y |\n|---|---|\n| <script>bad</script> | 2 |");
ok(!h9.includes("<b>") && !h9.includes("<script>"), "表格单元格里的 HTML 同样被转义");

// ---------- 6. 既有语法不能被搞坏 ----------
ok(renderMarkdown("### 标题").includes("<h3>标题</h3>"), "标题");
ok(renderMarkdown("## 标题").includes("<h3>标题</h3>"), "二级标题");
ok(renderMarkdown("- 项目一\n- 项目二").includes("•"), "无序列表");
ok(renderMarkdown("**粗** 和 *斜*").includes("<strong>粗</strong>"), "粗体");
ok(renderMarkdown("**粗** 和 *斜*").includes("<em>斜</em>"), "斜体");
ok(renderMarkdown("行尾换行\n下一行").includes("<br>"), "换行仍是 <br>");

// ---------- 7. 单独测 renderTables 的边界 ----------
ok(renderTables("| a |\n|---|").includes("<table>"), "只有表头没有数据行也是合法表格");
ok(!renderTables("普通文本").includes("<table>"), "普通文本原样返回");
ok(renderTables("| a | b |\n|---|---|\n| 1 |").includes("<td>1</td>"),
   "数据行缺列时按现有列渲染，不越界");

console.log("结果：" + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
