// 结构化结果来源仅允许浏览器可安全打开的网页协议。
export function safeHttpSource(value) {
  try {
    const url = new URL(String(value || ""));
    return ["https:", "http:"].includes(url.protocol) ? url.href : "";
  } catch { return ""; }
}

export function routingSourceUrl(sources = []) {
  return sources.map(safeHttpSource).find((value) => {
    if (!value) return false;
    const host = new URL(value).hostname;
    return host === "rail.re" || host.endsWith(".rail.re");
  }) || "https://rail.re";
}
