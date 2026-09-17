// RailFanAI · 外观主题（跟随系统 / 浅色 / 深色）
//
// 为什么单独成模块：模式解析（auto 要看系统偏好）是纯逻辑，抽出来就能用 node 直接测，
// 与 markdown.js / throttle.js 抽出成模块是同一个理由。
//
// 为什么是**三档**而不是一个深浅开关：默认状态是"跟随系统"，只有两档的话这个状态
// 无处表达 —— 用户点一下浅色再点回深色，就永远失去了跟随能力。CSS 侧只认最终结果
// （`html[data-theme="light"|"dark"]`），"跟随"这一层只活在这里，避免两边各判一次。

export const THEME_MODES = ["auto", "light", "dark"];
export const DEFAULT_THEME_MODE = "auto";

/** `<meta name="theme-color">` 的值（Android 上决定状态栏底色）。 */
export const THEME_COLORS = { light: "#f6f7fb", dark: "#0f1115" };

const LIGHT_QUERY = "(prefers-color-scheme: light)";

/** 任何来路不明的值都归到默认模式（存过 `"Light"`、`undefined`、旧版本残留都算）。 */
export function normalizeMode(mode) {
  const m = String(mode == null ? "" : mode).trim().toLowerCase();
  return THEME_MODES.indexOf(m) >= 0 ? m : DEFAULT_THEME_MODE;
}

/** 系统当前是不是浅色。没有 matchMedia 的环境按深色算（与本应用原本的默认一致）。 */
export function systemPrefersLight(win) {
  const w = win || (typeof window !== "undefined" ? window : null);
  if (!w || typeof w.matchMedia !== "function") return false;
  try {
    return !!w.matchMedia(LIGHT_QUERY).matches;
  } catch (e) {
    return false;
  }
}

/** 把模式解析成**实际生效**的主题：auto → 看系统；light / dark → 就是它自己。 */
export function resolveTheme(mode, prefersLight) {
  const m = normalizeMode(mode);
  if (m === "auto") return prefersLight ? "light" : "dark";
  return m;
}

/**
 * 把主题落到 DOM：`<html data-theme>` + `<meta name="theme-color">`。
 * 返回实际生效的主题，便于调用方与测试断言（而不是再回头读一遍 DOM）。
 */
export function applyTheme(mode, doc, prefersLight) {
  const d = doc || (typeof document !== "undefined" ? document : null);
  const light = prefersLight === undefined ? systemPrefersLight() : !!prefersLight;
  const theme = resolveTheme(mode, light);
  if (d) {
    if (d.documentElement) d.documentElement.setAttribute("data-theme", theme);
    const meta = d.querySelector ? d.querySelector('meta[name="theme-color"]') : null;
    if (meta) meta.setAttribute("content", THEME_COLORS[theme] || "");
  }
  return theme;
}

/**
 * 监听系统深浅色切换（用户在系统设置里改了、或到了日落自动切换）。
 *
 * 调用方**不必**先判断模式是不是 auto —— 回调里照常调 `applyTheme(当前模式)` 即可，
 * 显式的 light/dark 模式不受系统影响，这样就不存在"监听器装没装对"这种分支。
 * 返回取消函数；重复调用前应先取消，否则真机上监听器会越堆越多（WebView 不会自己回收）。
 */
export function watchSystemTheme(onChange, win) {
  const w = win || (typeof window !== "undefined" ? window : null);
  if (!w || typeof w.matchMedia !== "function") return () => {};
  const mq = w.matchMedia(LIGHT_QUERY);
  const handler = () => onChange(systemPrefersLight(w));
  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }
  // 老 WebView 只有已废弃的 addListener（Android 系统 WebView 更新较快，但 API 24–26 的机器上还在）
  if (typeof mq.addListener === "function") {
    mq.addListener(handler);
    return () => mq.removeListener(handler);
  }
  return () => {};
}
