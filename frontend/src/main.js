// RailFanAI v0.5 · M11 —— 前端入口（多对话 + 个人中心 + 移动优先自适应）
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
import { renderDocPage, renderSettingsPage, APP_VERSION, ROOT_KEY_ID } from "./pages.js";

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
  providers: [],      // 服务端返回的供应商列表（用于顶栏显示名字）
  llmReady: false,
};

// ---------- LLM 供应商（BYOK）----------
/** 组装随请求下发的供应商覆盖；未做任何选择时返回空对象（用服务端配置）。 */
function llmSpec() {
  const cfg = store.llm();
  const id = cfg.provider || "";
  const spec = {};
  if (id === "custom") {
    if (cfg.base_url) spec.base_url = cfg.base_url;
  } else if (id) {
    spec.provider = id;
  }
  if (cfg.model) spec.model = cfg.model;
  if (cfg.api) spec.api = cfg.api;
  const key = store.llmKey(id || ROOT_KEY_ID);
  if (key) spec.api_key = key;
  // 选了「自定义」却没填地址 → 退回服务端默认，避免发一个必然失败的请求
  if (spec.base_url === undefined && id === "custom" && !spec.provider) delete spec.base_url;
  return spec;
}

/** 顶栏徽标：显示当前实际生效的供应商，避免"以为在用 A 其实在跑 B"。 */
async function refreshProviderBadge() {
  if (!llmBtn) return;
  const cfg = store.llm();
  let text = "模型";
  let title = "模型供应商设置";
  if (cfg.provider === "custom" && cfg.base_url) {
    text = cfg.model || "自定义";
    title = `自定义供应商：${cfg.base_url}${cfg.model ? " · " + cfg.model : ""}`;
  } else if (cfg.provider) {
    const p = state.providers.find((x) => x.id === cfg.provider);
    text = cfg.model || (p ? p.label : cfg.provider);
    title = `供应商：${p ? p.label : cfg.provider}${cfg.model ? " · " + cfg.model : ""}`;
  } else {
    const p = state.providers.find((x) => x.id === state.serverActive);
    text = p ? p.label : "服务端默认";
    title = `使用服务端配置${p ? "：" + p.label : ""}`;
  }
  const txtEl = llmBtn.querySelector(".txt");
  if (txtEl) txtEl.textContent = " " + text;
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
  } catch {
    /* 服务端不可达时保持空列表，顶栏退回"服务端默认" */
  }
  await refreshProviderBadge();
  updateCtxInfo(null);   // 参数为空时自行取当前对话消息
}

// ---------- 小工具 ----------
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function renderMarkdown(text) {
  let h = esc(text);
  h = h.replace(/```([\s\S]*?)```/g, (_m, code) => '<pre class="code">' + code + "</pre>");
  h = h.replace(/(<pre[\s\S]*?<\/pre>)/g, "\u0000$1\u0000");
  h = h.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  h = h.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>");
  h = h.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
  h = h.replace(/^#\s+(.+)$/gm, "<h3>$1</h3>");
  h = h.replace(/^\s*[-*]\s+(.+)$/gm, "•  $1");
  h = h.replace(/\n/g, "<br>");
  h = h.replace(/\u0000/g, "");
  return h;
}
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
function toast(msg) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 1800);
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
function scrollBottom() { chatEl.scrollTop = chatEl.scrollHeight; }
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
  const mConv = h.match(/^#\/c\/([\w-]+)/);
  const mDoc = h.match(/^#\/doc\/(\w+)/);
  if (mDoc) {
    showView("page");
    pageBody.innerHTML = "";
    pageBody.appendChild(renderDocPage(mDoc[1], {
      onBack: () => navigate("#/c/" + state.convId),
      navigate,
    }));
    pageBody.parentElement.scrollTop = 0;
    return;
  }
  if (/^#\/settings/.test(h)) {
    showView("page");
    pageBody.innerHTML = "";
    pageBody.appendChild(renderSettingsPage({
      onBack: () => navigate("#/c/" + state.convId),
      navigate,
    }));
    pageBody.parentElement.scrollTop = 0;
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
    return;
  }
  // 默认：当前对话
  state.convId = store.currentId || store.create().id;
  showView("chat");
  navigate("#/c/" + state.convId, true);
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
  b.title = title || label;
  b.addEventListener("click", onClick);
  return b;
}

function renderUserRow(msg, index) {
  const row = el("div", "row user");
  row.appendChild(el("div", "bubble", msg.content));
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

  const intent = el("div", "intent", meta.intent ? "【意图】" + meta.intent : "");
  if (meta.questionType && meta.questionType !== "realtime") {
    intent.appendChild(el("span", "qtype " + meta.questionType,
      meta.questionType === "knowledge" ? "知识型" : "混合型"));
  }
  bubble.appendChild(intent);

  const progress = el("div", "progress", "");
  bubble.appendChild(progress);

  const ans = el("div", "md");
  ans.innerHTML = renderMarkdown(msg.content || "");
  bubble.appendChild(ans);

  const stats = el("div", "stats", "");
  if (meta.usage || meta.latencyMs != null) stats.textContent = formatStats(meta);
  bubble.appendChild(stats);

  if (meta.questionType === "knowledge" || meta.questionType === "mixed") {
    bubble.appendChild(el("div", "knowledge-hint",
      "ℹ️ 本题含知识型内容，可能包含未经检索确认的模型知识，请以官方资料为准"));
  }
  if (meta.stopped) bubble.appendChild(el("div", "stopped-tag", "⏹ 已停止生成（内容可能不完整）"));
  if (meta.error) bubble.appendChild(el("div", "error-box", "⚠️ " + meta.error));

  const logs = details("流程日志（意图 / 抽取 / 检索 / 生成）", []);
  const logBody = logs.querySelector(".fold-body");
  if (Array.isArray(meta.processLogs)) {
    for (const l of meta.processLogs) logBody.appendChild(el("div", "log-line", "· " + l));
  }
  bubble.appendChild(logs);

  const thinkPre = el("pre", null, meta.thinking || "");
  bubble.appendChild(details("思考过程（think）", [thinkPre]));

  if (Array.isArray(meta.sources) && meta.sources.length) {
    bubble.appendChild(el("div", "sources", "数据来源：" + meta.sources.join(" · ")));
  }

  row.appendChild(bubble);

  const actions = el("div", "actions");
  actions.appendChild(actionBtn("复制", "复制回答", () => copyText(msg.content || "")));
  const regen = actionBtn("重新生成", "丢弃这条回答并重新生成", () => regenerate(index));
  regen.disabled = state.generating;
  actions.appendChild(regen);
  row.appendChild(actions);

  return { row, intent, progress, ans, stats, logBody, thinkPre, bubble };
}

function renderIntentLine(refs, meta) {
  refs.intent.textContent = meta.intent ? "【意图】" + meta.intent : "";
  if (meta.questionType && meta.questionType !== "realtime") {
    refs.intent.appendChild(el("span", "qtype " + meta.questionType,
      meta.questionType === "knowledge" ? "知识型" : "混合型"));
  }
}

function formatStats(meta) {
  const u = meta.usage || {};
  const secs = ((meta.latencyMs != null ? meta.latencyMs : 0) / 1000).toFixed(1);
  return "本次 token used：" + (u.total_tokens || 0).toLocaleString() + " · 耗时 " + secs + " 秒";
}

function emptyState() {
  const d = el("div", "empty");
  d.innerHTML =
    '输入一句铁路相关问题，我会查 12306 / rail.re 实时数据后作答。<br />' +
    '<span class="ex" data-q="G1今天由哪组动车组担当？">· G1今天由哪组动车组担当？</span>' +
    '<span class="ex" data-q="明天北京到上海的高铁余票">· 明天北京到上海的高铁余票</span>' +
    '<span class="ex" data-q="京沪线经过哪些站？">· 京沪线经过哪些站？</span>' +
    '<span class="ex" data-q="我在吉林市船营区，要拍 CR400AF，今天下午">· 我在吉林市船营区，要拍 CR400AF，今天下午</span>' +
    '<span style="display:block;margin-top:14px;font-size:12px;opacity:.7">' +
    '多轮追问（如「那明天呢」）· 生成中可随时停止 · 消息可编辑重发 · 左上角 ☰ 管理多个对话</span>';
  d.querySelectorAll(".ex").forEach((n) => {
    n.addEventListener("click", () => { inputEl.value = n.dataset.q; autoGrow(); send(); });
  });
  return d;
}

/** 重绘当前对话。 */
function renderChat() {
  const conv = store.get(state.convId) || store.current();
  state.convId = conv.id;
  chatEl.innerHTML = "";
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
  sendBtn.textContent = on ? "■" : "发送";
  sendBtn.title = on ? "停止生成" : "发送";
  inputEl.placeholder = on
    ? "正在生成…点击 ■ 可停止（仍可先输入下一条）"
    : "输入你的问题…（Enter 发送，Shift+Enter 换行）";
}

async function send() {
  if (state.generating) { stopGeneration(); return; }
  const text = inputEl.value.trim();
  if (!text) return;

  inputEl.value = "";
  autoGrow();

  const conv = store.current();
  state.convId = conv.id;
  if (!conv.messages.length) chatEl.innerHTML = "";

  const stored = store.addMessage(conv.id, { role: "user", content: text });
  const userIndex = conv.messages.length - 1;
  chatEl.appendChild(renderUserRow(stored, userIndex));
  updateCtxInfo(conv.messages);
  updateHeaderTitle();
  renderConvList();

  await runAssistant(conv.id, userIndex);
}

/** 为第 userIndex 条用户消息生成回答（history 取其之前的所有消息）。 */
async function runAssistant(convId, userIndex) {
  const conv = store.get(convId);
  if (!conv) return;
  const userText = conv.messages[userIndex].content;
  const history = buildHistory(conv.messages, userIndex);

  const assistant = { role: "assistant", content: "", meta: {} };
  store.addMessage(convId, assistant);
  const aIndex = conv.messages.length - 1;
  const refs = renderAssistantRow(assistant, aIndex);
  assistant._refs = refs;
  chatEl.appendChild(refs.row);
  refs.progress.textContent = "正在准备…";
  scrollBottom();

  setGenerating(true);
  const controller = new AbortController();
  state.controller = controller;

  const stageOrder = { intent: "意图分类", extract: "信息抽取", retrieve: "数据检索" };
  let answerRaw = "";
  let thinkRaw = "";
  let finished = false;
  const persist = () => store.updateMessage(convId, aIndex, { content: answerRaw, meta: assistant.meta });

  try {
    const resp = await fetch(API_BASE + "/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: userText, history,
        session_id: convId,
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
            break;
          case "answer":
            answerRaw += ev.delta || "";
            assistant.content = answerRaw;
            refs.ans.innerHTML = renderMarkdown(answerRaw) + '<span class="caret"></span>';
            scrollBottom();
            break;
          case "billing":            // M11.3 起下发：先记录，暂不展示
            assistant.meta = { ...assistant.meta, billing: ev };
            break;
          case "done": {
            finished = true;
            refs.progress.textContent = "";
            renderIntentLine(refs, assistant.meta);
            assistant.meta = {
              ...assistant.meta,
              intent: ev.intent,
              questionType: ev.question_type || "realtime",
              usage: ev.usage || {},
              latencyMs: ev.latency_ms,
              thinking: ev.thinking || thinkRaw,
              sources: ev.sources || [],
              processLogs: ev.process_logs || [],
            };
            refs.stats.textContent = formatStats(assistant.meta);
            if (assistant.meta.thinking) refs.thinkPre.textContent = assistant.meta.thinking;
            if (assistant.meta.processLogs.length) {
              refs.logBody.innerHTML = "";
              for (const l of assistant.meta.processLogs) {
                refs.logBody.appendChild(el("div", "log-line", "· " + l));
              }
            }
            if (assistant.meta.questionType === "knowledge" || assistant.meta.questionType === "mixed") {
              refs.bubble.appendChild(el("div", "knowledge-hint",
                "ℹ️ 本题含知识型内容，可能包含未经检索确认的模型知识，请以官方资料为准"));
            }
            if (assistant.meta.sources.length) {
              refs.bubble.appendChild(el("div", "sources", "数据来源：" + assistant.meta.sources.join(" · ")));
            }
            bumpTotal((ev.usage && ev.usage.total_tokens) || 0);
            break;
          }
          case "error":
            assistant.meta = { ...assistant.meta, error: ev.message || "生成失败" };
            refs.progress.textContent = "";
            if (!refs.errorBox) {
              refs.errorBox = el("div", "error-box", "⚠️ " + assistant.meta.error);
              refs.bubble.appendChild(refs.errorBox);
            } else {
              refs.errorBox.textContent = "⚠️ " + assistant.meta.error;
            }
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
      if (answerRaw) refs.bubble.appendChild(el("div", "stopped-tag", "⏹ 已停止生成（内容可能不完整）"));
      toast("已停止生成");
    } else {
      assistant.meta = { ...assistant.meta, error: (e && e.message) || "网络/服务错误" };
      renderIntentLine(refs, assistant.meta);
      refs.progress.textContent = "";
      refs.bubble.appendChild(el("div", "error-box", "⚠️ " + assistant.meta.error));
    }
  } finally {
    if (!finished && !assistant.meta.stopped && !assistant.meta.error) {
      assistant.meta = { ...assistant.meta, error: "连接中断，回答可能不完整" };
      refs.bubble.appendChild(el("div", "error-box", "⚠️ 连接中断，回答可能不完整"));
    }
    if (refs.ans.querySelector(".caret")) {
      refs.ans.innerHTML = renderMarkdown(assistant.content || "") ||
        (assistant.meta.stopped ? "<em>（未产生内容）</em>" : "");
    }
    state.controller = null;
    setGenerating(false);
    persist();                     // 流式结束后一次性落盘（含 meta）
    updateCtxInfo(conv.messages);
    updateHeaderTitle();
    renderConvList();
    inputEl.focus();
  }
}

function stopGeneration() { if (state.controller) state.controller.abort(); }
function bumpTotal(n) {
  const v = store.bumpTotal(n);
  if (totalEl) totalEl.textContent = v.toLocaleString();
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
sendBtn.addEventListener("click", send);
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
  sidebarEl.classList.contains("open") ? closeSidebar() : openSidebar();
});
scrimEl.addEventListener("click", closeSidebar);
document.getElementById("newchat").addEventListener("click", newConv);
document.getElementById("go-about").addEventListener("click", () => { closeSidebar(); navigate("#/doc/about"); });
if (llmBtn) llmBtn.addEventListener("click", () => { navigate("#/settings"); });
if (convSearchEl) convSearchEl.addEventListener("input", renderConvList);

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
  if (document.visibilityState === "hidden") store.save();
});
// 视口变化：移动端旋转/宽度变化时收起抽屉
window.addEventListener("resize", () => {
  if (window.innerWidth >= 1024) closeSidebar();
  autoGrow();
});

// ---------- 初始化 ----------
store.load();
store.setTheme(store.theme());
if (verBadge) verBadge.textContent = APP_VERSION;
state.convId = store.currentId;
if (totalEl) totalEl.textContent = store.totalTokens().toLocaleString();
renderConvList();
updateHeaderTitle();
renderChat();
handleRoute();
autoGrow();
if (window.innerWidth >= 1024) inputEl.focus();
// 供应商列表与顶栏徽标（异步，不阻塞首屏；失败时静默退回"服务端默认"）
void loadProviders();
