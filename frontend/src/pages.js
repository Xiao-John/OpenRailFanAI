// OpenRailFanAI · 内容页（使用帮助 / 免责声明 / 关于 / 联系我们）
// 说明：文本目前是**占位骨架**（结构齐全、正文待填），页面会显式标注"待补充"，
// 避免把模板当正式内容对外发布。填充位置见各处 .todo 区块。

import { store } from "./store.js";

const API_BASE = window.__API_BASE__ || "";

export const DOCS = {
  disclaimer: { title: "免责声明", icon: "⚠️" },
  help: { title: "使用帮助", icon: "❓" },
  about: { title: "关于我们", icon: "ℹ️" },
  contact: { title: "联系我们", icon: "✉️" },
};

export const APP_VERSION = "v0.6 · Community";

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

function todo(text) {
  return el("div", "todo", "【待补充】" + text);
}

function placeholder(text) {
  return el("div", "placeholder-block", text);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const resp = await fetch(API_BASE + path, { ...options, headers });
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
  const { onBack, navigate } = deps;
  const meta = DOCS[key] || { title: "文档", icon: "📄" };
  const root = el("div");
  root.appendChild(pageHeader(meta.title, onBack));

  if (key === "about") return renderAbout(root, deps);
  if (key === "contact") return renderContact(root, deps);
  if (key === "help") return renderHelp(root, deps);

  // 协议类（用户协议 / 隐私政策 / 免责声明）——结构齐备，正文待填
  root.appendChild(placeholder("本节为条款正文占位，待法务/运营补齐后替换本段。"));

  if (key === "disclaimer") {
    for (const [h, body] of [
      ["一、数据来源与准确性", "数据来自 12306、rail.re、黄河铁路网等第三方，可能存在延迟或错误，**不作为出行依据**。"],
      ["二、不构成任何建议", "不构成购票、线路、拍摄安全等专业建议；外出拍车请遵守法规与现场规定。"],
      ["三、模型生成内容", "知识型回答可能包含模型记忆内容，页面已标注，请以官方资料为准。"],
      ["四、第三方链接", "外链内容由第三方负责，平台不承担其准确性责任。"],
    ]) {
      root.appendChild(el("h2", null, h));
      root.appendChild(todo(body));
    }
  }

  root.appendChild(el("div", "meta", `版本 ${APP_VERSION} · 最后更新：待填写`));
  return root;
}

function renderAbout(root, deps) {
  const intro = card("这是什么");
  intro.appendChild(el("p", null,
    "RailFanAI 是面向中国铁路爱好者的 RAG / Agent 助手：输入一句自然语言，系统先判断意图与问题性质，" +
    "再调用真实数据源检索，最后给出带来源与时效说明的回答。"));
  root.appendChild(intro);

  const cap = card("能做什么");
  for (const t of [
    "车次 ↔ 担当车组（交路）互查 —— rail.re",
    "两站间实时余票 / 车次时刻 / 经停站 —— 12306",
    "车站当日到发车次（站台、车底型号、担当客运段）—— 12306 车站大屏",
    "按线路名查站序、指定径路与里程（含既有线口径）—— 黄河铁路网",
    "站名/拼音/电报码互查（3384 站，含同音纠错）—— 12306 站点库",
    "拍摄点建议（结合交路与站点，拒答危险行为）",
    "多轮追问、生成中停止、编辑重发、多对话管理",
  ]) {
    const li = el("li", null, t);
    cap.appendChild(li);
  }
  root.appendChild(cap);

  const tech = card("技术说明");
  tech.appendChild(kv("流水线", "意图分类 → 槽位抽取 → 数据检索 → 回答生成"));
  tech.appendChild(kv("前端", "原生 HTML/JS（无构建工具），移动优先自适应"));
  tech.appendChild(kv("后端", "FastAPI（SSE 流式）"));
  tech.appendChild(kv("账户", "手机号 + 验证码 / JWT（M11.1）"));
  tech.appendChild(kv("前端版本", APP_VERSION));
  tech.appendChild(el("div", "sub",
    "开源与数据版权：各数据源版权归其所有者，本项目仅做检索与转述，回答中均附来源。"));
  root.appendChild(tech);

  const links = card("更多");
  for (const [key, meta] of Object.entries(DOCS)) {
    if (key === "about") continue;
    const row = el("div", "list-link");
    row.appendChild(el("span", null, `${meta.icon} ${meta.title}`));
    row.appendChild(el("span", "arrow", "›"));
    row.addEventListener("click", () => deps.navigate && deps.navigate("#/doc/" + key));
    links.appendChild(row);
  }
  root.appendChild(links);
  return root;
}

function renderContact(root, deps) {
  const c = card("联系方式");
  c.appendChild(todo("客服邮箱 / 微信公众号 / 反馈群 / 商务合作邮箱 —— 待填写"));
  c.appendChild(kv("问题反馈", "待填写"));
  c.appendChild(kv("商务合作", "待填写"));
  c.appendChild(kv("响应时间", "待填写（建议：工作日 1-3 个工作日）"));
  root.appendChild(c);

  const note = card("提交问题时请附上");
  for (const t of ["问题原句与时间", "页面显示的意图与数据来源", "若是错误数据，请附权威来源截图/链接"]) {
    note.appendChild(el("li", null, t));
  }
  root.appendChild(note);
  root.appendChild(el("div", "meta", "本页信息待补齐；当前版本未提供工单系统。"));
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
