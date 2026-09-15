// RailFanAI · 本机数据层（对话 / 供应商选择 / 偏好）
// 设计原则：
//   1) 所有数据先落 localStorage，**服务端可无状态**（v1 会话不落库）；
//   2) 写入做容量保护：对话数、单对话消息数、元信息体积都有上限，避免 localStorage 爆掉；
//   3) 不存任何"账号凭据"（社区版没有账号体系）。
//      例外：用户自备的 LLM Key（BYOK）可**按用户显式勾选**存在本机 —— 它是
//      用户自己的 Key，只发往用户自己配置的后端，不经过任何第三方；
//      未勾选时只保留在内存里（刷新即失效），见 setLlm/llmKey。

const LS_CONVS = "railfan_conversations_v1";
const LS_CURRENT = "railfan_current_conv_v1";
const LS_TOTAL = "railfan_total_tokens";
const LS_THEME = "railfan_theme";
const LS_LLM = "railfan_llm_v1";

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

/**
 * 把旧的「单个选择 + key 表」结构迁移成「条目列表」。
 *
 * 为什么要写迁移而不是直接丢弃：老用户本机已经存了供应商选择与（可能记住的）Key，
 * 升级后不该让他们重填一遍 —— 掉配置比多几十行代码更伤。
 */
function _migrateLlm(raw) {
  if (!raw || typeof raw !== "object") return { entries: [], activeId: "", rememberKey: false };
  if (Array.isArray(raw.entries)) return raw;                 // 已是新结构
  const entries = [];
  const keys = raw.keys || {};
  const id = raw.provider === "custom" ? "custom" : (raw.provider || "default");
  if (raw.provider || raw.base_url) {
    entries.push({
      id,
      label: id === "custom" ? "自定义" : id,
      base_url: raw.base_url || "",
      model: raw.model || "",
      api: raw.api || "",
      key: keys[id] || keys[raw.provider] || "",
      custom: id === "custom",
    });
  }
  return { entries, activeId: entries.length ? id : "", rememberKey: !!raw.rememberKey };
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

  // ---------- LLM 供应商（BYOK）----------
  // 结构（v2，条目列表模型）：
  //   {
  //     entries: [ { id, label, base_url, model, api, key, custom } ],
  //     activeId: "deepseek",        // 当前使用哪一条
  //     rememberKey: false           // 是否把 Key 落盘
  //   }
  // - 除 key 外的字段不是秘密，始终持久化；
  // - key 只在用户勾选"记住 Key"时落盘，否则仅留在内存（本对象）里。
  //
  // 为什么从"单个选择"改成"条目列表"：旧结构只能表达"当前用哪家 + 一张 key 表"，
  // 于是界面上"选择供应商"和"填写自定义地址"必然被拆成两套互不相干的表单，
  // 用户得先自我归类。列条模型让两者变成同一种东西，界面才能是一张列表 + 一个表单。
  llm() {
    if (!this._llm) {
      const raw = safeParse(localStorage.getItem(LS_LLM), null);
      this._llm = _migrateLlm(raw);
    }
    return this._llm;
  },
  _persistLlm() {
    const next = this.llm();
    const persisted = { ...next };
    if (!next.rememberKey) {
      // 不记住 → 绝不落盘：整表的 key 字段都剔掉
      persisted.entries = (next.entries || []).map((e) => ({ ...e, key: "" }));
    }
    try {
      localStorage.setItem(LS_LLM, JSON.stringify(persisted));
    } catch {
      /* 容量满等情况：退化为仅内存，不阻断使用 */
    }
    return next;
  },
  setLlmRemember(rememberKey) {
    this.llm().rememberKey = !!rememberKey;
    return this._persistLlm();
  },
  llmEntries() {
    return this.llm().entries || [];
  },
  llmEntry(id) {
    return this.llmEntries().find((e) => e.id === id) || null;
  },
  activeLlmEntry() {
    const s = this.llm();
    return this.llmEntry(s.activeId) || this.llmEntries()[0] || null;
  },
  /** 新增或更新一条供应商条目（按 id 覆盖）。 */
  upsertLlmEntry(entry) {
    const s = this.llm();
    const entries = s.entries || [];
    const i = entries.findIndex((e) => e.id === entry.id);
    if (i >= 0) entries[i] = { ...entries[i], ...entry };
    else entries.push(entry);
    if (!s.activeId) s.activeId = entry.id;      // 第一条自动成为当前使用
    return this._persistLlm();
  },
  removeLlmEntry(id) {
    const s = this.llm();
    s.entries = (s.entries || []).filter((e) => e.id !== id);
    if (s.activeId === id) s.activeId = s.entries[0] ? s.entries[0].id : "";
    return this._persistLlm();
  },
  setActiveLlm(id) {
    this.llm().activeId = id;
    return this._persistLlm();
  },
  /** 保持旧 API 可用（少数调用点仍按 id 取 Key）。 */
  llmKey(id) {
    const e = this.llmEntry(id);
    return (e && e.key) || "";
  },
  setLlmKey(id, key) {
    const e = this.llmEntry(id);
    if (e) this.upsertLlmEntry({ id, key });
    return this.llm();
  },
  clearLlm() {
    this._llm = { entries: [], activeId: "", rememberKey: false };
    localStorage.removeItem(LS_LLM);
  },

};

export { uid };
