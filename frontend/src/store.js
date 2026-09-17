// RailFanAI · 本机数据层（对话 / 供应商选择 / 偏好）
// 设计原则：
//   1) 所有数据先落**本机**（见下方 db：浏览器里是 localStorage，Android 应用里是
//      原生侧的应用私有文件），**服务端无状态**（v1 会话不落库）；
//   2) 写入做容量保护：对话数、单对话消息数、元信息体积都有上限，避免存储爆掉；
//   3) 不存任何"账号凭据"（社区版没有账号体系）。
//      例外：用户自备的 LLM Key（BYOK）可**按用户显式勾选**存在本机 —— 它是
//      用户自己的 Key，只发往用户自己配置的后端，不经过任何第三方；
//      未勾选时只保留在内存里（刷新即失效），见 setLlm/llmKey。

import { native } from "./native.js";
import { applyTheme, normalizeMode, watchSystemTheme } from "./theme.js";

const LS_CONVS = "railfan_conversations_v1";
const LS_CURRENT = "railfan_current_conv_v1";
const LS_TOTAL = "railfan_total_tokens";
const LS_THEME = "railfan_theme";
// 主题偏好的一次性迁移标记（见 migrateTheme）
const LS_THEME_MIGRATED = "railfan_theme_v2";
const LS_LLM = "railfan_llm_v1";

const MAX_CONVS = 100;          // 最多保留的对话数（超出丢最旧的、非当前）
const MAX_MSGS = 300;           // 单对话最多消息数
const MAX_THINK = 4000;         // 单条思考内容上限（字符）
const MAX_LOGS = 60;            // 单条流程日志行数上限
const MAX_SOURCES = 20;         // 单条来源数上限

// ================= 本机持久化通道 =================
//
// 为什么要在 localStorage 之上再加一层：
//
// localStorage 是**按「源」隔离**的，而源 = scheme://host:port。Android 一体化版
// 的后端跑在 127.0.0.1 的随机端口上，端口一旦变化（上次那个被别的 App 占了就换），
// 整个应用在浏览器眼里就成了"另一个站点" —— 对话、供应商配置、记住的 Key 全部读不到，
// 用户看到的是"我的数据没了"。这不是理论：真机上 http://127.0.0.1:58213 存的东西
// 在 http://127.0.0.1:43657 下就是读不到。
//
// 所以接了原生桥时（Android 应用），状态写进应用私有目录里的一个文件，与端口无关；
// 桥不存在时（桌面浏览器、node 单测）行为与从前完全一致，仍是 localStorage。
//
// 为什么不干脆把状态挪到后端 API：那会把整套数据层染成异步（store.js 现在全同步，
// 有几十个调用点）。而 @JavascriptInterface 的返回值是同步的，所以"走原生文件"
// 既解决了问题，又一行都不用改调用方。

const LS_FALLBACK = typeof localStorage !== "undefined" ? localStorage : null;

/** 原生文件后端：整份状态一个 JSON 文档，读一次、写整份。 */
function nativeBackend() {
  let doc = {};
  try {
    const raw = native.readState();
    doc = raw ? (JSON.parse(raw) || {}) : {};
  } catch (e) {
    // 文件损坏时宁可丢一次，也不要让应用启动就崩
    console.warn("[store] 原生状态解析失败，从空状态开始", e);
    doc = {};
  }

  // 首次启用原生存储时，把当前源里已有的数据搬过来。
  // 这是**最后的机会**：旧版本的数据躺在某个固定端口的 localStorage 里，
  // 升级后想找回它就得靠运气，所以只要原生侧还是空的就搬一次。
  if (!Object.keys(doc).length && LS_FALLBACK) {
    for (const k of [LS_CONVS, LS_CURRENT, LS_TOTAL, LS_THEME, LS_LLM]) {
      const v = LS_FALLBACK.getItem(k);
      if (v != null) doc[k] = v;
    }
  }

  let timer = null;
  let dirty = false;
  const flush = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (!dirty) return;
    dirty = false;
    try {
      native.writeState(JSON.stringify(doc));
    } catch (e) {
      console.warn("[store] 状态写盘失败", e);
    }
  };
  const touch = () => {
    dirty = true;
    // 桥是同步调用、会阻塞 JS 线程，而一次问答会触发十几次 save()。
    // 所以做合并：250ms 内的多次写入只落一次盘；页面隐藏/卸载时立即补写（见文件末尾）。
    if (!timer) timer = setTimeout(flush, 250);
  };

  return {
    kind: "native",
    getItem: (k) => (k in doc ? doc[k] : null),
    setItem: (k, v) => {
      doc[k] = String(v);
      touch();
    },
    removeItem: (k) => {
      delete doc[k];
      touch();
    },
    flushNow: flush,
    /** 供测试用：不经过合并直接落盘。 */
    raw: () => doc,
  };
}

/**
 * 一次性迁移：清掉老版本**自动写入**的主题偏好（v0.1.5 起默认跟随系统）。
 *
 * 为什么非做不可：老代码每次启动都会执行 `setTheme(theme())`，而 `theme()` 在没存过偏好时
 * 返回 "dark" —— 于是每个用过旧版的设备都被记下了一个**用户从没选过**的 dark；
 * 而旧版界面上**根本没有**主题入口（`data-theme` 只能靠手改 localStorage 才动得了）。
 * 不迁移的话，"跟随系统"对老用户永远不生效，且表现为"新装的人是浅色、升级的人还是黑的"
 * 这种从现象上根本解释不通的差异。
 *
 * 只跑一次（标记位），且只删偏好本身 —— 用户在新界面里重新选一次就会重新落盘。
 * 返回是否真的迁移过（供测试断言，也可用于埋点）。
 */
function migrateTheme(backend) {
  if (backend.getItem(LS_THEME_MIGRATED) != null) return false;
  backend.setItem(LS_THEME_MIGRATED, "1");
  const stale = backend.getItem(LS_THEME);
  if (stale != null) backend.removeItem(LS_THEME);
  return stale != null;
}

function createBackend() {
  if (native.available) {
    try {
      return nativeBackend();
    } catch (e) {
      console.warn("[store] 原生存储不可用，退回 localStorage", e);
    }
  }
  return {
    kind: "localStorage",
    getItem: (k) => (LS_FALLBACK ? LS_FALLBACK.getItem(k) : null),
    setItem: (k, v) => {
      if (LS_FALLBACK) LS_FALLBACK.setItem(k, v);
    },
    removeItem: (k) => {
      if (LS_FALLBACK) LS_FALLBACK.removeItem(k);
    },
    flushNow() {},
  };
}

const db = createBackend();

// 切到后台/关页面时把合并中的写入补上 —— 否则最后 250ms 内的消息会丢。
// visibilitychange 是关键的那条：Android 应用被系统回收前一定会先转后台。
if (typeof document !== "undefined") {
  window.addEventListener("pagehide", () => db.flushNow());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") db.flushNow();
  });
}

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
    const raw = safeParse(db.getItem(LS_CONVS), []);
    this.conversations = Array.isArray(raw) ? raw.filter((c) => c && c.id) : [];
    this.conversations.forEach((c) => {
      c.messages = slimMessages(c.messages);
      if (!c.createdAt) c.createdAt = Date.now();
      if (!c.updatedAt) c.updatedAt = c.createdAt;
      if (!c.title) c.title = "新对话";
    });
    this.currentId = db.getItem(LS_CURRENT) || null;
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
      db.setItem(LS_CONVS, JSON.stringify(this.conversations));
      if (this.currentId) db.setItem(LS_CURRENT, this.currentId);
    } catch (e) {
      // 配额超限：丢掉最旧的 20% 再试一次，仍失败则提示（不阻断对话）
      const sorted = this.sort();
      this.conversations = sorted.slice(0, Math.max(1, Math.floor(sorted.length * 0.8)));
      try {
        db.setItem(LS_CONVS, JSON.stringify(this.conversations));
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
    return Number(db.getItem(LS_TOTAL) || 0);
  },
  bumpTotal(n) {
    const v = this.totalTokens() + (Number(n) || 0);
    db.setItem(LS_TOTAL, String(v));
    return v;
  },

  // ---------- 外观主题 ----------
  //
  // 三个模式：auto（跟随系统，**默认**）/ light / dark。
  // 默认从"dark"改成"auto"：社区版面向的是普通用户，系统是浅色却给人一个黑界面
  // 属于"我的应用坏了"级别的观感；显式选过 light/dark 的用户不受影响。
  theme() {
    return normalizeMode(db.getItem(LS_THEME));
  },
  setTheme(mode) {
    const m = normalizeMode(mode);
    db.setItem(LS_THEME, m);
    return applyTheme(m);
  },
  /**
   * 启动时应用主题，并在系统切换深浅色时实时跟随（不刷新页面）。
   *
   * 系统一变就无条件重算：显式 light/dark 模式下 `applyTheme` 的结果与系统无关，
   * 所以不需要在这里判断"是不是 auto"——少一个分支就少一处装错监听器的机会。
   */
  initTheme() {
    migrateTheme(db);
    const applied = this.setTheme(this.theme());
    if (this._themeUnwatch) this._themeUnwatch();      // 防重复挂载（真机上监听器不会自己回收）
    this._themeUnwatch = watchSystemTheme(() => applyTheme(this.theme()));
    return applied;
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
      const raw = safeParse(db.getItem(LS_LLM), null);
      this._llm = _migrateLlm(raw);
      // Key 若存在系统密钥库里，明文文档中就是空的 —— 这里再逐个取回来填进内存模型。
      // 顺序很重要：先按明文恢复条目（id/base_url/model），再补 Key。
      if (this._llm.rememberKey && native.secure.available) {
        for (const e of this._llm.entries) e.key = native.secure.get(e.id) || "";
      }
    }
    return this._llm;
  },
  _persistLlm() {
    const next = this.llm();
    const entries = next.entries || [];
    const secure = native.secure.available;

    if (next.rememberKey && secure) {
      // 「记住 Key」+ 有系统密钥库：Key 一律走 Keystore 加密存，明文文档里一个都不留。
      for (const e of entries) native.secure.put(e.id, e.key || "");
    } else if (!next.rememberKey && secure) {
      // 取消勾选后要**真的删掉**密钥库里的副本 —— 否则用户以为删了、其实还在，
      // 下次一勾"记住"就"自己回来了"，那是最容易被当成 bug 的行为。
      for (const e of entries) native.secure.remove(e.id);
    }

    const persisted = { ...next };
    if (!next.rememberKey || secure) {
      // 不记住 → 绝不落盘；走密钥库 → 明文里也不该有 Key。
      persisted.entries = entries.map((e) => ({ ...e, key: "" }));
    }
    try {
      db.setItem(LS_LLM, JSON.stringify(persisted));
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
    native.secure.remove(id);          // 条目删了，密钥库里的 Key 不能留
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
    // 密钥库里的副本也要清 —— 否则"清空"只清了引用，Key 还躺在系统里。
    if (native.secure.available) {
      for (const e of (this._llm && this._llm.entries) || []) native.secure.remove(e.id);
    }
    this._llm = { entries: [], activeId: "", rememberKey: false };
    db.removeItem(LS_LLM);
  },

};

export { uid };

// 供测试与诊断使用：当前生效的持久化后端（"native" = Android 应用私有文件，
// "localStorage" = 浏览器）。把它暴露出来，"数据到底存在哪"就不用靠猜。
export const persistence = db;
