// OpenRailFanAI · 内容页（使用帮助 / 免责声明 / 联系我们）+ 设置页（模型供应商 / 关于）
// 说明：免责声明正文已填写（依据仓库文档与实现现状）；使用帮助已填写。
// 「关于」不再是独立页面，而是**设置页里的一张卡片**（入口重复过一次：
// 侧栏「ℹ️ 关于」与顶栏「⚙️ 设置」并列，用户要找一个设置得先猜它在哪一边）。

import { store } from "./store.js";

const API_BASE = window.__API_BASE__ || "";

// 唯一的对外反馈入口（GitHub Issues）。写死在这里而不是可配置项：
// 社区版没有工单系统，留一个"待填写"的客服邮箱只会让用户白等回复。
export const ISSUES_URL = "https://github.com/Xiao-John/OpenRailFanAI/issues";
export const REPO_URL = "https://github.com/Xiao-John/OpenRailFanAI";

export const DOCS = {
  disclaimer: { title: "免责声明", icon: "⚠️" },
  help: { title: "使用帮助", icon: "❓" },
  contact: { title: "联系我们", icon: "✉️" },
};

export const APP_VERSION = "v0.6 · Community";

// 未选择具体供应商时，用户的 BYOK Key 记在这个键下
// （语义：只覆盖 Key，供应商地址/模型仍用服务端默认配置）
export const ROOT_KEY_ID = "__default__";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function card(title) {
  const c = el("div", "card");
  if (title) c.appendChild(el("h3", null, title));
  return c;
}

function kv(k, v) {
  const row = el("div", "kv");
  row.appendChild(el("span", "k", k));
  row.appendChild(el("span", "v", v));
  return row;
}

function placeholder(text) {
  return el("div", "placeholder-block", text);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  // 容错：忘了写 /api 前缀时自动补上。这个坑真实踩过 —— 设置页因此静默 404，
  // 界面只显示"服务端不可达"，而网络层才看得到真实 URL 少了 /api。
  const url = path.startsWith("/api/") ? path : "/api" + path;
  const resp = await fetch(API_BASE + url, { ...options, headers });
  let body = null;
  try { body = await resp.json(); } catch { /* 非 JSON */ }
  return { ok: resp.ok, status: resp.status, body };
}

function errText(res, fallback) {
  const d = res && res.body && res.body.detail;
  if (d && typeof d === "object" && !Array.isArray(d)) return d.message || d.code || fallback;
  if (Array.isArray(d) && d.length) return String(d[0].msg || "").replace(/^Value error,\s*/, "") || fallback;
  return fallback;
}

// ============ 页面骨架 ============

function pageHeader(title, onBack) {
  const wrap = el("div");
  const bar = el("div");
  bar.style.cssText = "display:flex;align-items:center;gap:10px;margin-bottom:12px";
  const back = el("button", "icon-btn", "←");
  back.title = "返回对话";
  back.addEventListener("click", onBack);
  bar.appendChild(back);
  bar.appendChild(el("h1", null, title));
  wrap.appendChild(bar);
  return wrap;
}

export function renderDocPage(key, deps) {
  const { onBack } = deps;
  const meta = DOCS[key] || { title: "文档", icon: "📄" };
  const root = el("div");
  root.appendChild(pageHeader(meta.title, onBack));

  if (key === "contact") return renderContact(root, deps);
  if (key === "help") return renderHelp(root, deps);

  // 协议类页面：免责声明正文已填写；其余协议页仍为占位
  if (key === "disclaimer") {
    root.appendChild(el("p", null,
      "本页说明 RailFanAI（OpenRailFanAI）的使用条件与责任边界，继续使用即表示已阅读并同意以下内容。"));
    for (const [h, body] of [
      ["一、数据来源与准确性",
        "回答所依据的事实来自 12306（含车站车次大屏、站点库）、rail.re、黄河铁路网（jprailfan）、cnrail.geogv.org 等第三方公开数据源，" +
        "可能存在延迟、缺失或错误；余票为提问时刻的快照，12306 不再列出当日已发车次；线路里程存在口径差异（两站间最短径路与指定径路／既有线口径不同，回答中会分别标注）。" +
        "请以铁路官方发布为准，本服务的任何数据不作为出行依据。"],
      ["二、不构成任何建议",
        "本服务不构成购票、行程安排、线路选择、拍摄安全等任何专业建议。拍摄点信息仅供参考，系统会自动拒答危险行为；" +
        "外出拍车请遵守法律法规与车站、线路的现场管理规定，不得影响行车安全与公共秩序。"],
      ["三、模型生成内容",
        "标注为「知识型／混合型」的回答可能包含模型记忆内容（页面显示「含模型知识，请核实」徽标），涉及编号、参数、厂商等信息时可能不准确；" +
        "系统中的日期与时刻类数值以检索事实为准，模型知识部分请以官方资料核实。"],
      ["四、第三方链接与数据版权",
        "各数据源与外部链接的内容、版权归其所有者所有；本服务仅做检索与转述，并在回答中标注来源与时效。" +
        "请遵守各站点的使用条款与 robots 约定，不得滥用，包括但不限于高频或并发抓取、绕过限流与反爬、整表转载与转售第三方数据。"],
      ["五、免责与责任限制",
        "本服务按「现状」提供，不对可用性、准确性、连续性作任何明示或默示担保。" +
        "因使用或无法使用本服务（含数据错误、服务中断、依据回答作出的任何决定）造成的直接或间接损失，作者与贡献者不承担赔偿责任。"],
      ["六、使用许可",
        "本项目代码按仓库 LICENSE 声明的许可发布（MIT），允许包括商业使用在内的使用、修改与分发；" +
        "第三方数据不在该授权范围内，其使用须另行遵守各数据源条款；使用本服务时请遵守所在地法律法规。"],
    ]) {
      root.appendChild(el("h2", null, h));
      root.appendChild(el("p", null, body));
    }
  } else {
    root.appendChild(placeholder("本节为条款正文占位，待法务/运营补齐后替换本段。"));
  }

  root.appendChild(el("div", "meta", `版本 ${APP_VERSION} · 内容随版本更新`));
  return root;
}

/**
 * 「关于」卡片 —— 挂在**设置页**底部，不再单独占一个路由。
 *
 * 为什么保留这一张卡而不是直接删掉：应用名/版本/许可是用户排查问题时
 * 第一个会被问到的信息（"你装的是哪版"），随手可查比藏在某个折叠块里有用。
 * 免责声明 / 使用帮助 / 联系我们 三个入口也从这里进 —— 它们原来是从
 * 「关于」页链接过去的，不在这里留入口就等于改完找不到了。
 */
export function aboutCard(navigate) {
  const c = card("关于");

  const intro = el("p", "sub",
    "RailFanAI 是面向中国铁路爱好者的 RAG / Agent 助手：输入一句自然语言，"
    + "系统先判断意图与问题性质，再调用真实数据源检索，最后给出带来源与时效说明的回答。");
  c.appendChild(intro);

  c.appendChild(kv("应用", "RailFanAI（OpenRailFanAI）"));
  c.appendChild(kv("版本", APP_VERSION));
  c.appendChild(kv("许可", "MIT（第三方数据的版权归各数据源所有）"));
  c.appendChild(kv("技术栈", "FastAPI（SSE 流式）+ 原生 HTML/JS（无构建工具）"));

  // 仓库地址与反馈地址是**两个**链接：仓库给"想看源码/自建"的人，
  // issue 给"要报问题"的人，用同一个地址会让人以为只能提 issue。
  c.appendChild(externalLink("在 GitHub 上打开仓库", REPO_URL));
  c.appendChild(el("div", "url-plain", REPO_URL));

  const links = el("div");
  links.style.marginTop = "10px";
  for (const key of ["disclaimer", "help", "contact"]) {
    const meta = DOCS[key];
    const row = el("div", "list-link");
    row.appendChild(el("span", null, `${meta.icon} ${meta.title}`));
    row.appendChild(el("span", "arrow", "›"));
    row.addEventListener("click", () => { if (navigate) navigate("#/doc/" + key); });
    links.appendChild(row);
  }
  c.appendChild(links);
  return c;
}

/**
 * 外链元素。
 *
 * `target="_blank"` + `rel="noopener"` 是给浏览器用的；Android 一体化版的
 * WebView 里 MainActivity.shouldOverrideUrlLoading **会拦截一切非 127.0.0.1 的跳转**
 * （只在启动日志里记一行"已拦截外部跳转"），所以页面上同时明写 URL 文本并配"复制链接"，
 * 保证跳不出去时用户还有路可走 —— 只给一个点了没反应的链接等于没有入口。
 */
function externalLink(text, url) {
  const a = document.createElement("a");
  a.className = "link-out";
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.appendChild(el("span", null, text));
  a.appendChild(el("span", null, "↗"));
  return a;
}

/**
 * 复制文本：与 main.js 里同一套兜底顺序。
 *
 * 不能只用 Clipboard API —— 它在"没有用户手势 / 文档失焦"时会直接 reject
 * （真机上被判为"复制失败"就是这么来的），所以 reject 后必须退到 execCommand。
 */
function copyText(text) {
  const legacy = () => new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    // 不能 display:none（那样选不中），挪出视口即可
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    ok ? resolve() : reject(new Error("copy failed"));
  });
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).catch(legacy);
  }
  return legacy();
}

function renderContact(root) {
  const c = card("联系我们");
  c.appendChild(el("p", "sub",
    "问题反馈、功能建议、数据纠错统一走 GitHub Issues —— 这里能看到处理进度，也方便其他车迷搜到同样的答案。"));
  c.appendChild(externalLink("在 GitHub 上提 issue", ISSUES_URL));

  // WebView 可能拦掉外链跳转（见 externalLink 注释），所以地址要能看见、能复制。
  // 地址与按钮**上下排**而不是左右排：窄屏上并排会把 URL 挤成"i ssues"这种断词。
  const copyWrap = el("div");
  copyWrap.style.marginTop = "10px";
  copyWrap.appendChild(el("div", "url-plain", ISSUES_URL));
  const copyBtn = el("button", "btn ghost", "复制链接");
  copyBtn.style.marginTop = "8px";
  copyBtn.addEventListener("click", () => {
    copyText(ISSUES_URL).then(
      () => { copyBtn.textContent = "已复制"; },
      () => { copyBtn.textContent = "复制失败，请长按地址"; }
    );
  });
  copyWrap.appendChild(copyBtn);
  c.appendChild(copyWrap);
  root.appendChild(c);

  const note = card("提交问题时请附上");
  for (const t of ["问题原句与时间", "页面显示的意图与数据来源", "若是错误数据，请附权威来源截图/链接"]) {
    note.appendChild(el("li", null, t));
  }
  root.appendChild(note);
  root.appendChild(el("div", "meta", "这三项能把排查从「猜」变成「看」：缺了它们往往只能靠运气复现。"));
  return root;
}

function renderHelp(root, deps) {
  const q = card("快速上手");
  for (const [k, v] of [
    ["提问", "像跟人说话一样提问，例如「G1 今天由哪组担当？」「明天北京到上海的高铁余票」。"],
    ["多轮追问", "直接说「那明天呢」「它的余票呢」，系统会承接上一轮的对象。"],
    ["停止生成", "生成中点「■」或按 ESC，已生成内容会保留。"],
    ["编辑重发", "悬停/长按消息 → 「编辑」，发送后该消息之后的内容会被丢弃。"],
    ["多对话", "左上角「☰」打开对话列表，可新建、切换、重命名、删除；对话保存在本机浏览器。"],
  ]) {
    q.appendChild(kv(k, v));
  }
  root.appendChild(q);

  const tip = card("结果怎么读");
  for (const [k, v] of [
    ["意图 / 问题性质", "回答顶部显示识别出的意图；「知识型/混合型」回答含模型知识，页面会提示核实。"],
    ["数据来源", "回答附来源链接；每条事实带各自的时效说明。"],
    ["流程日志 / 思考过程", "折叠块可展开，便于核对检索与推理过程。"],
    ["非今日数据", "若标注【非今日数据】，表示该值不是当天的实时值（例如已发车车次的次日图定时刻）。"],
  ]) {
    tip.appendChild(kv(k, v));
  }
  root.appendChild(tip);

  const limits = card("已知限制");
  for (const t of [
    "12306 不再列出当日已发车次，此时会明确说明「今日时刻不可得」，而不是编造。",
    "余票为提问时刻的快照，低余量会二次校验并标注变动。",
    "线路里程有口径差异：最短径路（可能走高线）与指定径路/既有线口径不同，页面会分别标注。",
    "普速车次的担当机车无公开数据源，系统会如实说明。",
  ]) {
    limits.appendChild(el("li", null, t));
  }
  root.appendChild(limits);
  return root;
}

// ============ 模型供应商设置（BYOK）============
//
// 设计（按用户给的示例图重做）：
//   · **一张列表**列出已添加的供应商，每行「名称 + 自定义标签 + 状态点 + 操作」；
//   · 底部两个按钮是**同一个动作的两种来源**：从预设挑 / 从零填。二者都进入
//     **同一个编辑表单** —— 这正是旧版最大的问题：把"选供应商"和"填地址"
//     做成上下两张卡片两套表单，逼用户先自我归类。
//   · 模型用**下拉**（填完 Key 自动探测），保留手工输入兜底（不少网关没有 /models）。

function formRow(labelText, control) {
  const row = el("div", "form-row");
  row.appendChild(el("label", null, labelText));
  row.appendChild(control);
  return row;
}

function inputEl(type, placeholder, value) {
  const i = el("input");
  i.type = type;
  i.placeholder = placeholder || "";
  i.value = value || "";
  i.autocomplete = "off";
  i.spellcheck = false;
  return i;
}

function selectEl(options, value) {
  const s = el("select");
  for (const [v, label] of options) {
    const o = el("option", null, label);
    o.value = v;
    s.appendChild(o);
  }
  if (value != null) s.value = value;
  return s;
}

function buttonEl(text, cls, onClick) {
  const b = el("button", "btn" + (cls ? " " + cls : ""), text);
  b.addEventListener("click", onClick);
  return b;
}

/** 一个供应商条目行：名称 + 自定义标签 + 状态点 + 使用中标记 + 操作。 */
function providerRow(entry, rerender, onEdit) {
  const row = el("div", "prov-row");
  if (store.llm().activeId === entry.id) row.classList.add("active");

  const left = el("div", "prov-main");
  left.appendChild(el("span", "prov-name", entry.label || entry.id));
  if (entry.custom) left.appendChild(el("span", "prov-tag", "自定义"));
  const dot = el("span", "prov-dot" + (entry.key ? " ok" : ""));
  dot.title = entry.key ? "已配置 API Key" : "尚未填写 API Key";
  left.appendChild(dot);
  if (store.llm().activeId === entry.id) left.appendChild(el("span", "prov-tag using", "使用中"));
  row.appendChild(left);

  const actions = el("div", "prov-actions");
  actions.appendChild(buttonEl("编辑", "", () => onEdit(entry)));
  if (entry.custom || store.llmEntries().length > 1) {
    actions.appendChild(buttonEl("删除", "danger", () => {
      store.removeLlmEntry(entry.id);
      rerender();
    }));
  }
  if (store.llm().activeId !== entry.id && entry.key) {
    actions.appendChild(buttonEl("使用", "primary", () => {
      store.setActiveLlm(entry.id);
      rerender();
    }));
  }
  row.appendChild(actions);
  return row;
}

export function renderSettingsPage(deps) {
  const { onBack, navigate } = deps;
  const root = el("div");
  // 标题从「模型」改成「设置」：这一页现在同时承载模型供应商与关于，
  // 顶栏入口叫「⚙️ 设置」，进来看见「模型」会让人以为走错了地方。
  root.appendChild(pageHeader("设置", onBack));

  let presets = [];          // 服务端内置/已配置的供应商（仅作预设来源）
  let editing = null;        // 正在编辑的条目副本（null = 不在编辑态）

  const body = el("div");
  root.appendChild(body);

  function render() {
    body.innerHTML = "";
    const s = store.llm();
    const entries = store.llmEntries();

    const c = card(null);
    c.appendChild(el("p", "sub", "填入各提供方的 API 密钥即可使用其模型。"));

    if (!entries.length) {
      c.appendChild(el("p", "sub", "还没有添加提供方。点下面的按钮选一个常用服务，或自己填地址。"));
    }
    for (const e of entries) {
      c.appendChild(providerRow(e, () => { editing = null; render(); }, openEditor));
    }

    const addRow = el("div", "prov-add-row");
    addRow.appendChild(buttonEl("＋ 添加提供方", "ghost", () => openPresetPicker()));
    addRow.appendChild(buttonEl("＋ 添加自定义提供方", "ghost", () => {
      editing = { id: "custom-" + Date.now().toString(36), label: "", base_url: "", model: "", api: "", key: "", custom: true };
      render();
    }));
    c.appendChild(addRow);

    // 记住 Key：全局开关（属于设备偏好，不属于某一家）
    const rk = el("input");
    rk.type = "checkbox";
    rk.checked = !!s.rememberKey;
    rk.addEventListener("change", () => store.setLlmRemember(rk.checked));
    const rkRow = el("div", "form-row");
    rkRow.appendChild(rk);
    const rkLabel = el("label", null, "记住 API Key（存本机浏览器；不勾选则刷新后需重填）");
    rkLabel.style.minWidth = "0";
    rkRow.appendChild(rkLabel);
    c.appendChild(rkRow);

    c.appendChild(el("p", "sub",
      "以上只作用于当前浏览器。要让所有人都默认使用某供应商，请在服务端 .env 设置 "
      + "LLM_PROVIDER / LLM_PROVIDERS（见 docs/run.md）。"));
    body.appendChild(c);

    if (editing) body.appendChild(editorCard());
  }

  /** 打开某个已添加条目的编辑表单（用副本，取消时不污染已存配置）。 */
  function openEditor(entry) {
    if (!entry) return;
    editing = { ...entry };
    render();
  }

  // ---- 从预设挑选（与"添加自定义"进入同一个表单）----
  //
  // 预设列表**在这里现取**，而不是依赖进页面时那次 fire-and-forget 请求已完成：
  // 后者会让"点开却是空列表"偶发出现（真机实测踩到），而且失败时无从补救。
  async function openPresetPicker() {
    editing = null;
    body.innerHTML = "";
    const c = card("添加提供方");
    c.appendChild(el("p", "sub", "选一个常用服务，下一步只需填它的 API Key。"));
    const loading = el("p", "sub", "正在读取预设…");
    c.appendChild(loading);
    body.appendChild(c);

    const res = await api("/api/providers");
    presets = (res.ok && res.body && res.body.providers ? res.body.providers : [])
      .filter((p) => p.source !== "legacy");
    loading.remove();
    if (!presets.length) {
      c.appendChild(el("p", "sub",
        "读取预设失败（服务端不可达）。可以改用「＋ 添加自定义提供方」手工填写地址。"));
      c.appendChild(buttonEl("← 返回", "ghost", () => { editing = null; render(); }));
      return;
    }
    const list = el("div", "preset-list");
    for (const p of presets) {
      const row = el("div", "preset-row");
      row.appendChild(el("span", "prov-name", p.label));
      if (p.note) row.appendChild(el("span", "preset-note", p.note));
      const b = buttonEl(store.llmEntry(p.id) ? "已添加" : "添加", "", () => {
        const exist = store.llmEntry(p.id);
        editing = exist
          ? { ...exist }
          : { id: p.id, label: p.label, base_url: p.base_url, model: p.model || "", api: "", key: "", custom: false };
        render();
      });
      if (store.llmEntry(p.id)) b.disabled = true;
      row.appendChild(b);
      list.appendChild(row);
    }
    c.appendChild(list);
    c.appendChild(buttonEl("← 返回", "ghost", () => { editing = null; render(); }));
    body.appendChild(c);
  }

  // ---- 编辑表单（预设与自定义共用同一个）----
  function editorCard() {
    const e = editing;
    const c = card(e.custom ? "自定义提供方" : ("配置 " + (e.label || e.id)));

    const nameInp = inputEl("text", "例如：公司网关", e.label);
    const baseInp = inputEl("text", "https://api.example.com/v1", e.base_url);
    const keyInp = inputEl("password", "sk-…（只保存在本机）", e.key);
    const manualInp = inputEl("text", "手工填写模型名", e.model);
    // 生成预算：BYOK 场景下 .env 往往不可达（尤其 Android），所以这两项要能在界面上调
    const maxTokInp = inputEl("text", "留空用服务端默认", e.max_tokens || "");
    maxTokInp.inputMode = "numeric";
    const ctxOptions = [
      ["", "留空用服务端默认"],
      ["8192", "8k（小窗口 / 老模型）"],
      ["32768", "32k"],
      ["65536", "64k"],
      ["131072", "128k"],
      ["200000", "200k"],
      ["__custom__", "手工输入…"],
    ];
    const ctxSel = selectEl(ctxOptions, e.context_tokens ? String(e.context_tokens) : "");
    const ctxCustom = inputEl("text", "窗口大小（token）", "");
    ctxCustom.inputMode = "numeric";
    ctxCustom.style.display = "none";
    ctxSel.addEventListener("change", () => {
      ctxCustom.style.display = ctxSel.value === "__custom__" ? "" : "none";
      if (ctxSel.value === "__custom__") ctxCustom.focus();
    });
    const modelSel = selectEl([["", "（先填 API Key，再自动探测）"]], e.model);
    const status = el("div", "sub", "");

    if (e.custom) c.appendChild(formRow("名称", nameInp));
    c.appendChild(formRow("接口地址", baseInp));
    c.appendChild(formRow("API Key", keyInp));

    // 模型：下拉优先（探测结果），下拉里带"手工输入…"这一项兜底
    const modelWrap = el("div", "form-row");
    modelWrap.appendChild(el("label", null, "模型"));
    const modelCol = el("div");
    modelCol.style.flex = "1";
    modelCol.appendChild(modelSel);
    manualInp.style.display = "none";
    modelCol.appendChild(manualInp);
    modelWrap.appendChild(modelCol);
    c.appendChild(modelWrap);
    c.appendChild(formRow("最大输出", maxTokInp));
    const ctxWrap = el("div", "form-row");
    ctxWrap.appendChild(el("label", null, "上下文窗口"));
    const ctxCol = el("div");
    ctxCol.style.flex = "1";
    ctxCol.appendChild(ctxSel);
    ctxCol.appendChild(ctxCustom);
    ctxWrap.appendChild(ctxCol);
    c.appendChild(ctxWrap);
    c.appendChild(el("p", "sub",
      "「最大输出」是单次生成的 token 上限（含思考 token，调小会让长回答在半句处被截断）；"
      + "「上下文窗口」填所用模型的窗口大小（8k/32k/128k），用于把输出预算收进窗口内。"
      + "两者留空即用服务端默认。"));

    modelSel.addEventListener("change", () => {
      if (modelSel.value === "__manual__") {
        manualInp.style.display = "";
        manualInp.focus();
      } else {
        manualInp.style.display = "none";
      }
    });

    function currentModel() {
      return modelSel.value === "__manual__" ? manualInp.value.trim() : modelSel.value;
    }

    function fillModels(models, note) {
      const keep = currentModel() || e.model;
      modelSel.innerHTML = "";
      const opts = [["", note || "（未选择）"]].concat(models.map((m) => [m, m]));
      opts.push(["__manual__", "手工输入…"]);
      for (const [v, label] of opts) {
        const o = el("option", null, label);
        o.value = v;
        modelSel.appendChild(o);
      }
      // 尽量保持已选值
      if (keep && models.includes(keep)) modelSel.value = keep;
      else if (keep) { modelSel.value = "__manual__"; manualInp.value = keep; manualInp.style.display = ""; }
      else modelSel.value = "";
    }
    fillModels([], "（先填 API Key，再自动探测）");

    async function detect(showAll) {
      const base = baseInp.value.trim();
      const key = keyInp.value.trim();
      if (!base || !key) { status.textContent = "需要先填接口地址与 API Key 才能探测模型。"; return; }
      status.textContent = "正在探测可用模型…";
      const res = await api("/api/providers/models", {
        method: "POST",
        body: JSON.stringify({ base_url: base, api_key: key, provider: e.custom ? undefined : e.id }),
      });
      const b = res.body || {};
      if (b.ok) {
        const list = showAll ? (b.models || []) : (b.chat_models || b.models || []);
        fillModels(list);
        status.textContent = `探测到 ${list.length} 个${showAll ? "" : "对话"}模型`
          + (b.truncated ? "（已截断）" : "") + "。没找到想要的？点「显示全部」或选「手工输入…」。";
      } else {
        fillModels([], "（探测失败，请手工输入）");
        manualInp.style.display = "";
        status.textContent = "探测失败：" + (b.error || errText(res, "该供应商可能未提供模型列表"))
          + " —— 可直接手工填写模型名。";
      }
    }

    keyInp.addEventListener("blur", () => { if (keyInp.value.trim() && !currentModel()) void detect(false); });
    baseInp.addEventListener("blur", () => { if (keyInp.value.trim() && !currentModel()) void detect(false); });

    const btnRow = el("div", "form-row");
    btnRow.appendChild(buttonEl("探测模型", "", () => detect(false)));
    btnRow.appendChild(buttonEl("显示全部", "", () => detect(true)));
    btnRow.appendChild(buttonEl("保存", "primary", () => {
      const id = e.id;
      const isCustom = !!e.custom;
      const asInt = (v) => {
        const n = parseInt(String(v || "").trim(), 10);
        return Number.isFinite(n) && n > 0 ? n : 0;
      };
      store.upsertLlmEntry({
        id,
        label: (isCustom ? nameInp.value.trim() : e.label) || id,
        base_url: baseInp.value.trim(),
        model: currentModel(),
        api: e.api || "",
        key: keyInp.value.trim(),
        custom: isCustom,
        max_tokens: asInt(maxTokInp.value),
        context_tokens: ctxSel.value === "__custom__"
          ? asInt(ctxCustom.value)
          : asInt(ctxSel.value),
      });
      editing = null;
      render();
    }));
    btnRow.appendChild(buttonEl("取消", "ghost", () => { editing = null; render(); }));
    c.appendChild(btnRow);
    c.appendChild(status);
    return c;
  }

  render();
  // 「关于」并入本页（侧栏那个重复的「ℹ️ 关于」入口已移除）。
  // 挂在 body **之外**：body 每次增删/编辑供应商都会整体重建，静态卡片没必要跟着重建。
  root.appendChild(aboutCard(navigate));
  return root;
}
