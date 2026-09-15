// RailFanAI · 本机数据层（对话 / 登录态 / 偏好）
// 设计原则：
//   1) 所有数据先落 localStorage，**服务端可无状态**（v1 会话不落库）；
//   2) 写入做容量保护：对话数、单对话消息数、元信息体积都有上限，避免 localStorage 爆掉；
//   3) 只存"可重建"的展示数据；不存任何密钥（社区版无需登录）。

const LS_CONVS = "railfan_conversations_v1";
const LS_CURRENT = "railfan_current_conv_v1";
const LS_TOTAL = "railfan_total_tokens";
const LS_THEME = "railfan_theme";

const MAX_CONVS = 100;          // 最多保留的对话数（超出丢最旧的、非当前）
const MAX_MSGS = 300;           // 单对话最多消息数
const MAX_THINK = 4000;         // 单条思考内容上限（字符）
const MAX_LOGS = 60;            // 单条流程日志行数上限
const MAX_SOURCES = 20;         // 单条来源数上限

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function safeParse(raw, fallback) {
  try {
    const v = JSON.parse(raw);
    return v == null ? fallback : v;
  } catch {
    return fallback;
  }
}

/** 压缩要持久化的 meta（思考/日志/来源都可能很长）。 */
function slimMeta(meta) {
  if (!meta || typeof meta !== "object") return {};
  const out = { ...meta };
  if (typeof out.thinking === "string" && out.thinking.length > MAX_THINK) {
    out.thinking = out.thinking.slice(0, MAX_THINK) + "…";
  }
  if (Array.isArray(out.processLogs) && out.processLogs.length > MAX_LOGS) {
    out.processLogs = out.processLogs.slice(-MAX_LOGS);
  }
  if (Array.isArray(out.sources) && out.sources.length > MAX_SOURCES) {
    out.sources = out.sources.slice(0, MAX_SOURCES);
  }
  return out;
}

function slimMessages(messages) {
  const list = Array.isArray(messages) ? messages.slice(-MAX_MSGS) : [];
  return list.map((m) => ({
    role: m.role,
    content: String(m.content || ""),
    meta: m.role === "assistant" ? slimMeta(m.meta) : undefined,
  }));
}

export const store = {
  conversations: [],
  currentId: null,

  // ---------- 载入 / 保存 ----------
  load() {
    const raw = safeParse(localStorage.getItem(LS_CONVS), []);
    this.conversations = Array.isArray(raw) ? raw.filter((c) => c && c.id) : [];
    this.conversations.forEach((c) => {
      c.messages = slimMessages(c.messages);
      if (!c.createdAt) c.createdAt = Date.now();
      if (!c.updatedAt) c.updatedAt = c.createdAt;
      if (!c.title) c.title = "新对话";
    });
    this.currentId = localStorage.getItem(LS_CURRENT) || null;
    if (!this.conversations.length) {
      this.create();                       // 首次进入给一个空对话
    } else if (!this.conversations.some((c) => c.id === this.currentId)) {
      this.currentId = this.sort()[0].id;
    }
    return this;
  },

  save() {
    // 容量保护：只保留最近的 MAX_CONVS 个（当前对话一定保留）
    if (this.conversations.length > MAX_CONVS) {
      const sorted = this.sort();
      const keep = new Set(sorted.slice(0, MAX_CONVS).map((c) => c.id));
      if (this.currentId) keep.add(this.currentId);
      this.conversations = this.conversations.filter((c) => keep.has(c.id));
    }
    try {
      localStorage.setItem(LS_CONVS, JSON.stringify(this.conversations));
      if (this.currentId) localStorage.setItem(LS_CURRENT, this.currentId);
    } catch (e) {
      // 配额超限：丢掉最旧的 20% 再试一次，仍失败则提示（不阻断对话）
      const sorted = this.sort();
      this.conversations = sorted.slice(0, Math.max(1, Math.floor(sorted.length * 0.8)));
      try {
        localStorage.setItem(LS_CONVS, JSON.stringify(this.conversations));
        return "trimmed";
      } catch {
        return "failed";
      }
    }
    return "ok";
  },

  // ---------- 查询 ----------
  sort() {
    return this.conversations.slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  },
  search(kw) {
    const q = String(kw || "").trim().toLowerCase();
    const list = this.sort();
    if (!q) return list;
    return list.filter((c) => {
      if ((c.title || "").toLowerCase().includes(q)) return true;
      return c.messages.some((m) => String(m.content || "").toLowerCase().includes(q));
    });
  },
  get(id) {
    return this.conversations.find((c) => c.id === id) || null;
  },
  current() {
    return this.get(this.currentId) || this.create();
  },

  // ---------- 变更 ----------
  create() {
    const now = Date.now();
    const conv = { id: uid(), title: "新对话", titleAuto: true, createdAt: now, updatedAt: now, messages: [] };
    this.conversations.unshift(conv);
    this.currentId = conv.id;
    this.save();
    return conv;
  },
  setCurrent(id) {
    if (!this.get(id)) return;
    this.currentId = id;
    this.save();
  },
  rename(id, title) {
    const c = this.get(id);
    if (!c) return;
    const t = String(title || "").trim();
    c.title = t || "新对话";
    c.titleAuto = !t;
    c.updatedAt = Date.now();
    this.save();
  },
  remove(id) {
    this.conversations = this.conversations.filter((c) => c.id !== id);
    if (this.currentId === id) {
      this.currentId = this.conversations.length ? this.sort()[0].id : null;
      if (!this.currentId) this.create();
    }
    this.save();
  },
  clearAll() {
    this.conversations = [];
    this.currentId = null;
    this.create();
  },

  /** 添加消息；助手消息的 meta 会被压缩后持久化。 */
  addMessage(convId, msg) {
    const c = this.get(convId);
    if (!c) return null;
    const item = { role: msg.role, content: String(msg.content || "") };
    if (msg.role === "assistant") item.meta = slimMeta(msg.meta);
    c.messages.push(item);
    if (c.messages.length > MAX_MSGS) c.messages = c.messages.slice(-MAX_MSGS);
    c.updatedAt = Date.now();
    // 首条用户消息自动命名（用户手动改过就不再自动改）
    if (c.titleAuto) {
      const firstUser = c.messages.find((m) => m.role === "user");
      if (firstUser && String(firstUser.content || "").trim()) {
        const t = String(firstUser.content).replace(/\s+/g, " ").trim();
        c.title = t.length > 18 ? t.slice(0, 18) + "…" : t;
      }
    }
    this.save();
    return item;
  },

  /** 截断当前对话的消息（编辑重发 / 重新生成用）。 */
  truncate(convId, length) {
    const c = this.get(convId);
    if (!c) return;
    c.messages = c.messages.slice(0, Math.max(0, length));
    c.updatedAt = Date.now();
    this.save();
  },

  updateAssistantMeta(convId, index, meta) {
    const c = this.get(convId);
    if (!c || !c.messages[index]) return;
    c.messages[index].meta = slimMeta(meta);
    c.updatedAt = Date.now();
    this.save();
  },

  /** 流式结束后把内存中的正文与 meta 写回存储（流式过程中不逐字写，避免频繁 JSON 序列化）。 */
  updateMessage(convId, index, { content, meta } = {}) {
    const c = this.get(convId);
    const m = c && c.messages[index];
    if (!m) return;
    if (content != null) m.content = String(content);
    if (meta != null && m.role === "assistant") m.meta = slimMeta(meta);
    c.updatedAt = Date.now();
    this.save();
  },

  stats() {
    let msgs = 0;
    for (const c of this.conversations) msgs += c.messages.length;
    return { convs: this.conversations.length, msgs };
  },

  // ---------- 累计 token ----------
  totalTokens() {
    return Number(localStorage.getItem(LS_TOTAL) || 0);
  },
  bumpTotal(n) {
    const v = this.totalTokens() + (Number(n) || 0);
    localStorage.setItem(LS_TOTAL, String(v));
    return v;
  },

  // ---------- 主题 ----------
  theme() {
    return localStorage.getItem(LS_THEME) || "dark";
  },
  setTheme(t) {
    localStorage.setItem(LS_THEME, t);
    document.documentElement.setAttribute("data-theme", t);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", t === "light" ? "#f6f7fb" : "#0f1115");
  },

};

export { uid };
