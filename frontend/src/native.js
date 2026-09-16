// RailFanAI · 原生桥（Android 一体化版）
//
// 存在的理由 —— WebView 上有三件事纯网页做不好：
//
//   1) **稳定持久化**。localStorage 按「源」隔离，而源里含端口（scheme://host:port）。
//      Android 版的后端端口会变（上次的端口被别的 App 占了就换），端口一变整个应用
//      在浏览器眼里就是**另一个站点**：对话、供应商配置、记住的 Key 全部读不到，
//      用户看到的是"数据没了"。原生侧把同一份状态写进应用私有目录的文件，与端口无关。
//   2) **读剪贴板**。BYOK 场景下"从剪贴板粘贴 Key"是刚需，WebView 里
//      navigator.clipboard.readText() 不可靠（需要权限 + 用户手势）。
//   3) **Key 的静态加密**。让 Key 存进 Android Keystore 而不是明文躺在 localStorage。
//
// 设计约束：
//   - **不引入任何依赖**。就用 MainActivity 里的 @JavascriptInterface（见 RailBridge 注释）。
//   - 桥不存在时（桌面浏览器、node 单测、未接桥的旧包）全部退化为安全空实现，
//     调用方不需要到处写 `if (android)`。
//   - **同步**：@JavascriptInterface 的返回值是同步回到 JS 的，所以 store.js 现有的
//     同步读写模型一行都不用改成 async。这也是选"原生桥 + 本地文件"而不是
//     "状态挪到后端 API"的关键原因 —— 后者会把整个数据层染成异步。
//
// 安全前提：桥只对**本应用 WebView 里加载的页面**可见。本项目的外链一律交给系统
// 浏览器（见 MainActivity.openExternally），WebView 永远不会停在第三方页面上，
// 因此不存在"陌生页面调用本桥"的路径。若将来允许 WebView 内打开第三方页面，
// 必须重新评估这个前提。

const bridge = (typeof window !== "undefined" && window.RailNative) || null;

const has = (fn) => !!(bridge && typeof bridge[fn] === "function");

/** 桥调用统一包一层：原生抛异常不能把前端带崩（旧机型/Keystore 异常都遇到过）。 */
function call(fn, ...args) {
  if (!has(fn)) return null;
  try {
    return bridge[fn](...args);
  } catch (e) {
    console.warn("[native] 调用失败：" + fn, e);
    return null;
  }
}

async function copyViaBrowser(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 落到下面的兜底 */
  }
  // 兜底：老浏览器/无权限时的 execCommand 路径
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

export const native = {
  /** 是否运行在接了原生桥的 Android 应用里。 */
  available: !!bridge,
  platform: has("platform") ? String(call("platform") || "") : "web",

  // ---------- 稳定持久化（store.js 用）----------
  // 整份状态是一个 JSON 文档，读取一次、写入整份；键名沿用 store.js 里的 LS_* 常量。
  readState() {
    const v = call("readState");
    return typeof v === "string" ? v : "";
  },
  writeState(json) {
    call("writeState", String(json));
  },

  // ---------- Key 的静态加密（Android Keystore）----------
  secure: {
    get available() {
      return has("secureAvailable") && !!call("secureAvailable");
    },
    get(id) {
      const v = call("secureGet", String(id));
      return typeof v === "string" ? v : "";
    },
    put(id, value) {
      call("securePut", String(id), String(value));
    },
    remove(id) {
      call("secureRemove", String(id));
    },
  },

  // ---------- 系统能力 ----------
  clipboard: {
    /** 读剪贴板。Android 10+ 只允许有焦点的应用读，读不到时返回 ""。 */
    async read() {
      if (has("readClipboard")) {
        const v = call("readClipboard");
        return typeof v === "string" ? v : "";
      }
      try {
        return (await navigator.clipboard.readText()) || "";
      } catch {
        return "";
      }
    },
    async copy(text) {
      if (has("copyText")) {
        call("copyText", String(text));
        return true;
      }
      return copyViaBrowser(String(text));
    },
  },

  /** 分享文本（系统分享面板）。返回是否成功唤起。 */
  async share(text) {
    if (has("shareText")) {
      const r = call("shareText", String(text));
      return r !== false;
    }
    if (navigator.share) {
      try {
        await navigator.share({ text: String(text) });
        return true;
      } catch {
        return false;                      // 用户取消也算失败，调用方不必提示错误
      }
    }
    return copyViaBrowser(String(text));
  },
};

export default native;
