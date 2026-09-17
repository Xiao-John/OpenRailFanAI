// 用 localStorage 打桩，直接验证 store.js 的会话管理逻辑
const mem = new Map();
globalThis.localStorage = {
  getItem: (k) => (mem.has(k) ? mem.get(k) : null),
  setItem: (k, v) => mem.set(k, String(v)),
  removeItem: (k) => mem.delete(k),
};
// 预置一个"老版本自动写入的 dark"：旧代码每次启动都执行 setTheme(theme())，
// 而 theme() 未设置时返回 "dark"，于是每个用过旧版的设备都躺着一条 dark。
// 这一条必须在 import store.js **之前**放进去（后端在模块加载时就定型了）。
mem.set("railfan_theme", "dark");
// 用法：node frontend/tests/store.test.mjs（也可由 backend/tests/test_frontend_store.py 调用）
import { fileURLToPath } from "node:url";
import path from "node:path";
const HERE = path.dirname(fileURLToPath(import.meta.url));
const STORE_URL = new URL("../src/store.js", import.meta.url).href;
const { store } = await import(STORE_URL);

let pass = 0, fail = 0;
const ok = (cond, msg) => { cond ? (pass++, console.log("[PASS] " + msg)) : (fail++, console.log("[FAIL] " + msg)); };

store.load();
ok(store.conversations.length === 1, "首次载入自动创建一个空对话");
const c1 = store.current();

store.addMessage(c1.id, { role: "user", content: "G1 今天由哪组动车组担当？" });
store.addMessage(c1.id, { role: "assistant", content: "由 CR400BFA-5159 担当。",
  meta: { intent: "emu_routing（车组交路查询）", sources: ["https://api.rail.re/train/G1"],
          thinking: "x".repeat(9000), processLogs: Array.from({length: 200}, (_, i) => "log" + i) } });
ok(store.get(c1.id).title.startsWith("G1 今天"), "首条用户消息自动命名对话：" + store.get(c1.id).title);
ok(store.get(c1.id).messages.length === 2, "消息已写入（2 条）");
const am = store.get(c1.id).messages[1].meta;
ok(am.thinking.length <= 4001, "超长 thinking 被压缩到 " + am.thinking.length + " 字");
ok(am.processLogs.length <= 60, "超长 processLogs 被裁剪到 " + am.processLogs.length + " 行");
ok(am.sources[0].includes("rail.re"), "来源被保留");

// 新对话不再覆盖旧对话（核心诉求）
const c2 = store.create();
ok(store.conversations.length === 2, "「新对话」创建新会话而不是覆盖：" + store.conversations.length + " 个");
ok(store.currentId === c2.id, "当前对话切到新会话");
ok(store.get(c1.id).messages.length === 2, "旧对话内容完好（未被清空）");

// 切换 / 重命名 / 删除
store.setCurrent(c1.id);
ok(store.current().id === c1.id, "切换回旧对话成功");
store.rename(c2.id, "我的第二条对话");
ok(store.get(c2.id).title === "我的第二条对话", "重命名生效");
store.addMessage(c2.id, { role: "user", content: "北京到上海走哪条线路？" });
ok(store.get(c2.id).title === "我的第二条对话", "手动命名后不再被自动改名");

store.remove(c2.id);
ok(store.conversations.length === 1 && !store.get(c2.id), "删除对话生效");
ok(store.currentId === c1.id, "删除非当前对话后当前指针不变");

// 截断（编辑重发/重新生成）
const before = store.get(c1.id).messages.length;
store.truncate(c1.id, 1);
ok(store.get(c1.id).messages.length === 1 && before === 2, "truncate 丢弃后续消息");

// 搜索
store.addMessage(c1.id, { role: "user", content: "明天北京到上海的高铁余票" });
ok(store.search("余票").length === 1, "按关键字搜索命中 1 个对话");
ok(store.search("不存在的词").length === 0, "无匹配返回空");

// 持久化：重新 load 应从 localStorage 恢复
const idsBefore = store.conversations.map((c) => c.id);
store.save();
delete globalThis.__store;
const fresh = await import(STORE_URL + "?fresh=1");
fresh.store.load();
ok(fresh.store.conversations.length === 1 && fresh.store.conversations[0].id === idsBefore[0],
   "重新载入后从 localStorage 恢复同一对话");
ok(fresh.store.get(idsBefore[0]).messages.length === 2, "消息内容也恢复（2 条）");

// 容量保护：超过 100 个对话时裁剪
for (let i = 0; i < 130; i++) fresh.store.create();
ok(fresh.store.conversations.length <= 100, "对话数量被限制在 100 以内：" + fresh.store.conversations.length);

// 累计 token
const t = fresh.store.bumpTotal(1234);
ok(t >= 1234, "累计 token 累加生效：" + t);
ok(fresh.store.totalTokens() === t, "累计 token 持久化读取一致");

// ---------- LLM 供应商（BYOK）----------
// 关键约束：**未勾选"记住 Key"时，Key 绝不落盘**（隐私红线，改了必须在这里失败）
const LS_LLM = "railfan_llm_v1";
fresh.store.setLlmRemember(false);
fresh.store.upsertLlmEntry({ id: "deepseek", label: "DeepSeek",
  base_url: "https://api.deepseek.com", model: "deepseek-flash", key: "sk-should-not-persist" });
ok(fresh.store.llmKey("deepseek") === "sk-should-not-persist", "同一会话内可读到 Key（内存）");
ok(!String(mem.get(LS_LLM) || "").includes("sk-should-not-persist"),
   "未勾选「记住 Key」时 Key 不落盘");
ok(String(mem.get(LS_LLM) || "").includes("deepseek"), "供应商条目本身照常持久化");
ok(fresh.store.llm().activeId === "deepseek", "第一条添加的条目自动成为当前使用");

// 多条目：新增 / 切换 / 删除
fresh.store.upsertLlmEntry({ id: "custom-1", label: "公司网关",
  base_url: "https://gw.corp.com/v1", model: "qwen-plus", key: "sk-corp", custom: true });
ok(fresh.store.llmEntries().length === 2, "可同时保存多家供应商：" + fresh.store.llmEntries().length);
fresh.store.setActiveLlm("custom-1");
ok(fresh.store.activeLlmEntry().id === "custom-1", "可切换当前使用的供应商");
fresh.store.removeLlmEntry("custom-1");
ok(fresh.store.llmEntries().length === 1 && fresh.store.llm().activeId === "deepseek",
   "删除当前条目后自动回退到剩余条目");

// 勾选后允许落盘，并可跨会话恢复
fresh.store.setLlmRemember(true);
fresh.store.upsertLlmEntry({ id: "deepseek", key: "sk-remembered" });
ok(String(mem.get(LS_LLM)).includes("sk-remembered"), "勾选「记住 Key」后才落盘");
const fresh2 = await import(STORE_URL + "?fresh=2");
ok(fresh2.store.activeLlmEntry().id === "deepseek", "当前供应商可跨会话恢复");
ok(fresh2.store.llmKey("deepseek") === "sk-remembered", "记住的 Key 可跨会话恢复");
ok(fresh2.store.llm().rememberKey === true, "记住标记被保留");

// 旧结构迁移：不能因为改结构就把用户已填的配置丢掉
mem.set(LS_LLM, JSON.stringify({ provider: "siliconflow", model: "old-model",
  base_url: "https://api.siliconflow.cn/v1", rememberKey: true, keys: { siliconflow: "sk-old" } }));
const fresh3 = await import(STORE_URL + "?fresh=3");
ok(fresh3.store.llmEntries().length === 1, "旧结构可迁移为条目列表");
ok(fresh3.store.llmKey("siliconflow") === "sk-old", "迁移后 Key 保留");
ok(fresh3.store.activeLlmEntry().model === "old-model", "迁移后模型保留");

// 清空
fresh3.store.clearLlm();
ok(!mem.has(LS_LLM) && fresh3.store.llmEntries().length === 0, "clearLlm 清空全部条目与 Key");

// ---------- 主题：默认跟随系统 + 清掉老版本自动写入的偏好 ----------
// 这个实例是从"带着 railfan_theme=dark 的旧状态"载入的（见文件开头预置的那一条）。
ok(store.theme() === "dark", "迁移只发生在 initTheme()，读偏好本身不擅自动它");
store.initTheme();
ok(store.theme() === "auto", "initTheme() 清掉老版本自动写入的 dark → 跟随系统");
ok(!mem.has("railfan_theme") || mem.get("railfan_theme") === "auto",
   "迁移后的偏好是 auto（不是残留的 dark）");
ok(mem.get("railfan_theme_v2") === "1", "落下迁移标记，避免每次都清");
store.setTheme("dark");
ok(store.theme() === "dark", "用户显式选过之后就按用户的选择来");
store.initTheme();
ok(store.theme() === "dark", "再启动一次不会把用户显式选的 dark 又清掉（迁移只跑一次）");
store.setTheme("auto");
ok(store.theme() === "auto", "用户也能主动选回「跟随系统」");

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
