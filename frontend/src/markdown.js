// RailFanAI · 回答正文的轻量 Markdown 渲染
//
// 为什么自己写而不是引 marked / markdown-it：前端没有构建步骤，也不想为几十行规则
// 背一个依赖；这里只需要覆盖模型实际会用的那几种语法。
//
// 为什么单独成一个模块：渲染规则的边界情况多（表格尤其），需要能**脱离浏览器用 node
// 直接跑测试**，见 frontend/tests/markdown.test.mjs。
//
// 安全性：**先整体 HTML 转义**，之后的替换只插入我们自己生成的标签，
// 所以模型输出里的任何标签都只会显示成文字，不会被当成 HTML 执行。

export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------- GFM 表格
//
// 为什么必须支持：模型经常用表格回答"某天某区间有哪些车次"这类问题，原始输出是
// 一串 `| 车次 | 发→到 | 商务 | 一等 | 二等 | 无座 |`。渲染不出来时用户只能看到
// 一坨竖线，整条回答基本没法读。

/** 分隔行：`|---|---|`、`| :--- | ---: |`，也允许不写外侧竖线。 */
function isDelimiter(line) {
  const t = String(line == null ? "" : line).trim();
  // 必须同时含 `-` 和 `|`：只有 `---` 那是分割线，不是表格分隔行
  return t.includes("-") && t.includes("|") && /^[\s:|-]+$/.test(t);
}

function splitRow(line) {
  return String(line).trim()
    .replace(/^\|/, "").replace(/\|$/, "")
    .split("|").map((c) => c.trim());
}

function alignsOf(delim) {
  return splitRow(delim).map((d) => {
    const left = d.startsWith(":");
    const right = d.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return "";                       // 未指定就交给 CSS
  });
}

/**
 * 把片段里连续的表格行转成 `<table>`。**输入必须是已转义、行内语法已处理过**的文本。
 *
 * 外面套一层 `.table-wrap` 是为了窄屏能横向滚动：车次表天然很宽，直接铺开会把
 * 整个对话区撑变形。
 */
export function renderTables(seg) {
  const lines = String(seg).split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const head = lines[i];
    const delim = lines[i + 1];
    if (!(head.includes("|") && isDelimiter(delim))) {
      out.push(head);
      continue;
    }
    const aligns = alignsOf(delim);
    const cell = (tag, text, k) =>
      "<" + tag + (aligns[k] ? ' style="text-align:' + aligns[k] + '"' : "") + ">"
      + text + "</" + tag + ">";
    const headRow = "<tr>" + splitRow(head).map((c, k) => cell("th", c, k)).join("") + "</tr>";

    const body = [];
    let j = i + 2;
    for (; j < lines.length; j++) {
      // 表格在遇到第一个不以 `|` 开头的行时结束；空行也算结束
      if (!lines[j].trim().startsWith("|")) break;
      body.push("<tr>" + splitRow(lines[j]).map((c, k) => cell("td", c, k)).join("") + "</tr>");
    }
    out.push('<div class="table-wrap"><table><thead>' + headRow + "</thead>"
      + (body.length ? "<tbody>" + body.join("") + "</tbody>" : "")
      + "</table></div>");
    i = j - 1;
  }
  return out.join("\n");
}

// ---------------------------------------------------------------- 渲染入口

export function renderMarkdown(text) {
  let h = esc(text);
  h = h.replace(/```([\s\S]*?)```/g, (_m, code) => '<pre class="code">' + code + "</pre>");
  // 代码块先"封存"起来：下面的行内替换与表格解析都不能碰它的内容
  // （代码块里出现 `| a | b |` 或 `*斜体*` 是常事，被改坏会很显眼）。
  h = h.replace(/(<pre[\s\S]*?<\/pre>)/g, "\u0000$1\u0000");
  h = h.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  h = h.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>");
  h = h.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
  h = h.replace(/^#\s+(.+)$/gm, "<h3>$1</h3>");
  h = h.replace(/^\s*[-*]\s+(.+)$/gm, "•  $1");
  // 表格必须在这里做，两个位置都有讲究：
  //  · 要在 `\n`→`<br>` **之前** —— 否则每行末尾都被插上 <br>，没法再按行解析；
  //  · 要在代码块围栏**之外** —— 按 \u0000 切分后，奇数下标就是被封存的 <pre>，跳过。
  h = h.split("\u0000").map((seg, i) => (i % 2 ? seg : renderTables(seg))).join("\u0000");
  h = h.replace(/\n/g, "<br>");
  h = h.replace(/\u0000/g, "");
  return h;
}
