// RailFanAI · 流式渲染节流测试（node 直跑，不需要浏览器）
//
// 为什么值得单独钉住：
//   1) 合并 —— 一次回答上百个 delta 只能触发有限次渲染，否则长回答会退化成
//      "每个 token 重新解析整篇 Markdown + 整棵子树重排"（移动端卡顿的根因）；
//   2) 最小间隔 —— 合并之外还要限频，防长回答把每帧都占满；
//   3) **cancel()** —— 收尾渲染之后若还有一次排队中的渲染，会把已经摘掉的流式
//      光标重新贴回去（回答写完了光标还在闪）。这条是本次改动最容易踩的回归。
//
// 用法：node frontend/tests/throttle.test.mjs

import { rafThrottle } from "../src/throttle.js";

let pass = 0, fail = 0;
const ok = (cond, msg) => { cond ? (pass++, console.log("[PASS] " + msg)) : (fail++, console.log("[FAIL] " + msg)); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- 1. 合并：密集调用只渲染一次 ----------
{
  let n = 0;
  const f = rafThrottle(() => n++, 0);
  for (let i = 0; i < 50; i++) f();          // 模拟 50 个连续 delta
  ok(n === 0, "同步连续调用期间不渲染（先合并）");
  await sleep(40);
  ok(n === 1, "50 次调用合并成 1 次渲染，实测 " + n);
}

// ---------- 2. 最小间隔：渲染次数被限频 ----------
{
  let n = 0;
  const f = rafThrottle(() => n++, 30);
  for (let i = 0; i < 20; i++) { f(); await sleep(2); }   // 40ms 内 20 次调用
  await sleep(60);
  ok(n <= 3, "40ms 内的 20 次调用渲染不超过 3 次（间隔 30ms），实测 " + n);
  ok(n >= 1, "但至少渲染了一次（不能把更新吞掉）");
}

// ---------- 3. cancel：排队中的那一次不再执行 ----------
{
  let n = 0;
  const f = rafThrottle(() => n++, 50);
  f();
  f.cancel();
  await sleep(80);
  ok(n === 0, "cancel() 之后排队中的渲染没有执行（否则会重贴流式光标）");
}

// ---------- 4. cancel 之后仍可继续使用 ----------
{
  let n = 0;
  const f = rafThrottle(() => n++, 0);
  f(); f.cancel();
  await sleep(30);
  f();
  await sleep(30);
  ok(n === 1 && f.cancel, "cancel() 不会把节流器搞坏，后续仍能渲染");
}

console.log("结果：" + pass + " 通过 / " + fail + " 失败");
process.exit(fail ? 1 : 0);
