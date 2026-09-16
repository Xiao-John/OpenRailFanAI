// RailFanAI · store.js 的**原生桥**路径测试（接 Android 应用时走的那条）。
//
// 要钉住的核心结论只有一条：**换源之后数据还在**。
//
// 背景：WebView 的 localStorage 按「源」隔离，而源里含端口。后端端口一变
// （上次的端口被别的 App 占了就换），整个应用在浏览器眼里就是另一个站点，
// 对话/配置/记住的 Key 全部读不到。所以接了原生桥时，状态必须落进应用私有文件。
// 这里用"清空 localStorage + 重新 import 模块"来模拟那次换源。
//
// 用法：node frontend/tests/store.native.test.mjs
// 说明：必须在 import store.js **之前**把 window.RailNative 打好桩 ——
//       native.js 是在模块求值时决定"有没有桥"的。

const mem = new Map();
globalThis.localStorage = {
  getItem: (k) => (mem.has(k) ? mem.get(k) : null),
  setItem: (k, v) => mem.set(k, String(v)),
  removeItem: (k) => mem.delete(k),
};

// store.js 会碰 document（主题开关）与 addEventListener（切后台时补写）。
// 给一个最小桩即可 —— 这里要验的是持久化，不是 DOM。
globalThis.document = {
  documentElement: { setAttribute() {} },
  querySelector: () => null,
  addEventListener() {},
  visibilityState: "visible",
};

// ---- 原生桥打桩 ----
const bridgeState = { json: "" };        // 相当于应用私有目录里的 state.json
const vault = new Map();                 // 相当于 Android Keystore 里的密文
globalThis.window = {
  addEventListener() {},                 // store.js 注册 pagehide 用
  RailNative: {
    platform: () => "android",
    readState: () => bridgeState.json,
    writeState: (j) => { bridgeState.json = String(j); },
    secureAvailable: () => true,
    // 存的是密文、取的时候解密 —— 与 Android Keystore 那侧一致。
    // （第一版桩直接返回密文，测出来的是"Key 取不回"，那是桩错、不是代码错。）
    secureGet: (id) => {
      const c = vault.get(id);
      return c ? c.slice(4) : "";
    },
    securePut: (id, v) => { v ? vault.set(id, "enc:" + v) : vault.delete(id); },
    secureRemove: (id) => vault.delete(id),
  },
};

const STORE_URL = new URL("../src/store.js", import.meta.url).href;
const { store, persistence } = await import(STORE_URL);

let pass = 0, fail = 0;
const ok = (cond, msg) => { cond ? (pass++, console.log("[PASS] " + msg)) : (fail++, console.log("[FAIL] " + msg)); };

ok(persistence.kind === "native", "有桥时用的是原生存储：" + persistence.kind);

store.load();
const c = store.current();
store.addMessage(c.id, { role: "user", content: "京沪高铁经过哪些站？" });
store.setTheme("light");
persistence.flushNow();
ok(bridgeState.json.includes("京沪高铁"), "对话已写入原生存储");

// ---- 换源：清空 localStorage + 丢掉模块缓存重新加载 ----
mem.clear();
const m2 = await import(STORE_URL + "?origin=2");
const s2 = m2.store;
s2.load();
ok(s2.conversations.length === 1, "换源后对话仍在：" + s2.conversations.length + " 个");
ok((s2.conversations[0].messages[0] || {}).content === "京沪高铁经过哪些站？",
   "换源后消息内容完好");
ok(s2.theme() === "light", "换源后主题仍在");

// ---- Key 必须走密钥库，明文文档里不许出现 ----
const { store: s3, persistence: p3 } = await import(STORE_URL + "?origin=3");
s3.load();
s3.setLlmRemember(true);
s3.upsertLlmEntry({ id: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com",
                    model: "deepseek-chat", api: "", key: "sk-绝密-123", custom: false });
p3.flushNow();
ok(!bridgeState.json.includes("sk-绝密-123"), "明文状态文档里不含 API Key");
ok(vault.get("deepseek") === "enc:sk-绝密-123", "Key 已存进密钥库（加密）");

const m4 = await import(STORE_URL + "?origin=4");
m4.store.load();
ok(m4.store.llmKey("deepseek") === "sk-绝密-123", "重载后 Key 能从密钥库取回");
ok(m4.store.activeLlmEntry().model === "deepseek-chat", "供应商其余字段照常持久化");

// ---- 取消勾选"记住 Key"要真的删掉 ----
m4.store.setLlmRemember(false);
m4.persistence.flushNow();
ok(!vault.has("deepseek"), "取消勾选后密钥库里的副本被删除");
ok(!bridgeState.json.includes("sk-绝密-123"), "取消勾选后明文里依然没有 Key");

// ---- 删条目 / 清空也要连密钥库一起清 ----
m4.store.setLlmRemember(true);
m4.store.upsertLlmEntry({ id: "kimi", label: "Kimi", base_url: "https://api.moonshot.cn/v1",
                          model: "kimi-k2", api: "", key: "sk-kimi", custom: false });
m4.persistence.flushNow();
ok(vault.has("kimi"), "新增条目后密钥库里有它");
m4.store.removeLlmEntry("kimi");
ok(!vault.has("kimi"), "删除条目后密钥库里的副本也删了");

console.log("结果：" + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
