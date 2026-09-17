// RailFanAI · 高频更新的渲染节流
//
// 为什么单独成一个模块：这里的坑（合并、最小间隔、收尾取消）都不依赖 DOM，
// 抽出来就能用 node 直接跑测试，见 frontend/tests/throttle.test.mjs。
// 与 markdown.js 抽出成模块是同一个理由。

/**
 * 把密集的小更新合并成"每帧最多一次、且两次之间至少隔 minIntervalMs"。
 *
 * 为什么需要：流式回答里 delta 来得比帧还快，逐个直接改 DOM 会变成
 * "重新解析整篇 + 整棵子树重排"，代价随回答长度增长（O(n²)）——
 * 桌面端看不出来，移动 WebView 上是滚动卡顿和键盘跟随迟滞。
 *
 * @param {() => void} fn 真正做渲染的函数
 * @param {number} minIntervalMs 两次渲染之间的最小间隔
 * @returns {(() => void) & { cancel: () => void }} 带 `.cancel()` 的节流函数
 *
 * ⚠️ **收尾时必须调用 `.cancel()`**：否则挂在队列里的那一次会在最终渲染
 * 之后又画一遍（例如把已经摘掉的流式光标重新贴回去）。
 */
export function rafThrottle(fn, minIntervalMs = 0) {
  // 没有 requestAnimationFrame 的环境（老 WebView / node 测试）退回定时器
  const raf = typeof requestAnimationFrame === "function"
    ? requestAnimationFrame
    : (cb) => setTimeout(cb, 16);
  let scheduled = null;      // raf id 或 timeout id
  let pending = false;
  let last = 0;

  const run = () => {
    scheduled = null;
    pending = false;
    last = Date.now();
    fn();
  };
  const throttled = () => {
    if (pending) return;
    pending = true;
    const wait = Math.max(0, minIntervalMs - (Date.now() - last));
    scheduled = wait
      ? setTimeout(() => { scheduled = raf(run); }, wait)
      : raf(run);
  };
  throttled.cancel = () => {
    if (scheduled !== null) {
      // 两种 id 都清一遍：clearTimeout 与 cancelAnimationFrame 互不干扰
      clearTimeout(scheduled);
      if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(scheduled);
      scheduled = null;
    }
    pending = false;
  };
  return throttled;
}
