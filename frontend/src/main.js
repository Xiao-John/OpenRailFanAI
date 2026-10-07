// RailFanAI —— 前端入口（多对话 + 设置 + 移动优先自适应）
// 版本号不写在这里：唯一来源是仓库根的 VERSION，由 pages.loadAppVersion() 运行时取。
//
// 结构：
//   store.js   本机数据层（对话与偏好，localStorage）
//   pages.js   内容页（使用帮助、免责声明、关于）
//   main.js    应用外壳：路由（hash）、侧栏会话列表、对话视图（SSE 流式）
//
// 能力（延续 M8/M10，并新增多对话）：
//   1) 多对话：会话列表可新建/切换/重命名/删除/搜索，数据保存在本机浏览器，不再"新对话覆盖旧的"
//   2) 多轮上下文：以当前对话的消息为准，每次把此前消息作为 history 回传
//   3) 暂停输出：生成中「发送」变「■ 停止」，AbortController 中断并保留已生成内容
//   4) 编辑重发 / 重新生成：丢弃目标消息之后的内容后重跑
//   5) 三端自适应：移动优先（抽屉侧栏），≥1024px 侧栏常驻
//   6) 无需登录：对话匿名可用，不存任何凭据
import { store } from "./store.js";
import { renderMarkdown } from "./markdown.js";
import { rafThrottle } from "./throttle.js";
import { native } from "./native.js";
import { normalizeDisplayResults } from "./display-result.js";
import { UI_COPY } from "./ui-copy.js";
import { renderDocPage, renderSettingsPage, versionLabel, loadAppVersion,
         ROOT_KEY_ID } from "./pages.js";

// API 地址：默认与页面同源（空串 → 相对路径）；可由宿主注入 window.__API_BASE__
const API_BASE = window.__API_BASE__ || "";

// ---------- DOM ----------
const chatEl = document.getElementById("chat");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send");
const totalEl = document.getElementById("total-tokens");
const ctxEl = document.getElementById("ctxinfo");
const toastEl = document.getElementById("toast");
const sidebarEl = document.getElementById("sidebar");
const scrimEl = document.getElementById("scrim");
const convListEl = document.getElementById("conv-list");
const convSearchEl = document.getElementById("conv-search");
const menuBtn = document.getElementById("menu-btn");
const historyBtn = document.getElementById("history-btn");
const newChatTopBtn = document.getElementById("new-chat-top");
const convTitleEl = document.getElementById("conv-title");
const viewChat = document.getElementById("view-chat");
const viewPage = document.getElementById("view-page");
const pageBody = document.getElementById("page-body");
const verBadge = document.getElementById("ver-badge");
const llmBtn = document.getElementById("llm-btn");

// ---------- 运行态 ----------
const state = {
  generating: false,
  controller: null,
  convId: null,       // 当前对话 id（与 store.currentId 同步）
  live: null,         // {convId, index}：此刻正在流式生成的那条消息（见 renderAssistantRow）
  providers: [],      // 服务端返回的供应商列表（用于顶栏显示名字）
  llmReady: false,
  apiKeyError: null,
  autoFollow: true,
  pendingBottom: false,
  readingAnchor: null,
};

// ---------- LLM 供应商（BYOK）----------
function activeCloudEntry() {
  const entries = store.llmEntries().filter((e) => !["ondevice", "ollama", "lmstudio", "vllm"].includes(e.id));
  return entries.find((e) => e.id === store.llm().activeId) || entries[0] || null;
}

/** 组装随请求下发的供应商覆盖；未做任何选择时返回空对象（用服务端配置）。 */
function llmSpec() {
  const e = activeCloudEntry();
  if (!e) return {};                       // 一条都没配 → 用服务端配置
  const spec = {};
  if (e.custom || !e.id || e.id.startsWith("custom-")) {
    if (e.base_url) spec.base_url = e.base_url;   // 自定义：按地址下发
  } else {
    spec.provider = e.id;                        // 预设：按 id 下发
    if (e.base_url) spec.base_url = e.base_url;   // 允许覆盖地址（中转站场景）
  }
  if (e.model) spec.model = e.model;
  if (e.api) spec.api = e.api;
  if (e.key) spec.api_key = e.key;
  if (e.max_tokens) spec.max_tokens = e.max_tokens;
  if (e.context_tokens) spec.context_tokens = e.context_tokens;
  return spec;
}

/**
 * 判断"当前是否已配置可用模型"。
 *
 * **必须先看客户端自己的 BYOK 条目**，再看服务端 llm_ready：
 * Android 等自备 Key 的场景下服务端根本没有 Key（返回 llm_ready=false），
 * 若只看服务端，用户明明配好了却会被判定为"未配置"而拦下发送（实测踩到）。
 */
function llmConfigured() {
  const e = activeCloudEntry();
  if (e) {
    if (e.key) return true;
  }
  return !!state.llmReady || !!state.llmMock;
}

/**
 * 顶栏入口：显示"⚙️ 设置"，并把当前实际生效的供应商接在后面。
 *
 * 为什么还留这一截后缀：这个按钮同时是"现在到底在用谁"的唯一提示（BYOK 场景下
 * 用户经常以为在跑 A 其实在跑 B）。按钮名字必须叫「设置」（它现在同时承载模型配置
 * 与关于），但把供应商整段丢掉是拿功能换整洁，所以折中成「设置 · <名字>」——
 * 后缀截断到 16 字，否则长模型名会把标题和对话名挤没。
 */
async function refreshProviderBadge() {
  if (!llmBtn) return;
  const e = activeCloudEntry();
  // 没有可用供应商时沿用旧文案"未配置"：这是它变成纯「设置」入口后
  // 唯一还留在顶栏的配置状态提示（引导条被用户关掉时全靠它）
  let text = "未配置";
  let title = "尚未配置模型 —— 点击选择供应商并填入 API Key";
  if (e) {
    text = e.model || e.label || e.id;
    title = `当前使用：${e.label || e.id}${e.model ? " · " + e.model : ""}`
      + `${e.base_url ? "\n" + e.base_url : ""}`;
    if (!e.key) {
      text = "缺 Key";
      title += "\n（尚未填写 API Key）";
    }
  } else {
    const p = state.providers.find((x) => x.id === state.serverActive);
    if (p) { text = p.label; title = "使用服务端配置：" + p.label; }
  }
  const txtEl = llmBtn.querySelector(".txt");
  if (txtEl) {
    if (String(text).length > 16) text = String(text).slice(0, 16) + "…";
    txtEl.textContent = text ? " 设置 · " + text : " 设置";
  }
  llmBtn.title = title + "（点击修改）";
}

async function loadProviders() {
  try {
    const resp = await fetch(API_BASE + "/api/providers");
    if (!resp.ok) return;
    const body = await resp.json();
    state.providers = body.providers || [];
    state.serverActive = body.active || "";
    state.llmReady = !!body.llm_ready;
    state.llmMock = !!body.mock;
  } catch {
    /* 服务端不可达时保持空列表，顶栏退回"服务端默认" */
  }
  await refreshProviderBadge();
  renderLlmNotice();
  updateCtxInfo(null);   // 参数为空时自行取当前对话消息
}

/**
 * 未配置模型时给出**可操作的引导**（而不是等用户问完再报错）。
 *
 * 三种情形分开处理，因为用户该做的事完全不同：
 *   - Mock 模式：能用，但回答是确定性的本地 mock，需说明清楚，避免误以为是真模型；
 *   - 未配置任何 Key：引导去设置页填自己的 Key（社区版不内置 Key）；
 *   - 已配置：不打扰。
 */
function renderLlmNotice() {
  const box = document.getElementById("llm-notice");
  const text = document.getElementById("llm-notice-text");
  const go = document.getElementById("llm-notice-go");
  if (!box || !text) return;

  if (state.llmNoticeDismissed) {
    box.classList.add("hidden");
    return;
  }

  if (state.llmMock) {
    text.textContent = "当前为 Mock 演示模式（LLM_MOCK=true）：回答由本地确定性规则生成，不是真实模型。";
    go.textContent = "配置真实模型";
    box.classList.remove("hidden");
  } else if (!llmConfigured()) {
    text.textContent = "尚未配置模型 API：请在设置页添加云端服务提供方，填写自己的 API Key。";
    go.textContent = "去配置";
    box.classList.remove("hidden");
  } else {
    box.classList.add("hidden");
  }

  if (!go._wired) {
    go._wired = true;
    go.addEventListener("click", () => navigate("#/settings"));
    const close = document.getElementById("llm-notice-close");
    if (close) {
      close.addEventListener("click", () => {
        // 关闭是会话级记忆：不要每次重绘又弹回来
        state.llmNoticeDismissed = true;
        box.classList.add("hidden");
      });
    }
  }
}

// ---------- 小工具 ----------
// Markdown 渲染在 ./markdown.js 里：它的规则（尤其表格）边界情况多，
// 单独成模块才能用 node 直接跑测试，不必起浏览器。
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
function details(title, bodyNodes) {
  const d = el("details", "fold");
  d.appendChild(el("summary", null, title));
  const div = el("div", "fold-body");
  for (const n of bodyNodes) div.appendChild(n);
  d.appendChild(div);
  return d;
}
let toastTimer = null;
/** duration 可调：默认 1.8s 够读"已复制"，但读不完一句要用户照做的长提示。 */
function toast(msg, duration = 1800) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), duration);
}
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    const ta = el("textarea", null, text);
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); toast("已复制"); }
    catch { toast("复制失败，请手动选择"); }
    document.body.removeChild(ta);
  }
}
/** 分享一条回答：正文与「复制」保持一致（避免"复制到的是 A、分享出去的是 B"），
 *  再附上来源链接 —— 收到的人想核对出处时不必回头找。 */
async function shareText(msg) {
  let text = String((msg && msg.content) || "");
  const sources = (msg && msg.meta && msg.meta.sources) || [];
  if (Array.isArray(sources) && sources.length) text += "\n\n数据来源：\n" + sources.join("\n");
  try {
    const ok = await native.share(text);
    // 返回值只表示"分享面板有没有被唤起"：用户在系统面板上按返回取消是正常操作，
    // 弹错误只会让人以为程序坏了。
    //
    // 但**桌面浏览器没有分享面板**时，native.share 会退化成"复制到剪贴板" ——
    // 那种情况下什么都不说，用户点了没反应，只会以为坏了。所以这一条要如实讲。
    if (!native.available && !navigator.share) {
      toast(ok ? "当前环境没有分享面板，内容已复制到剪贴板" : "复制失败：请改用「复制」按钮");
    }
  } catch (e) {
    toast("分享失败：" + ((e && e.message) || "当前环境不支持分享"));
  }
}

function rememberReadingAnchor() {
  if (state.autoFollow) return;
  const box = chatEl.getBoundingClientRect();
  const row = [...chatEl.children].find((node) => {
    const rect = node.getBoundingClientRect();
    return rect.height > 0 && rect.bottom > box.top && rect.top < box.bottom;
  });
  if (row) state.readingAnchor = { element: row, top: row.getBoundingClientRect().top };
}

function restoreReadingAnchor() {
  const anchor = state.readingAnchor;
  if (!anchor?.element?.isConnected || state.autoFollow) return;
  const delta = anchor.element.getBoundingClientRect().top - anchor.top;
  if (Math.abs(delta) > 0.1) chatEl.scrollTop += delta;
}

function scrollBottom(force = false) {
  if (!force && !state.autoFollow) {
    state.pendingBottom = true;
    const cue = document.getElementById("new-content-cue");
    if (cue) cue.hidden = false;
    requestAnimationFrame(restoreReadingAnchor);
    return;
  }
  chatEl.scrollTop = chatEl.scrollHeight;
  // WebView 在首帧排版后还可能调整字体和按钮高度；下一帧再对齐一次。
  requestAnimationFrame(() => { if (force || state.autoFollow) chatEl.scrollTop = chatEl.scrollHeight; });
}

function relTime(ts) {
  const d = Date.now() - (ts || 0);
  if (d < 60e3) return "刚刚";
  if (d < 3600e3) return Math.floor(d / 60e3) + " 分钟前";
  if (d < 86400e3) return Math.floor(d / 3600e3) + " 小时前";
  if (d < 7 * 86400e3) return Math.floor(d / 86400e3) + " 天前";
  const dt = new Date(ts);
  return `${dt.getMonth() + 1}月${dt.getDate()}日`;
}

// ---------- 通用弹层（确认 / 输入） ----------
function modal({ title, message, value, okText = "确定", danger = false, withInput = false }) {
  return new Promise((resolve) => {
    const scrim = el("div", "modal-scrim");
    const box = el("div", "modal");
    box.appendChild(el("h4", null, title));
    if (message) box.appendChild(el("p", null, message));
    let input = null;
    if (withInput) {
      input = el("input");
      input.value = value || "";
      input.style.cssText = "width:100%;background:var(--panel-2);border:1px solid var(--line);" +
        "color:var(--fg);border-radius:10px;padding:11px 12px;font-size:15px;outline:none;margin-bottom:12px";
      box.appendChild(input);
    }
    const actions = el("div", "modal-actions");
    const cancel = el("button", "btn", "取消");
    const ok = el("button", "btn " + (danger ? "danger" : "primary"), okText);
    actions.appendChild(cancel);
    actions.appendChild(ok);
    box.appendChild(actions);
    scrim.appendChild(box);
    document.body.appendChild(scrim);
    if (input) { input.focus(); input.select(); }

    const close = (v) => { document.body.removeChild(scrim); resolve(v); };
    cancel.addEventListener("click", () => close(null));
    ok.addEventListener("click", () => close(withInput ? (input.value || "") : true));
    scrim.addEventListener("click", (e) => { if (e.target === scrim) close(null); });
    document.addEventListener("keydown", function onKey(e) {
      if (e.key === "Escape") { document.removeEventListener("keydown", onKey); close(null); }
      if (e.key === "Enter" && withInput) { document.removeEventListener("keydown", onKey); close(input.value || ""); }
    });
  });
}

// ---------- 侧栏：会话列表 ----------
function openSidebar() { sidebarEl.classList.add("open"); scrimEl.classList.add("show"); }
function closeSidebar() { sidebarEl.classList.remove("open"); scrimEl.classList.remove("show"); }

function renderConvList() {
  convListEl.innerHTML = "";
  const kw = convSearchEl ? convSearchEl.value : "";
  const list = store.search(kw);
  if (!list.length) {
    convListEl.appendChild(el("div", "side-empty", kw ? "没有匹配的对话" : "还没有对话，点上方「＋ 新对话」开始"));
    return;
  }
  for (const c of list) {
    const item = el("div", "conv-item" + (c.id === state.convId ? " active" : ""));
    const body = el("div", "cbody");
    body.appendChild(el("div", "ctitle", c.title || "新对话"));
    const sub = `${c.messages.length} 条 · ${relTime(c.updatedAt)}`;
    body.appendChild(el("div", "ctime", sub));
    item.appendChild(body);

    const more = el("button", "cmore", "⋯");
    more.title = "更多操作";
    more.addEventListener("click", async (e) => {
      e.stopPropagation();
      const act = await modal({
        title: c.title || "新对话",
        message: "选择操作：",
        okText: "重命名",
      });
      if (act === null) return;                       // 取消
      const name = await modal({ title: "重命名对话", value: c.title || "", withInput: true, okText: "保存" });
      if (name === null) return;
      store.rename(c.id, name);
      renderConvList();
      if (c.id === state.convId) updateHeaderTitle();
      toast("已重命名");
    });
    // 长按/右键删除（桌面右键，移动端用长按）
    const removeConv = async () => {
      const ok = await modal({
        title: "删除对话",
        message: `确定删除「${c.title || "新对话"}」？该操作不可恢复。`,
        okText: "删除", danger: true,
      });
      if (!ok) return;
      const wasCurrent = c.id === state.convId;
      store.remove(c.id);
      if (wasCurrent) {
        state.convId = store.currentId;
        renderChat();
      }
      renderConvList();
      toast("已删除");
    };
    item.addEventListener("contextmenu", (e) => { e.preventDefault(); removeConv(); });
    let pressTimer = null;
    item.addEventListener("touchstart", () => { pressTimer = setTimeout(removeConv, 650); }, { passive: true });
    item.addEventListener("touchend", () => clearTimeout(pressTimer));
    item.addEventListener("touchmove", () => clearTimeout(pressTimer));
    item.addEventListener("click", () => switchConv(c.id));

    item.appendChild(more);
    convListEl.appendChild(item);
  }
}

function switchConv(id) {
  if (state.generating) stopGeneration();
  store.setCurrent(id);
  state.convId = id;
  renderConvList();
  updateHeaderTitle();
  renderChat();
  closeSidebar();
  navigate("#/c/" + id, true);
}

function newConv() {
  if (state.generating) stopGeneration();
  const c = store.create();
  state.convId = c.id;
  renderConvList();
  updateHeaderTitle();
  renderChat();
  closeSidebar();
  navigate("#/c/" + c.id, true);
  inputEl.focus();
}

function updateHeaderTitle() {
  const c = store.get(state.convId);
  if (convTitleEl) convTitleEl.textContent = c && c.messages.length ? (c.title || "") : "";
}

// ---------- 路由（hash） ----------
function showView(name) {
  viewChat.classList.toggle("active", name === "chat");
  viewPage.classList.toggle("active", name === "page");
}
function navigate(hash, replace = false) {
  if (replace) history.replaceState(null, "", hash);
  else location.hash = hash;
  if (replace) handleRoute();
}
function handleRoute() {
  const h = location.hash || "";
  if (h !== "#/history") pageBody.classList.remove("history-container");
  const appHeader = document.querySelector(".main > header");
  if (appHeader) appHeader.hidden = h === "#/history";
  const mConv = h.match(/^#\/c\/([\w-]+)/);
  const mDoc = h.match(/^#\/doc\/(\w+)/);
  if (h === "#/history") {
    pageBody.classList.add("history-container");
    showView("page"); pageBody.innerHTML = "";
    pageBody.appendChild(renderHistoryPage()); pageBody.scrollTop = 0;
    return;
  }
  if (mDoc) {
    // 「关于」已并入设置页：老书签/旧链接仍指向 #/doc/about，重定向过去而不是
    // 落到"文档占位"页 —— pages.js 里已经没有这个 key 了。
    if (mDoc[1] === "about") return navigate("#/settings", true);
    showView("page");
    pageBody.innerHTML = "";
    pageBody.appendChild(renderDocPage(mDoc[1], {
      onBack: () => navigate("#/c/" + state.convId),
      navigate,
    }));
    pageBody.scrollTop = 0;
    return;
  }
  if (/^#\/settings/.test(h)) {
    showView("page");
    pageBody.innerHTML = "";
    pageBody.appendChild(renderSettingsPage({
      onBack: () => navigate("#/c/" + state.convId),
      navigate,
      // 设置页的表单很长，卡片底部那行 .sub 提示常落在折叠线以下；
      // 把外壳的 toast 传下去，按钮的反馈才真正可见（样式仍是同一套）。
      toast,
      apiKeyError: state.apiKeyError,
    }));
    pageBody.scrollTop = 0;
    void refreshProviderBadge();
    return;
  }
  if (mConv && store.get(mConv[1])) {
    state.convId = mConv[1];
    store.setCurrent(mConv[1]);
    showView("chat");
    renderConvList();
    updateHeaderTitle();
    renderChat();
    // 每次回到对话页都重算：用户可能刚在设置页配好了 Key，
    // 若只在启动时算一次，配完返回仍会看到那条引导条（真实反馈过）。
    void loadProviders();
    return;
  }
  // 默认：当前对话
  state.convId = store.currentId || store.create().id;
  showView("chat");
  navigate("#/c/" + state.convId, true);
}

function historySubtitle(conversation) {
  const result = [...conversation.messages].reverse().find((message) =>
    message.role === "assistant" && message.meta?.displayResults?.length)?.meta.displayResults[0];
  if (result?.kind === "emu_routing") return `${(result.records || []).length} 条交路记录`;
  if (result?.kind === "train_schedule") {
    const route = result.from_station && result.to_station
      ? `${result.from_station} → ${result.to_station}` : "";
    return route || `${result.train_code || UI_COPY.schedule.source} · ${result.date || ""}`;
  }
  const question = [...conversation.messages].reverse().find((message) => message.role === "user")?.content || "";
  return question.length > 24 ? `${question.slice(0, 24)}…` : question;
}

function renderHistoryPage() {
  const root = el("section", "history-page");
  const header = el("div", "history-header");
  const back = el("button", "history-back"); back.type = "button";
  back.addEventListener("click", () => navigate("#/c/" + state.convId));
  back.appendChild(icon("back")); header.append(back, el("strong", "", UI_COPY.history.title));
  const close = el("button", "history-close"); close.type = "button"; close.appendChild(icon("close"));
  close.addEventListener("click", () => navigate("#/c/" + state.convId)); header.append(close); root.appendChild(header);
  const search = document.createElement("input"); search.type = "search"; search.placeholder = UI_COPY.history.search;
  search.className = "history-search"; root.appendChild(search);
  const create = el("button", "history-create", UI_COPY.history.create); create.type = "button";
  create.prepend(icon("add"));
  create.addEventListener("click", () => newConv()); root.appendChild(create);
  const list = el("div", "history-list"); root.appendChild(list);
  const footer = el("div", "history-footer");
  const settings = el("button", "", UI_COPY.history.settings); settings.type = "button";
  settings.prepend(icon("settings")); settings.addEventListener("click", () => navigate("#/settings"));
  const help = el("button", "", UI_COPY.history.help); help.type = "button";
  help.prepend(icon("help")); help.addEventListener("click", () => navigate("#/doc/help"));
  footer.append(settings, help); root.appendChild(footer);
  const render = () => {
    list.replaceChildren();
    const now = new Date(); const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const groups = new Map();
    for (const conversation of store.search(search.value)) {
      const day = new Date(conversation.updatedAt || 0); const start = new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
      const label = start >= todayStart ? UI_COPY.history.today : start >= todayStart - 86400000 ? UI_COPY.history.yesterday : UI_COPY.history.earlier;
      if (!groups.has(label)) groups.set(label, []); groups.get(label).push(conversation);
    }
    for (const label of [UI_COPY.history.today, UI_COPY.history.yesterday, UI_COPY.history.earlier]) {
      const conversations = groups.get(label) || []; if (!conversations.length) continue;
      list.appendChild(el("h3", "history-group-title", label));
      for (const conversation of conversations) {
        const item = el("article", "history-item" + (conversation.id === state.convId ? " active" : ""));
        const select = el("button", "history-select"); select.type = "button";
        const title = conversation.title || "新对话";
        const subtitle = historySubtitle(conversation);
        select.appendChild(el("strong", "", title));
        if (subtitle && subtitle.replace(/^(查看|查询|看看)/, "") !== title) {
          select.appendChild(el("span", "", subtitle));
        }
        select.addEventListener("click", () => switchConv(conversation.id));
        const more = el("button", "history-more"); more.type = "button"; more.appendChild(icon("more"));
        more.addEventListener("click", () => showHistoryActions(conversation));
        item.append(select, more); list.appendChild(item);
      }
    }
    if (!list.childElementCount) list.appendChild(el("div", "history-empty", UI_COPY.history.noMatches));
  };
  search.addEventListener("input", render); render();
  return root;
}

function showHistoryActions(conversation) {
  const scrim = el("div", "history-action-scrim");
  const panel = el("div", "history-action-panel");
  panel.appendChild(el("div", "history-action-title", UI_COPY.history.operations));
  const rename = el("button", "history-rename", UI_COPY.history.rename); rename.type = "button";
  rename.prepend(icon("edit"));
  rename.addEventListener("click", async () => {
    scrim.remove(); const title = await modal({ title: "重命名对话", value: conversation.title || "", withInput: true, okText: "保存" });
    if (title === null) return; store.rename(conversation.id, title); handleRoute(); renderConvList();
  });
  const remove = el("button", "danger", UI_COPY.history.delete); remove.type = "button";
  remove.prepend(icon("trash"));
  remove.addEventListener("click", async () => {
    scrim.remove(); const ok = await modal({ title: "删除对话", message: `确定删除「${conversation.title || "新对话"}」？该操作不可恢复。`, okText: "删除", danger: true });
    if (!ok) return; const wasCurrent = conversation.id === state.convId; store.remove(conversation.id);
    if (wasCurrent) { state.convId = store.currentId; navigate("#/c/" + state.convId); }
    else handleRoute(); renderConvList();
  });
  const cancel = el("button", "history-action-cancel", UI_COPY.history.cancel); cancel.type = "button"; cancel.addEventListener("click", () => scrim.remove());
  panel.append(rename, remove); scrim.append(panel, cancel); scrim.addEventListener("click", (event) => { if (event.target === scrim) scrim.remove(); });
  document.body.appendChild(scrim);
}
window.addEventListener("hashchange", handleRoute);

// ---------- 对话渲染 ----------
/** 取 [0, upto) 的消息作为 history（不含当前待发消息）。
 *  被中断（stopped）或出错（error）的助手消息**不进入上下文**：
 *  把半截回答当完整历史回传会污染后续轮次。
 */
function buildHistory(messages, upto) {
  return messages.slice(0, upto)
    .filter((m) => (m.role === "user" || m.role === "assistant") && String(m.content || "").trim())
    .filter((m) => !(m.role === "assistant" && m.meta && (m.meta.stopped || m.meta.error)))
    .map((m) => ({ role: m.role, content: m.content }));
}
function updateCtxInfo(messages) {
  if (!ctxEl) return;
  const msgs = messages || (store.get(state.convId) || {}).messages || [];
  const turns = msgs.filter((m) => m.role === "user").length;
  ctxEl.textContent = turns ? `上下文：${turns} 轮` : "";
}

function actionBtn(label, title, onClick) {
  const b = el("button", null, label);
  const asset = label === "复制" ? "copy" : label === "重新生成" ? "refresh" : null;
  if (asset) { b.textContent = ""; b.append(icon(asset), document.createTextNode(label)); }
  b.title = title || label;
  b.addEventListener("click", onClick);
  return b;
}

function icon(name, cls = "") {
  const img = document.createElement("img");
  img.className = `ui-icon ${cls}`.trim();
  img.src = `assets/icons/${name}.svg`;
  img.alt = "";
  img.setAttribute("aria-hidden", "true");
  return img;
}

function renderUserRow(msg, index) {
  const row = el("div", "row user");
  const line = el("div", "user-message-line");
  line.appendChild(el("div", "bubble", msg.content));
  const avatar = el("span", "user-avatar"); avatar.appendChild(icon("person")); line.appendChild(avatar);
  row.appendChild(line);
  const actions = el("div", "actions");
  actions.appendChild(actionBtn("复制", "复制这条提问", () => copyText(msg.content)));
  actions.appendChild(actionBtn("编辑", "修改后重新发送（此后的消息将被丢弃）",
    () => startEdit(index, row, msg)));
  row.appendChild(actions);
  return row;
}

function renderAssistantRow(msg, index) {
  const row = el("div", "row assistant");
  const bubble = el("div", "bubble");
  const meta = msg.meta || {};

  const intent = el("div", "intent", intentLabel(meta.intent));
  if (meta.questionType && meta.questionType !== "realtime") {
    intent.appendChild(el("span", "qtype " + meta.questionType,
      meta.questionType === "knowledge" ? "知识型" : "混合型"));
  }
  bubble.appendChild(intent);

  const progress = el("div", "progress", "");
  bubble.appendChild(progress);
  const loadingCard = document.createElement("section");
  loadingCard.className = "query-loading";
  loadingCard.appendChild(el("span", "query-spinner"));
  loadingCard.appendChild(el("strong", "", UI_COPY.loading.title));
  loadingCard.appendChild(el("div", "query-loading-stage", UI_COPY.loading.stage));
  const skeletons = el("div", "query-skeletons");
  skeletons.appendChild(el("i", "")); skeletons.appendChild(el("i", "short"));
  loadingCard.appendChild(skeletons);
  const loadingFoot = el("div", "query-loading-foot", UI_COPY.loading.preserve);
  bubble.appendChild(loadingCard);
  bubble.appendChild(loadingFoot);
  if (!meta.streaming) loadingCard.hidden = true;

  const ans = el("div", "md");
  ans.innerHTML = renderMarkdown(msg.content || "");
  if (ans.querySelector("table")) row.classList.add("has-table-answer");
  bubble.appendChild(ans);
  const structuredBox = el("div", "structured-results");
  renderTrainScheduleCards(structuredBox, meta.displayResults, meta);
  bubble.appendChild(structuredBox);

  const stats = el("div", "stats", "");
  if (meta.usage || meta.latencyMs != null) stats.textContent = formatStats(meta);
  bubble.appendChild(stats);

  const hintBox = el("div");
  bubble.appendChild(hintBox);
  if (meta.questionType === "knowledge" || meta.questionType === "mixed") {
    hintBox.appendChild(el("div", "knowledge-hint",
      "本题含知识型内容，部分信息可能未经过检索，请以官方资料为准。"));
  }
  const sourcesBox = el("div");
  bubble.appendChild(sourcesBox);
  renderSources(sourcesBox, meta.sources);

  // 正文和来源优先；排查信息仍可展开查看。
  const logs = details("流程日志（意图 / 抽取 / 检索 / 生成）", []);
  const logBody = logs.querySelector(".fold-body");
  if (Array.isArray(meta.processLogs)) {
    for (const l of meta.processLogs) logBody.appendChild(el("div", "log-line", "· " + l));
  }
  bubble.appendChild(logs);

  const thinkPre = el("pre", null, meta.thinking || "");
  const thinkDetails = details("思考过程", [thinkPre]);
  if (!meta.thinking) thinkDetails.classList.add("hidden");
  bubble.appendChild(thinkDetails);

  if (meta.stopped) bubble.appendChild(el("div", "stopped-tag", "⏹ 已停止生成（内容可能不完整）"));
  // 上次生成途中被系统回收（本应用刻意**不用前台服务**：不为一个聊天应用去要保活权限）。
  // 本地只留下当时已经落盘的部分，如实说明，别让用户以为回答本来就这么短。
  //
  // 必须排除**此刻正在生成**的那一条：它同样带着 streaming 标记，但它是活的。
  // 这里曾经只判断 meta.streaming，而那条消息在流式开始时就渲染好了 —— 结果是每生成
  // 一次都立刻挂上「被中断」的提示，直到下次重渲染（切换对话）才消失。
  // 判据改用 state.live（登记"当前在生成哪条"），所以生成中途切走再切回来也不会误报。
  const live = !!(state.live && state.live.convId === state.convId
                  && state.live.index === index);
  if (meta.streaming && !live) {
    bubble.appendChild(el("div", "stopped-tag",
      "⏹ 上次回答在生成中被系统中断（应用切到后台后被回收），以上是当时已生成的部分。"
      + "可点「重新生成」重问一次。"));
  }
  // 模型因长度上限停止：如实标注，否则用户会以为内容本来就到这儿了
  if (meta.truncated) {
    bubble.appendChild(el("div", "truncate-note",
      "⚠️ 回答因输出长度上限被截断（内容不完整）。可在「⚙️ 设置 → 编辑」里调大「最大输出」，"
      + "或让问题更聚焦后重问。"));
  }
  if (meta.error) bubble.appendChild(buildConnectionError(meta.error));

  const followups = followupQuestions(meta, row.classList.contains("has-table-answer") && !(meta.displayResults || []).length);
  let followupBox = null;
  if (followups.length && !meta.streaming && !meta.error) {
    followupBox = el("div", "followups");
    for (const question of followups) {
      const b = el("button", "followup", question.label);
      b.type = "button";
      if (question.icon) b.prepend(icon(question.icon));
      b.addEventListener("click", () => {
        inputEl.value = inputEl.value.trim()
          ? `${inputEl.value.trim()}\n${question.text}`
          : question.text;
        autoGrow();
        inputEl.focus();
      });
      followupBox.appendChild(b);
    }
    bubble.appendChild(followupBox);
  }

  row.appendChild(bubble);

  const actions = el("div", "actions");
  actions.appendChild(actionBtn("复制", "复制回答", () => copyText(msg.content || "")));
  actions.appendChild(actionBtn("分享", "分享这条回答（调起系统分享面板）", () => shareText(msg)));
  const regen = actionBtn("重新生成", "丢弃这条回答并重新生成", () => regenerate(index));
  regen.disabled = state.generating;
  actions.appendChild(regen);
  row.appendChild(actions);
  if (row.classList.contains("has-table-answer")) {
    row.appendChild(el("div", "reading-guidance", UI_COPY.reading.guidance));
  }

  const refs = { row, intent, progress, loadingCard, loadingFoot, loadingStage: loadingCard.querySelector(".query-loading-stage"), recognized: "", ans, structuredBox, stats, logBody, thinkPre, thinkDetails, bubble, hintBox, sourcesBox, actions, followupBox };
  syncStructuredPresentation(refs, meta);

  return refs;
}

function syncStructuredPresentation(refs, meta) {
  const hasStructured = !!refs.structuredBox.childElementCount;
  const results = meta.displayResults || [];
  const isScheduleResult = results.some((result) => result.kind === "train_schedule");
  const isRoutingResult = results.some((result) => result.kind === "emu_routing");
  const isEmptyResult = results.some((result) => result.kind === "empty");
  const isBatchResult = results.some((result) => result.kind === "train_schedule_batch");
  const hasConnectionError = !!meta.error || !!refs.bubble.querySelector(".connection-error");
  const isLoading = !!meta.streaming && !hasConnectionError && !hasStructured;
  const isWideResult = !!refs.structuredBox.querySelector(".batch-card, .empty-routing-card");
  refs.row.classList.toggle("has-structured", hasStructured);
  refs.row.classList.toggle("has-schedule-result", isScheduleResult);
  refs.row.classList.toggle("has-routing-result", isRoutingResult);
  refs.row.classList.toggle("has-empty-result", isEmptyResult);
  refs.row.classList.toggle("has-batch-result", isBatchResult);
  refs.row.classList.toggle("has-connection-error", hasConnectionError);
  refs.row.classList.toggle("has-loading", isLoading);
  refs.row.classList.toggle("has-wide-result", isWideResult);
  refs.intent.classList.toggle("hidden", hasStructured || hasConnectionError || isLoading);
  refs.stats.hidden = hasStructured || hasConnectionError || isLoading;
  refs.sourcesBox.hidden = hasStructured || hasConnectionError || isLoading;
  refs.logBody.parentElement.hidden = hasStructured || hasConnectionError || isLoading;
  refs.thinkDetails.hidden = hasStructured || hasConnectionError || isLoading;
  refs.loadingFoot.hidden = !isLoading;
  if (isLoading) {
    refs.row.insertBefore(refs.loadingCard, refs.actions);
    refs.row.insertBefore(refs.loadingFoot, refs.actions);
  }
  const genericIntro = /^(查询结果如下[。！!]?|固定验收记录)$/.test(String(refs.ans.textContent || "").trim());
  if (hasStructured && genericIntro) refs.ans.hidden = true;
  else refs.ans.hidden = false;
  if (hasStructured) {
    refs.row.insertBefore(refs.structuredBox, refs.actions);
    if (refs.followupBox) refs.row.insertBefore(refs.followupBox, refs.actions.nextSibling);
    if (genericIntro && !meta.error) refs.bubble.hidden = true;
  } else if (!isLoading) {
    refs.bubble.hidden = false;
  }
}

function classifyConnectionError(message) {
  const text = String(message || "").toLowerCase();
  if (/未配置|尚未配置|未填写|missing.*(?:key|config)|not configured|base_url|非公网|地址被拒绝/.test(text)) return "configuration";
  if (/\b401\b|invalid[_ ](?:api[_ ])?key|incorrect.*api.*key|密钥无效|认证失败|鉴权失败|unauthorized/.test(text)) return "auth";
  if (text.includes("timeout") || text.includes("connect") || text.includes("网络") || text.includes("连接")) return "network";
  return "service";
}

function sanitizeConnectionError(message) {
  let safe = String(message || "连接失败").replace(/sk-[A-Za-z0-9_-]{8,}/g, "[已隐藏]");
  const key = activeCloudEntry()?.key;
  if (key && key.length >= 8) safe = safe.split(key).join("[已隐藏]");
  return safe;
}

function buildConnectionError(message, retry) {
  const kind = classifyConnectionError(message);
  const card = el("section", "connection-error");
  const titleRow = el("div", "connection-title-row");
  titleRow.append(icon("cloud-error", "connection-icon"), el("strong", "connection-error-title", ({ configuration: "请检查云端模型配置", auth: "模型认证失败，请检查 API Key", network: "网络连接失败，请稍后重试", service: UI_COPY.error.title })[kind]));
  card.appendChild(titleRow);
  card.appendChild(el("div", "connection-error-copy", UI_COPY.error.message));
  const actions = el("div", "connection-error-actions");
  const retryButton = el("button", "connection-retry", UI_COPY.error.retry); retryButton.type = "button";
  retryButton.addEventListener("click", () => retry && retry());
  const settingsButton = el("button", "connection-settings", UI_COPY.error.settings); settingsButton.type = "button";
  if (kind === "service" || kind === "network") settingsButton.textContent = "查看错误详情";
  settingsButton.addEventListener("click", () => {
    if (kind === "service" || kind === "network") { detailsEl.open = !detailsEl.open; return; }
    if (kind === "auth") state.apiKeyError = { provider: activeCloudEntry()?.id || "", message: UI_COPY.error.invalidKey };
    navigate("#/settings");
  });
  actions.append(retryButton, settingsButton); card.appendChild(actions);
  const detailsEl = document.createElement("details");
  detailsEl.appendChild(el("summary", "", UI_COPY.error.details));
  detailsEl.appendChild(el("pre", "", sanitizeConnectionError(message)));
  card.appendChild(detailsEl);
  if (kind === "auth") {
    card.appendChild(el("label", "connection-key-label", UI_COPY.error.keyLabel));
    const field = document.createElement("input"); field.type = "password"; field.disabled = true;
    field.value = "••••••••••••••••"; field.className = "connection-key-invalid";
    card.appendChild(field); card.appendChild(el("div", "connection-key-error", UI_COPY.error.invalidKey));
  }
  return card;
}

function renderTrainScheduleCards(container, results, requestMeta = {}) {
  container.replaceChildren();
  for (const result of results || []) {
    if (result.kind === "empty") {
      const card = el("section", "schedule-card empty-routing-card");
      const mark = el("div", "empty-routing-mark");
      for (const iconName of ["train-empty", "search-empty"]) {
        const image = document.createElement("img"); image.src = `./assets/icons/${iconName}.svg`;
        image.alt = ""; mark.appendChild(image);
      }
      card.appendChild(mark);
      card.appendChild(el("strong", "empty-routing-title", UI_COPY.empty.title));
      card.appendChild(el("div", "empty-routing-copy", UI_COPY.empty.message));
      const dateLabel = result.date ? `${result.date.slice(5).replace("-", "月")}日` : "选择日期";
      const datePill = el("div", "empty-routing-date"); datePill.append(icon("calendar"), document.createTextNode(dateLabel));
      card.appendChild(datePill);
      const actions = el("div", "empty-routing-actions");
      const dateInput = document.createElement("input"); dateInput.type = "date";
      dateInput.className = "visually-hidden-date"; dateInput.value = result.date || "";
      dateInput.addEventListener("change", () => {
        sendStructuredAction({ kind: "emu_routing", query: result.query, date: dateInput.value },
          `查询 ${dateInput.value} 的交路记录`);
      });
      const change = el("button", "routing-batch", UI_COPY.empty.changeDate); change.type = "button";
      change.addEventListener("click", () => {
        if (dateInput.showPicker) dateInput.showPicker(); else dateInput.click();
      });
      const recent = el("button", "routing-recent", UI_COPY.empty.recent); recent.type = "button";
      recent.addEventListener("click", () => sendStructuredAction(
        { kind: "emu_routing", query: result.query, date: null }, "查看最近交路记录"));
      actions.append(change, recent, dateInput); card.appendChild(actions);
      card.appendChild(el("div", "empty-routing-foot", UI_COPY.empty.historyNote));
      container.appendChild(card);
      continue;
    }
    if (result.kind === "train_schedule_batch") {
      const card = el("section", "schedule-card batch-card");
      let retry = null;
      let retainedNote = null;
      const items = result.items || [];
      const succeeded = items.filter((item) => item.status === "success").length;
      const head = el("div", "schedule-head"); head.appendChild(el("strong", "", UI_COPY.batch.title));
      head.appendChild(el("span", "batch-count", `${succeeded} / ${items.length} ${UI_COPY.batch.returned}`)); card.appendChild(head);
      for (const item of items) {
        const row = el("div", "batch-item");
        row.appendChild(el("span", "", item.train_code || "—"));
        const status = el("span", item.status === "success" ? "batch-ok" : "batch-failed");
        status.append(icon(item.status === "success" ? "check" : "warning"), document.createTextNode(item.status === "success" ? UI_COPY.batch.found : UI_COPY.batch.pending));
        row.appendChild(status);
        card.appendChild(row);
      }
      const failed = items.filter((item) => item.status === "failed" && item.train_code).map((item) => item.train_code);
      if (failed.length) {
        retry = el("button", "routing-batch", `${UI_COPY.batch.retry} ${failed.join("、")}`); retry.type = "button";
        retry.addEventListener("click", () => sendStructuredAction(
          { kind: "train_schedule_batch", trains: failed, date: items.find((item) => item.date)?.date || null },
          `重试 ${failed.join("、")} 的时刻查询`,
        ));
        retainedNote = el("div", "batch-retain", UI_COPY.batch.retained);
      }
      container.appendChild(card);
      if (retry) container.appendChild(retry);
      if (retainedNote) container.appendChild(retainedNote);
      continue;
    }
    if (result.kind === "emu_routing") {
      const routeCard = el("section", "schedule-card routing-card");
      const routeHead = el("div", "schedule-head");
      routeHead.appendChild(el("strong", "schedule-code", result.query || UI_COPY.routing.title));
      routeHead.appendChild(el("span", "schedule-example", ""));
      routeCard.appendChild(routeHead);
      if (result.focus_date) routeCard.appendChild(el("div", "routing-date", `${result.focus_date.slice(5).replace("-", "月")}日 · ${UI_COPY.routing.label}`));
      const timeNote = el("div", "schedule-note"); timeNote.append(icon("clock"), document.createTextNode(result.time_semantics || UI_COPY.routing.timeSemantics));
      routeCard.appendChild(timeNote);
      const table = document.createElement("table");
      const thead = document.createElement("thead"); const header = document.createElement("tr");
      UI_COPY.routing.columns.forEach((label) => header.appendChild(el("th", "", label)));
      thead.appendChild(header); table.appendChild(thead);
      const body = document.createElement("tbody");
      for (const record of result.records || []) {
        const row = document.createElement("tr");
        const codeCell = document.createElement("td");
        const jump = document.createElement("button"); jump.type = "button"; jump.className = "routing-jump";
        jump.textContent = record.train_code || "—";
        codeCell.appendChild(jump); row.appendChild(codeCell);
        const timeCell = el("td", "routing-record-cell");
        const time = el("strong", "routing-record-time", `${record.time || "—"}${record.date && record.date !== result.focus_date ? ` · ${record.date}` : ""}`);
        const lookup = el("button", "routing-record-link", "查看该车次时刻");
        lookup.type = "button";
        lookup.appendChild(icon("chevron-right"));
        const openSchedule = () => sendStructuredAction(
          { kind: "train_schedule_batch", trains: [record.train_code], date: result.focus_date || record.date || null },
          `查询 ${record.train_code} 的时刻表`,
        );
        jump.addEventListener("click", openSchedule);
        lookup.addEventListener("click", openSchedule);
        timeCell.append(time, lookup); row.appendChild(timeCell);
        body.appendChild(row);
      }
      table.appendChild(body); const wrap = el("div", "schedule-table-wrap"); wrap.appendChild(table); routeCard.appendChild(wrap);
      const sourceRow = el("div", "schedule-source");
      const link = document.createElement("a"); link.href = (result.sources || []).find((url) => url.includes("rail.re")) || "https://rail.re"; link.target = "_blank";
      link.rel = "noopener noreferrer"; link.append(icon("link"), document.createTextNode(`rail.re · ${UI_COPY.routing.source}`)); sourceRow.appendChild(link);
      const detailsEl = appendRequestDetails(result, requestMeta, false);
      const detailsButton = el("button", "schedule-detail-label routing-details-button", "");
      detailsButton.type = "button"; detailsButton.setAttribute("aria-label", UI_COPY.schedule.details);
      detailsButton.setAttribute("aria-expanded", "false"); detailsButton.appendChild(icon("chevron-right"));
      detailsButton.addEventListener("click", () => {
        detailsEl.open = !detailsEl.open;
        detailsButton.setAttribute("aria-expanded", String(detailsEl.open));
      });
      sourceRow.appendChild(detailsButton);
      routeCard.appendChild(sourceRow);
      const codes = (result.records || []).map((record) => record.train_code).filter(Boolean);
      const batch = el("button", "routing-batch", `${UI_COPY.routing.batchPrefix}${toChineseCount(codes.length)}${UI_COPY.routing.batchSuffix}`);
      batch.type = "button";
      batch.addEventListener("click", () => sendStructuredAction(
        { kind: "train_schedule_batch", trains: codes, date: result.focus_date || null },
          `查询 ${codes.length} 趟车的时刻表`,
      ));
      container.appendChild(routeCard);
      container.appendChild(detailsEl);
      container.appendChild(batch);
      container.appendChild(el("div", "routing-integrity-note", UI_COPY.routing.integrityNote));
      continue;
    }
    if (result.kind !== "train_schedule") continue;
    const card = el("section", "schedule-card");
    const head = el("div", "schedule-head");
    head.appendChild(el("strong", "schedule-code", result.train_code || "列车时刻"));
    const scheduleLabel = String(result.schedule_type || "").includes("图定") ? result.schedule_type : "";
    head.appendChild(el("span", "schedule-example", scheduleLabel ? `${scheduleLabel}${result.sample_data ? ` · ${UI_COPY.schedule.sampleData}` : ""}` : ""));
    card.appendChild(head);
    card.appendChild(el("div", "schedule-route",
      `${result.from_station || "—"} → ${result.to_station || "—"}`));
    const meta = el("div", "schedule-meta");
    if (result.date) { const date = el("span", ""); date.append(icon("calendar"), document.createTextNode(result.date.slice(5).replace("-", "月") + "日")); meta.appendChild(date); }
    if (result.duration) { const duration = el("span", ""); duration.append(icon("clock"), document.createTextNode(result.duration)); meta.appendChild(duration); }
    if (meta.childNodes.length) card.appendChild(meta);
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const header = document.createElement("tr");
    UI_COPY.schedule.columns.forEach((label) => header.appendChild(el("th", "", label)));
    thead.appendChild(header); table.appendChild(thead);
    const body = document.createElement("tbody");
    const stops = result.stops || [];
    for (const [stopIndex, stop] of stops.entries()) {
      const row = document.createElement("tr");
      row.className = "schedule-stop-row";
      const station = stop.station || stop.station_name || stop.name || "—";
      const stationCell = el("td", "schedule-station-cell");
      const line = el("span", "schedule-axis-line");
      line.classList.toggle("first", stopIndex === 0);
      line.classList.toggle("last", stopIndex === stops.length - 1);
      stationCell.appendChild(line);
      stationCell.appendChild(el("span", "schedule-axis-dot"));
      const stationText = el("div", "schedule-station-text");
      stationText.appendChild(el("strong", "schedule-station-name", station));
      if (stop.stopover_time) stationText.appendChild(el("span", "schedule-stopover", stop.stopover_time));
      stationCell.appendChild(stationText);
      row.appendChild(stationCell);
      row.appendChild(el("td", "", String(stop.arrive_time || "--")));
      row.appendChild(el("td", "", String(stop.start_time || "--")));
      body.appendChild(row);
    }
    table.appendChild(body);
    const axis = el("div", "schedule-table-wrap"); axis.appendChild(table); card.appendChild(axis);
    const note = el("div", "schedule-note"); note.append(icon("info"), document.createTextNode(UI_COPY.schedule.note)); card.appendChild(note);
    const sourceRow = el("div", "schedule-source");
    const sourceUrl = (result.sources || [])[0];
    if (sourceUrl) {
      const link = document.createElement("a"); link.href = sourceUrl; link.target = "_blank";
      link.rel = "noopener noreferrer"; link.append(icon("link"), document.createTextNode(`${displaySourceLabel(result)} · ${UI_COPY.schedule.source}`));
      sourceRow.appendChild(link);
    }
    const detailsEl = appendRequestDetails(result, requestMeta, false);
    const detailsButton = el("button", "schedule-detail-label", UI_COPY.schedule.details);
    detailsButton.type = "button";
    detailsButton.setAttribute("aria-expanded", "false");
    detailsButton.addEventListener("click", () => {
      detailsEl.open = !detailsEl.open;
      detailsButton.setAttribute("aria-expanded", String(detailsEl.open));
    });
    sourceRow.appendChild(detailsButton);
    card.appendChild(sourceRow);
    container.appendChild(card);
    container.appendChild(detailsEl);
  }
}

function appendRequestDetails(result, requestMeta, hideSummary = false) {
  const detailsEl = document.createElement("details"); detailsEl.className = "request-details";
  if (hideSummary) {
    const summary = el("summary", "visually-hidden", UI_COPY.schedule.details);
    detailsEl.appendChild(summary);
  } else {
    detailsEl.appendChild(el("summary", "", UI_COPY.schedule.details));
  }
  const body = el("div", "request-details-body");
  const sourceLabel = result.kind === "emu_routing" ? "rail.re · 交路记录" : `${displaySourceLabel(result)} · 列车时刻`;
  const sourceRow = el("div", "request-detail-row request-source-row");
  const sourceIcon = el("span", "request-source-icon"); sourceIcon.appendChild(icon("link"));
  sourceRow.append(sourceIcon, el("strong", "", sourceLabel));
  if (result.sample_data) sourceRow.appendChild(el("span", "request-sample-badge", UI_COPY.details.sample));
  body.appendChild(sourceRow);
  const addRow = (label, value) => {
    if (value == null || value === "") return;
    const row = el("div", "request-detail-row"); row.appendChild(el("span", "", label)); row.appendChild(el("strong", "", String(value))); body.appendChild(row);
  };
  const rawDate = result.date || result.focus_date;
  addRow(UI_COPY.details.date, rawDate ? String(rawDate).replace(/^(\d{4})-(\d{2})-(\d{2})$/, "$2月$3日") : rawDate);
  addRow(UI_COPY.details.timeBasis, result.schedule_type || result.time_semantics);
  if (requestMeta.latencyMs != null) addRow(UI_COPY.details.latency, `${(Number(requestMeta.latencyMs) / 1000).toFixed(1)} 秒`);
  if (requestMeta.usage?.total_tokens != null) addRow(UI_COPY.details.usage, `${Number(requestMeta.usage.total_tokens).toLocaleString()} Token`);
  const logs = requestMeta.processLogs || [];
  detailsEl.appendChild(body);
  if (logs.length) {
    const tech = document.createElement("details"); tech.className = "request-tech";
    tech.appendChild(el("summary", "", UI_COPY.details.technicalLog));
    const pre = el("pre", "", logs.join("\n")); tech.appendChild(pre); detailsEl.appendChild(tech);
  }
  return detailsEl;
}

function displaySourceLabel(result) {
  return (result.sources || []).some((url) => String(url).includes("github.com/wensimehrp"))
    ? "GTFS" : "12306";
}

function toChineseCount(number) {
  return ({ 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十" })[number]
    || String(number);
}

function followupQuestions(meta, legacyTable = false) {
  if (legacyTable) {
    return [
      { label: UI_COPY.reading.availability, text: "查一下这趟车的余票", icon: "search" },
      { label: UI_COPY.reading.changeDate, text: "换个日期查询这趟车的时刻", icon: "calendar" },
    ];
  }
  const label = String(meta.intent || "");
  if (label.includes("票价")) {
    return [{ label: "再查余票", text: "再查一下余票" }, { label: "查看时刻表", text: "再看一下这趟车的时刻表" }];
  }
  if (label.includes("交路") || label.includes("担当")) {
    return [{ label: "查看时刻表", text: "查看这些车次的时刻表" }, { label: "再查余票", text: "再查一下余票" }];
  }
  if (label.includes("时刻")) {
    return [{ label: "查票价", text: "查一下这趟车的票价" }, { label: "查担当车组", text: "查一下这趟车的担当车组" }];
  }
  return [];
}

function intentLabel(raw) {
  const value = String(raw || "");
  const translated = value.match(/^[a-z_]+（(.+)）$/);
  return translated ? translated[1] : value;
}

function renderIntentLine(refs, meta) {
  refs.intent.textContent = intentLabel(meta.intent);
  if (meta.questionType && meta.questionType !== "realtime") {
    refs.intent.appendChild(el("span", "qtype " + meta.questionType,
      meta.questionType === "knowledge" ? "知识型" : "混合型"));
  }
}

function renderSources(container, sources) {
  container.replaceChildren();
  if (!Array.isArray(sources) || !sources.length) return;
  const wrap = el("div", "sources");
  wrap.appendChild(el("span", "sources-label", "数据来源"));
  sources.forEach((raw, index) => {
    let url;
    try { url = new URL(String(raw)); } catch { return; }
    if (url.protocol !== "https:" && url.protocol !== "http:") return;
    const link = el("a", null, url.hostname.replace(/^www\./, "") + (sources.length > 1 ? ` ${index + 1}` : ""));
    link.href = url.href;
    link.title = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    wrap.appendChild(link);
  });
  if (wrap.querySelector("a")) container.appendChild(wrap);
}

function formatStats(meta) {
  const u = meta.usage || {};
  const secs = ((meta.latencyMs != null ? meta.latencyMs : 0) / 1000).toFixed(1);
  const total = u.total_tokens || 0;
  // 输入/输出分开显示：只有总数时，用户没法判断"慢"是慢在读长上下文还是慢在生成。
  // 设备端这两个数字的差距非常悬殊（prefill ~26 tok/s vs decode ~15 tok/s，
  // 而且提示词动辄上千 token），少了分解就看不出瓶颈在哪。
  const split = (u.prompt_tokens || u.completion_tokens)
    ? `（输入 ${(u.prompt_tokens || 0).toLocaleString()} / 输出 ${(u.completion_tokens || 0).toLocaleString()}）`
    : "";
  return "本次用量：" + total.toLocaleString() + " Token" + split + " · 耗时 " + secs + " 秒";
}

function emptyState() {
  const d = el("div", "empty");
  const mark = el("div", "empty-mark");
  mark.appendChild(icon("train"));
  d.appendChild(mark);
  d.appendChild(el("h2", null, "想查哪趟列车？"));
  d.appendChild(el("p", null, "查询 12306 与 rail.re 数据，帮你看票价、时刻和车组。"));
  const quick = el("div", "quick-grid");
  for (const [iconName, label, question] of [
    ["ticket", "查票价", "明天北京南到上海虹桥的 G1 票价"],
    ["clock", "查时刻", "G1 今天的时刻表"],
    ["route", "查交路", "CR400AF-5033 今天的交路"],
    ["camera", "找机位", "我想找一个拍 CR400AF 的机位"],
  ]) {
    const b = el("button", "quick-item");
    b.type = "button";
    b.appendChild(icon(iconName, "quick-icon"));
    b.appendChild(el("span", "quick-label", label));
    b.addEventListener("click", () => { inputEl.value = question; autoGrow(); inputEl.focus(); });
    quick.appendChild(b);
  }
  d.appendChild(quick);
  d.appendChild(el("p", "empty-section", "试试这样问"));
  const examples = el("div", "empty-examples");
  for (const question of ["明天 G1 北京南到上海虹桥多少钱？", "G8932 今天会经过哪些站？"]) {
    const b = el("button", "ex", question);
    b.type = "button";
    b.addEventListener("click", () => { inputEl.value = question; autoGrow(); inputEl.focus(); });
    examples.appendChild(b);
  }
  d.appendChild(examples);
  d.appendChild(el("p", "empty-tip", "点选后可先编辑，再发送。结果会附数据来源。"));
  return d;
}

/** 重绘当前对话。 */
/** 在对话区插入一条可操作的错误提示（带「去配置」）。 */
function showChatError(message) {
  chatEl.innerHTML = "";
  const box = el("div", "chat-error");
  box.appendChild(el("span", null, "⚠️ " + message));
  const go = el("button", "btn primary", "去配置");
  go.addEventListener("click", () => navigate("#/settings"));
  box.appendChild(go);
  chatEl.appendChild(box);
  scrollBottom();
}

function renderChat() {
  state.autoFollow = true;
  state.pendingBottom = false;
  state.readingAnchor = null;
  const cue = document.getElementById("new-content-cue"); if (cue) cue.hidden = true;
  const conv = store.get(state.convId) || store.current();
  state.convId = conv.id;
  chatEl.innerHTML = "";
  refreshTotal();
  if (!conv.messages.length) {
    chatEl.appendChild(emptyState());
    updateCtxInfo([]);
    return;
  }
  conv.messages.forEach((m, i) => {
    if (m.role === "user") {
      chatEl.appendChild(renderUserRow(m, i));
    } else {
      const refs = renderAssistantRow(m, i);
      m._refs = refs;
      chatEl.appendChild(refs.row);
    }
  });
  updateCtxInfo(conv.messages);
  scrollBottom();
}

// ---------- 发送 / 流式 ----------
function setGenerating(on) {
  state.generating = on;
  sendBtn.classList.toggle("stop", on);
  const readingPaused = !state.autoFollow && !!chatEl.querySelector(".row.assistant.has-table-answer");
  const sendIcon = icon(on ? "stop" : readingPaused ? "up" : "send", "send-icon");
  sendBtn.replaceChildren(sendIcon);
  sendBtn.title = on ? "停止生成" : "发送";
  sendBtn.setAttribute("aria-label", on ? "停止生成" : "发送");
  inputEl.placeholder = UI_COPY.loading.followupPlaceholder;
}

async function send(messageOverride = null, displayAction = null) {
  if (state.generating) { stopGeneration(); return; }
  const text = String(messageOverride == null ? inputEl.value : messageOverride).trim();
  if (!text) return;

  // 用户主动关掉了引导条、却仍未配置任何 Key：不要在对话里默默失败，
  // 直接给一条可操作的错误（并保留「去配置」入口）。
  if (!llmConfigured()) {
    showChatError("尚未配置模型 API，无法生成回答。请先在「⚙️ 设置」里选择供应商并填入 API Key。");
    return;
  }

  if (messageOverride == null) { inputEl.value = ""; autoGrow(); }

  const conv = store.current();
  state.convId = conv.id;
  if (!conv.messages.length) chatEl.innerHTML = "";

  const stored = store.addMessage(conv.id, { role: "user", content: text });
  const userIndex = conv.messages.length - 1;
  chatEl.appendChild(renderUserRow(stored, userIndex));
  updateCtxInfo(conv.messages);
  updateHeaderTitle();
  renderConvList();

  await runAssistant(conv.id, userIndex, displayAction);
}

function sendStructuredAction(action, message) {
  return send(message, action);
}

/** 为第 userIndex 条用户消息生成回答（history 取其之前的所有消息）。 */
async function runAssistant(convId, userIndex, displayAction = null) {
  const conv = store.get(convId);
  if (!conv) return;
  const userText = conv.messages[userIndex].content;
  const history = buildHistory(conv.messages, userIndex);

  // streaming 这个标记是"这条回答还没写完"的落盘证据：应用在生成途中被系统回收时，
  // 进程里涨到一半的正文会丢，本地留下的就是这个标记 + 已经落盘的那部分内容。
  // 下次打开据此如实提示"被打断了"，而不是让用户对着一个空气泡猜发生了什么。
  const assistant = { role: "assistant", content: "", meta: { streaming: true } };
  store.addMessage(convId, assistant);
  const aIndex = conv.messages.length - 1;
  // 登记"这一条正在生成"。渲染时靠它区分"活着"与"上次被打断"——
  // 只看 meta.streaming 是不行的：生成开始时那条消息的标记本来就是 true，
  // 于是每生成一次都会立刻挂上"被系统中断"的提示（用户实测就是这个现象）。
  state.live = { convId, index: aIndex };
  const refs = renderAssistantRow(assistant, aIndex);
  assistant._refs = refs;
  chatEl.appendChild(refs.row);
  refs.progress.textContent = "正在准备…";
  refs.progress.hidden = true;
  refs.loadingCard.hidden = false;
  scrollBottom();

  setGenerating(true);
  const controller = new AbortController();
  state.controller = controller;

  const stageOrder = { intent: "意图分类", extract: "信息抽取", retrieve: "数据检索" };
  let answerRaw = "";
  let thinkRaw = "";
  let finished = false;
  const persist = () => store.updateMessage(convId, aIndex, { content: answerRaw, meta: assistant.meta });
  // 流式途中**定期落盘**。不这么做的话，进程在生成到一半时被系统回收（本应用刻意不用
  // 前台服务，切到后台久了就会被杀），那条回答在本地就是空的 —— 正文原先只在结束时写一次。
  // 节流到约 1.5 秒：每来一个 delta 就序列化整份状态没必要，代价也不小。
  let persistedAt = 0;
  const persistThrottled = () => {
    const now = Date.now();
    if (now - persistedAt < 1500) return;
    persistedAt = now;
    if (thinkRaw) assistant.meta = { ...assistant.meta, thinking: thinkRaw };
    persist();
  };

  // 流式正文渲染节流。原先每个 delta 都 `renderMarkdown(整篇) + innerHTML`：
  // 一次回答上百个 delta 就把整篇 Markdown 重新解析、整棵 DOM 重新排版上百次，
  // 长回答（逐站列表、表格）在移动 WebView 上表现为滚动卡顿、键盘跟随迟滞。
  // 现在 delta 只累加到字符串，渲染合并成"每帧最多一次、且间隔 ≥ 50 ms"，
  // 结束时的收尾渲染仍是一次完整渲染（见 finally）。
  const renderAnswer = rafThrottle(() => {
    refs.ans.innerHTML = renderMarkdown(answerRaw) + '<span class="caret"></span>';
    if (!state.autoFollow) {
      restoreReadingAnchor();
      state.pendingBottom = true;
      const cue = document.getElementById("new-content-cue");
      if (cue) cue.hidden = false;
    } else scrollBottom();
  }, 50);

  try {
    const resp = await fetch(API_BASE + "/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: userText, history,
        session_id: convId,
        ...(displayAction ? { display_action: displayAction } : {}),
        // 供应商覆盖（BYOK）：未选择时为空对象，服务端用自身配置
        ...llmSpec(),
      }),
      signal: controller.signal,
    });
    if (!resp.ok || !resp.body) {
      let detail = "";
      try {
        const j = await resp.json();
        const d = j && j.detail;
        if (d && typeof d === "object" && !Array.isArray(d)) detail = d.message || d.code || "";
        else if (Array.isArray(d) && d.length) {
          detail = String((d[0] && d[0].msg) || "").replace(/^Value error,\s*/, "");
        }
      } catch { /* 非 JSON 响应 */ }
      throw new Error(detail ? `请求失败（HTTP ${resp.status}）：${detail}` : `请求失败：${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const rawEvent = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = rawEvent.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }

        switch (ev.type) {
          case "stage": {
            const name = stageOrder[ev.stage] || ev.stage;
            refs.progress.textContent = "● 正在[" + name + "]…";
            if (ev.stage === "intent") {
              const railwayQuery = /schedule|emu_routing|列车时刻|车组交路/i.test(String(ev.msg || ""));
              refs.loadingCard.hidden = !railwayQuery;
              refs.progress.hidden = railwayQuery;
              refs.recognized = String(ev.recognized || "").trim();
              refs.loadingStage.textContent = refs.recognized
                ? `${UI_COPY.loading.recognized} ${refs.recognized} · ${UI_COPY.loading.stage}`
                : UI_COPY.loading.stage;
            } else if (ev.stage === "retrieve" && !refs.loadingCard.hidden) {
              const trace = String(ev.msg || "");
              const sourceText = trace.includes("emu.routing") ? "正在连接 rail.re"
                : trace.includes("train.schedule") ? "正在连接 12306" : UI_COPY.loading.stage;
              refs.loadingStage.textContent = refs.recognized
                ? `${UI_COPY.loading.recognized} ${refs.recognized} · ${sourceText}` : sourceText;
            }
            refs.logBody.appendChild(el("div", "log-line", "· [" + name + "] " + ev.ms + "ms"));
            if (ev.stage === "intent" && ev.msg) {
              const parts = String(ev.msg).split(" · ");
              assistant.meta = {
                ...assistant.meta,
                intent: parts[0],
                questionType: (parts[1] || "realtime").trim(),
              };
              renderIntentLine(refs, assistant.meta);
            }
            break;
          }
          case "think":
            thinkRaw += ev.delta || "";
            refs.thinkPre.textContent = thinkRaw;
            if (thinkRaw) refs.thinkDetails.classList.remove("hidden");
            break;
          case "answer":
            answerRaw += ev.delta || "";
            assistant.content = answerRaw;
            renderAnswer();
            persistThrottled();
            break;
          case "replace":
            // 服务端在**生成中途**发现后文是连续重复（模型退化），把已吐出去的那段收回，
            // 整体替换成截断后的版本。没有这个事件，用户会看到一屏复读内容。
            answerRaw = ev.text || "";
            assistant.content = answerRaw;
            renderAnswer();
            persistThrottled();
            break;
          case "billing":            // M11.3 起下发：先记录，暂不展示
            assistant.meta = { ...assistant.meta, billing: ev };
            break;
          case "done": {
            finished = true;
            refs.progress.textContent = "";
            refs.progress.hidden = false;
            refs.loadingCard.hidden = true;
            assistant.meta = {
              ...assistant.meta,
              intent: ev.intent,
              questionType: ev.question_type || "realtime",
              usage: ev.usage || {},
              latencyMs: ev.latency_ms,
              thinking: ev.thinking || thinkRaw,
              sources: ev.sources || [],
              displayResults: normalizeDisplayResults(ev.display_results),
              processLogs: ev.process_logs || [],
              // 模型是否因长度上限停止：必须在气泡里如实提示，
              // 否则用户会以为回答本来就写到这儿（真实反馈过）
              truncated: !!ev.truncated,
              truncateReason: ev.truncate_reason || null,
            };
            renderTrainScheduleCards(refs.structuredBox, assistant.meta.displayResults, assistant.meta);
            syncStructuredPresentation(refs, assistant.meta);
            renderIntentLine(refs, assistant.meta);
            refs.stats.textContent = formatStats(assistant.meta);
            if (assistant.meta.thinking) {
              refs.thinkPre.textContent = assistant.meta.thinking;
              refs.thinkDetails.classList.remove("hidden");
            }
            if (assistant.meta.processLogs.length) {
              refs.logBody.innerHTML = "";
              for (const l of assistant.meta.processLogs) {
                refs.logBody.appendChild(el("div", "log-line", "· " + l));
              }
            }
            if (assistant.meta.questionType === "knowledge" || assistant.meta.questionType === "mixed") {
              refs.hintBox.appendChild(el("div", "knowledge-hint",
                "本题含知识型内容，部分信息可能未经过检索，请以官方资料为准。"));
            }
            if (assistant.meta.truncated) {
              // 按**原因**给不同指引：复读调大输出上限只会让它重复更久，方向是反的。
              refs.bubble.appendChild(el("div", "truncate-note",
                assistant.meta.truncateReason === "repetition"
                  ? "⚠️ 模型输出出现连续重复，已在重复开始处截断。请缩小问题范围后重试。"
                  : "⚠️ 回答因输出长度上限被截断（内容不完整）。可在「⚙️ 设置 → 编辑」里调大"
                    + "「最大输出」，或让问题更聚焦后重问。"));
            }
            renderSources(refs.sourcesBox, assistant.meta.sources);
            break;
          }
          case "error":
            assistant.meta = { ...assistant.meta, error: sanitizeConnectionError(ev.message || "生成失败") };
            refs.progress.textContent = "";
            refs.progress.hidden = false;
            refs.loadingCard.hidden = true;
            if (!refs.errorBox) {
              refs.errorBox = buildConnectionError(assistant.meta.error,
                () => runAssistant(convId, userIndex, displayAction));
              refs.bubble.appendChild(refs.errorBox);
            }
            syncStructuredPresentation(refs, assistant.meta);
            break;
          default:
            break;
        }
      }
    }
  } catch (e) {
    if (e && e.name === "AbortError") {
      assistant.meta = { ...assistant.meta, stopped: true };
      renderIntentLine(refs, assistant.meta);
      refs.ans.innerHTML = renderMarkdown(answerRaw) +
        (answerRaw ? "" : "<em>（未产生内容）</em>");
      refs.progress.textContent = "";
      refs.progress.hidden = false;
      refs.loadingCard.hidden = true;
      if (answerRaw) refs.bubble.appendChild(el("div", "stopped-tag", "⏹ 已停止生成（内容可能不完整）"));
      toast("已停止生成");
    } else {
      assistant.meta = { ...assistant.meta, error: sanitizeConnectionError((e && e.message) || "网络/服务错误") };
      renderIntentLine(refs, assistant.meta);
      refs.progress.textContent = "";
      refs.progress.hidden = false;
      refs.loadingCard.hidden = true;
      refs.bubble.appendChild(buildConnectionError(assistant.meta.error,
        () => runAssistant(convId, userIndex, displayAction)));
      syncStructuredPresentation(refs, assistant.meta);
    }
  } finally {
    // 先取消可能还挂着的节流渲染：否则它会在收尾渲染之后重画一次，
    // 把已经摘掉的 caret 又贴回去（表现为回答写完了光标还在闪）。
    renderAnswer.cancel();
    if (!finished && !assistant.meta.stopped && !assistant.meta.error) {
      assistant.meta = { ...assistant.meta, error: sanitizeConnectionError("连接中断，回答可能不完整") };
      refs.bubble.appendChild(buildConnectionError("连接中断，回答可能不完整",
        () => runAssistant(convId, userIndex, displayAction)));
    }
    // 收尾做一次**完整**渲染（流式期间渲染是节流的，这里必须补上不带 caret 的最终版本）。
    // ⚠️ 判据是 `answerRaw`（有没有正文），**不能**是"DOM 里有没有 caret"：
    //    渲染被节流到下一帧，而一次很快的回答（mock / 命中缓存 / 短问短答）可能在第一帧
    //    之前就结束 —— 那时 caret 根本没来得及画出来，按 caret 判断就会**整段回答不显示**。
    //    实测踩过：模拟器 LLM_MOCK 下一次回答只剩意图与流程日志，正文空白（1.2ms 就流完了）。
    //    （caret 一旦存在就必然有正文，所以改判据不会放过"流到一半中断"的情况。）
    if (answerRaw) {
      refs.ans.innerHTML = renderMarkdown(answerRaw);
    } else if (assistant.meta.stopped) {
      refs.ans.innerHTML = "<em>（未产生内容）</em>";
    }
    refs.loadingCard.hidden = true;
    state.controller = null;
    setGenerating(false);
    // 先摘掉"进行中"标记再落盘：万一正好死在这两步之间，下次会显示成"被打断"
    // —— 偏保守，但不会骗人。
    delete assistant.meta.streaming;
    state.live = null;             // 生成结束：这条不再是「活的」
    persist();                     // 流式结束后一次性落盘（含 meta）
    refreshTotal();
    updateCtxInfo(conv.messages);
    updateHeaderTitle();
    renderConvList();
    restoreReadingAnchor();
    scrollBottom();
    // 完成后**不要**自动聚焦输入框：在手机上这会立刻弹出软键盘，把刚生成的回答
    // 顶走半屏（用户实测反馈）。桌面端有实体键盘，聚焦一下能接着打下一句，仍然保留。
    // 判据用 (hover: hover) 而不是"是不是 Android"：真正决定要不要弹键盘的是
    // 有没有指针设备，不是平台。
    if (!window.matchMedia || window.matchMedia("(hover: hover)").matches) inputEl.focus();
  }
}

function stopGeneration() { if (state.controller) state.controller.abort(); }

/**
 * 重算底部的「累计 Token」。
 *
 * **不是累加，是重算** —— 它等于当前对话里所有回答的 usage 之和。
 * 累加式的旧实现有两个消不掉的毛病：新开对话不归零；删消息/编辑重发后只增不减。
 * 重算天然自愈，所以凡是可能改变对话内容的地方（答完、切对话、删对话、编辑重发）
 * 都调它一次即可，不需要在各种分支里小心地"补加/回退"。
 */
function refreshTotal() {
  if (totalEl) totalEl.textContent = store.totalTokens().toLocaleString();
}

// ---------- 编辑 / 重新生成 ----------
function startEdit(index, row, msg) {
  if (state.generating) { toast("请先停止当前生成"); return; }
  row.classList.add("pinned");
  const original = msg.content;

  const wrap = el("div", "edit-wrap");
  const ta = document.createElement("textarea");
  ta.value = original;
  wrap.appendChild(ta);

  const acts = el("div", "edit-actions");
  const cancel = el("button", "btn", "取消");
  const ok = el("button", "btn primary", "发送");
  acts.appendChild(cancel);
  acts.appendChild(ok);
  wrap.appendChild(acts);

  const bubble = row.querySelector(".bubble");
  row.replaceChild(wrap, bubble);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);

  cancel.addEventListener("click", () => renderChat());
  ok.addEventListener("click", async () => {
    const newText = ta.value.trim();
    if (!newText) { toast("内容不能为空"); return; }
    if (newText === original) { renderChat(); return; }
    const conv = store.get(state.convId);
    store.truncate(conv.id, index);        // 丢弃该条及其后所有消息
    renderChat();
    inputEl.value = newText;
    autoGrow();
    await send();
  });
}

async function regenerate(index) {
  if (state.generating) { toast("请先停止当前生成"); return; }
  const conv = store.get(state.convId);
  const msg = conv && conv.messages[index];
  if (!msg || msg.role !== "assistant") return;
  let userIdx = index - 1;
  while (userIdx >= 0 && conv.messages[userIdx].role !== "user") userIdx--;
  if (userIdx < 0) { toast("找不到对应的提问"); return; }
  store.truncate(conv.id, userIdx + 1);
  renderChat();
  await runAssistant(conv.id, userIdx);
}

// ---------- 输入框 ----------
function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, Math.round(window.innerHeight * 0.36)) + "px";
}

// ---------- 事件绑定 ----------
sendBtn.addEventListener("click", () => send());
inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  if (e.isComposing || e.keyCode === 229) return;   // 中文输入法组合中
  if (e.shiftKey) return;
  e.preventDefault();
  if (state.generating) { toast("生成中，请先点击 ■ 停止"); return; }
  send();
});
menuBtn.addEventListener("click", () => {
  if (window.innerWidth < 768) {
    if (sidebarEl.classList.contains("open")) closeSidebar();
    else window.history.back();
    return;
  }
  sidebarEl.classList.contains("open") ? closeSidebar() : openSidebar();
});
scrimEl.addEventListener("click", closeSidebar);
document.getElementById("newchat").addEventListener("click", newConv);
// 侧栏原来的「ℹ️ 关于」按钮已移除（与顶栏「⚙️ 设置」入口重复），
// 关于内容并入设置页；对应的监听器也一并删除 —— 元素没了还去 addEventListener
// 会直接抛 TypeError，而它会把启动期的错误横幅整个点亮。
if (llmBtn) llmBtn.addEventListener("click", () => { navigate("#/settings"); });
if (historyBtn) historyBtn.addEventListener("click", () => navigate("#/history"));
if (newChatTopBtn) newChatTopBtn.addEventListener("click", newConv);
if (convSearchEl) convSearchEl.addEventListener("input", renderConvList);
chatEl.addEventListener("scroll", () => {
  if (chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight > 48) {
    state.autoFollow = false;
    rememberReadingAnchor();
    if (!state.generating && chatEl.querySelector(".row.assistant.has-table-answer")) sendBtn.querySelector(".send-icon").src = "assets/icons/up.svg";
  }
}, { passive: true });
const bottomCue = document.getElementById("new-content-cue");
if (bottomCue) bottomCue.replaceChildren(icon("down"), document.createTextNode(UI_COPY.reading.bottomCue));
if (bottomCue) bottomCue.addEventListener("click", () => {
  state.autoFollow = true; state.pendingBottom = false; state.readingAnchor = null; bottomCue.hidden = true;
  if (!state.generating) sendBtn.querySelector(".send-icon").src = "assets/icons/send.svg";
  scrollBottom(true);
});

// 桌面快捷键：Ctrl/Cmd+K 新对话，ESC 停止或收起侧栏
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (sidebarEl.classList.contains("open")) { closeSidebar(); return; }
    if (state.generating) stopGeneration();
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    newConv();
  }
});

// 离开页面/切后台前落盘（流式中途也能保住）
window.addEventListener("beforeunload", () => { store.save(); });
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") { store.save(); return; }
  renderLlmNotice();
});
// 视口变化：移动端旋转/宽度变化时收起抽屉
window.addEventListener("resize", () => {
  if (window.innerWidth >= 1024) closeSidebar();
  autoGrow();
});

// ---------- 初始化 ----------
store.load();
// 外观：应用存下来的模式，并在系统切换深浅色时实时跟随（默认模式是"跟随系统"）。
// 首屏那一下由 index.html 里的内联脚本负责（模块脚本是 defer 的，来不及防闪烁）。
store.initTheme();
// 版本是异步取的（Android 读 build.json、桌面读 /api/version）。
// 拿到后刷新角标；如果用户正停在设置/文档页，顺手重渲染一次把文字更新掉。
loadAppVersion().then(() => {
  if (verBadge) verBadge.textContent = versionLabel();
  if (/^#\/(settings|doc\/)/.test(location.hash)) handleRoute();
});
state.convId = store.currentId;
refreshTotal();
renderConvList();
updateHeaderTitle();
renderChat();
handleRoute();
autoGrow();
if (window.innerWidth >= 1024) inputEl.focus();
// 供应商列表与顶栏徽标（异步，不阻塞首屏；失败时静默退回"服务端默认"）
void loadProviders();
