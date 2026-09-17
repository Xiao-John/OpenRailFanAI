// RailFanAI · 外观主题测试（node 直跑，不需要浏览器）
//
// 钉住三件事：
//   1) **默认是跟随系统** —— 没存过任何偏好时，系统浅色就该给浅色、系统深色就给深色。
//      这是本次改动的核心承诺，也是最容易在重构里悄悄退回成"写死深色"的地方。
//   2) 显式选择优先 —— 选过 light/dark 之后，系统怎么变都不该被带跑。
//   3) 脏值兜底 —— 存过 "Light"、旧版本残留、undefined 都归到默认模式，不能变成
//      `data-theme="undefined"`（那会让 CSS 两套令牌全不匹配，页面变成裸样式）。
//
// 用法：node frontend/tests/theme.test.mjs

import {
  THEME_MODES, DEFAULT_THEME_MODE, THEME_COLORS,
  normalizeMode, resolveTheme, applyTheme, watchSystemTheme, systemPrefersLight,
} from "../src/theme.js";

let pass = 0, fail = 0;
const ok = (cond, msg) => { cond ? (pass++, console.log("[PASS] " + msg)) : (fail++, console.log("[FAIL] " + msg)); };

// ---------- 1. 模式与默认 ----------
ok(DEFAULT_THEME_MODE === "auto", "默认模式是 auto（跟随系统）");
ok(THEME_MODES.join(",") === "auto,light,dark", "模式全集是 auto/light/dark");

// ---------- 2. 解析（auto 看系统；显式选择压倒系统）----------
ok(resolveTheme("auto", true) === "light", "auto + 系统浅色 → light");
ok(resolveTheme("auto", false) === "dark", "auto + 系统深色 → dark");
ok(resolveTheme("light", false) === "light", "显式浅色不受系统深色影响");
ok(resolveTheme("dark", true) === "dark", "显式深色不受系统浅色影响");
ok(resolveTheme(undefined, true) === "light", "没存过偏好 → 按默认（跟随系统）走");

// ---------- 3. 脏值兜底 ----------
ok(normalizeMode("Light") === "light", "大小写与空格不敏感");
ok(normalizeMode(" dark ") === "dark", "带空格的 dark 认得出");
for (const bad of [undefined, null, "", "  ", "system", "0", "theme"]) {
  ok(normalizeMode(bad) === DEFAULT_THEME_MODE, `脏值 ${JSON.stringify(bad)} → 默认模式`);
}

// ---------- 4. applyTheme 落到 DOM ----------
function fakeDoc() {
  const attrs = {};
  const meta = { attrs: {}, setAttribute: (k, v) => { meta.attrs[k] = v; } };
  return {
    documentElement: { setAttribute: (k, v) => { attrs[k] = v; } },
    querySelector: (sel) => (sel.indexOf("theme-color") >= 0 ? meta : null),
    _attrs: attrs,
    _meta: meta,
  };
}
{
  const d = fakeDoc();
  const t = applyTheme("auto", d, true);
  ok(t === "light" && d._attrs["data-theme"] === "light", "auto + 系统浅色 → <html data-theme=light>");
  ok(d._meta.attrs.content === THEME_COLORS.light, "theme-color 跟着变（否则状态栏还是黑的）");
}
{
  const d = fakeDoc();
  applyTheme("dark", d, true);
  ok(d._attrs["data-theme"] === "dark", "显式深色在浅色系统下仍写 dark");
}
ok(applyTheme("auto", null, false) === "dark", "没有 document 也不抛（node / 早期待测环境）");

// ---------- 5. 系统切换监听 ----------
function fakeWin(matches, withAddEventListener = true) {
  const listeners = [];
  const mq = {
    matches,
    media: "(prefers-color-scheme: light)",
    addEventListener: withAddEventListener ? (t, cb) => listeners.push(cb) : undefined,
    removeEventListener: withAddEventListener ? (t, cb) => {
      const i = listeners.indexOf(cb);
      if (i >= 0) listeners.splice(i, 1);
    } : undefined,
    addListener: withAddEventListener ? undefined : (cb) => listeners.push(cb),
    removeListener: withAddEventListener ? undefined : (cb) => {
      const i = listeners.indexOf(cb);
      if (i >= 0) listeners.splice(i, 1);
    },
  };
  return {
    matchMedia: () => mq,
    fire(nowLight) { mq.matches = nowLight; listeners.slice().forEach((cb) => cb()); },
    count: () => listeners.length,
  };
}
{
  const w = fakeWin(false);
  let got = null;
  const off = watchSystemTheme((light) => { got = light; }, w);
  w.fire(true);
  ok(got === true, "系统切到浅色时回调收到 light=true");
  off();
  w.fire(false);
  ok(got === true && w.count() === 0, "取消后不再收到回调（真机上监听器不会自己回收）");
}
{
  const w = fakeWin(false, /* withAddEventListener */ false);
  let got = null;
  const off = watchSystemTheme((light) => { got = light; }, w);
  w.fire(true);
  ok(got === true, "只有已废弃的 addListener 的老 WebView 也能跟随");
  off();
  ok(w.count() === 0, "老接口同样能取消");
}
ok(typeof watchSystemTheme(() => {}, null) === "function", "没有 window 时返回一个可调用的空取消函数");
ok(systemPrefersLight(null) === false, "没有 matchMedia 的环境按深色算（与本应用原默认一致）");

console.log("结果：" + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
