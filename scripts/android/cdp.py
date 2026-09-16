#!/usr/bin/env python3
"""通过 Chrome DevTools Protocol 在真机/模拟器上对 WebView 执行任意 JS。

为什么需要：改一次 Java 探针就得重装一次 APK，而调试布局/状态往往要问十几次问题。
CDP 让"在真实设备上执行 JS"变成一条命令，和桌面浏览器 DevTools 的控制台等价。

前置：debug 构建（MainActivity 只在 debuggable 构建里开启 WebView 调试）。

用法：
    python3 scripts/android/cdp.py <js表达式>
    python3 scripts/android/cdp.py --eval-file /tmp/probe.js
    python3 scripts/android/cdp.py --targets          # 列出可调试页面
    python3 scripts/android/cdp.py --tap-sel "#menu-btn"   # 真的点一下（见 tap()）
    python3 scripts/android/cdp.py --screenshot /tmp/x.png # 由渲染器截图

示例：
    python3 scripts/android/cdp.py "document.querySelectorAll('script').length"
    python3 scripts/android/cdp.py "JSON.stringify([...document.querySelectorAll('*')].slice(0,5).map(e=>e.className))"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADB = ROOT / ".android-build/sdk/platform-tools/adb"
APP_ID = "org.openrailfanai.app.debug"      # debug 构建带 .debug 后缀
CDP_PORT = 9222


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def discover_socket(app_id: str | None = None) -> str:
    """找到**当前进程**的 WebView devtools socket 名。

    必须按 PID 精确匹配，不能只扫 /proc/net/unix 取第一个匹配 —— 被强杀或重启过的
    进程会留下僵尸 socket，devtools 里那个"旧页面"仍然可连。本项目就踩过：
    界面明明已更新，CDP 却一直显示旧版 UI（因为连到了上一代进程残留的 WebView，
    其 URL 里的后端端口都不是当前的）。
    """
    target = app_id or APP_ID
    pid = sh(str(ADB), "shell", "pidof", target)
    if pid:
        sock = f"webview_devtools_remote_{pid.split()[0]}"
        unix = sh(str(ADB), "shell", "cat", "/proc/net/unix")
        if sock in unix:
            return sock
    # 退路：目标进程没有 WebView 时，才去扫（并按 PID 从大到小取最新的一个）
    unix = sh(str(ADB), "shell", "cat", "/proc/net/unix")
    socks = sorted(
        {m.group(0) for m in re.finditer(r"webview_devtools_remote_(\d+)", unix)},
        key=lambda s: int(s.rsplit("_", 1)[1]),
        reverse=True,
    )
    if socks:
        return socks[0]
    raise SystemExit(
        f"未找到 WebView 调试 socket（包 {target} 是否已启动？是否为 debug 构建？）"
    )


def fetch_targets() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=10) as r:
        return json.load(r)


async def evaluate(ws_url: str, expr: str, timeout_s: float = 30.0) -> str:
    import websockets  # 由后端 venv 提供

    # 必须显式 proxy=None：websockets 默认会读代理设置，而 macOS 的 urllib.getproxies()
    # 会把**系统代理**（本机是 SOCKS 127.0.0.1:7897）也算进去，于是连本地 CDP 端口
    # 也会被塞进 SOCKS（随后因缺 python-socks 而报 ImportError）。
    # 调试通道走 127.0.0.1，绝不该经过代理。
    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024, proxy=None) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expr,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_s))
            if msg.get("id") == 1:
                result = msg.get("result", {})
                if "exceptionDetails" in result:
                    return "JS 异常: " + json.dumps(result["exceptionDetails"], ensure_ascii=False)[:400]
                if "exceptionDetails" in result:
                    return "JS 异常: " + json.dumps(
                        result["exceptionDetails"].get("text", result["exceptionDetails"]),
                        ensure_ascii=False)
                return json.dumps(result.get("result", {}).get("value"), ensure_ascii=False, indent=2)


async def screenshot(ws_url: str, out_path: str) -> str:
    """向**渲染器**要一张截图。

    注意与 `adb exec-out screencap` 的区别：后者抓的是模拟器合成器的最后一帧，
    在软件渲染（-gpu swiftshader_indirect）下可能明显滞后 —— 实测拿到过
    "引导条只渲染了一半、页头按钮还没画出来"的中间态，据此判断布局会得出错误结论
    （本项目踩过）。CDP 的 Page.captureScreenshot 由渲染器直接给出，与
    DOM 测量同一时刻，二者交叉验证才可靠。
    """
    import base64

    import websockets

    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024, proxy=None) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Page.captureScreenshot",
            "params": {"format": "png", "captureBeyondViewport": False},
        }))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg.get("id") == 1:
                Path(out_path).write_bytes(base64.b64decode(msg["result"]["data"]))
                return f"已保存 {out_path}"


async def tap(ws_url: str, x: float, y: float, timeout_s: float = 30.0) -> str:
    """在页面坐标 (x, y) 派发一次真实点击，走**渲染器的输入管线**。

    为什么不用 `adb shell input tap`：在本项目的 arm64 API 35 模拟器上，adb 注入的
    触摸事件**根本没进 WebView** —— 连点最底部输入框都无法聚焦（实测 activeElement
    始终是 BODY），而同一时刻 DOM 的 elementFromPoint 完全正常。曾据此误判出"顶部
    105px 死区"这种不存在的结论（实际是点击落在安全区留白上 + 事件压根没送达）。

    为什么用鼠标事件而不是 touch：实测 WebView 的 `Input.dispatchTouchEvent` 是
    **静默失效**的 —— 不回包、不报错、页面毫无反应；`Input.dispatchMouseEvent`
    正常回 `{}` 并真正触发 click（点 ☰ 后侧栏 transform 由 translateX(-326.4px)
    变为 none）。鼠标事件同样经过命中测试，足以验证"这个按钮能不能点到"。
    """
    import websockets

    async def send(ws, msg_id: int, method: str, params: dict) -> dict:
        await ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_s))
            if msg.get("id") == msg_id:
                return msg

    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024, proxy=None) as ws:
        for msg_id, kind in ((1, "mousePressed"), (2, "mouseReleased")):
            resp = await send(ws, msg_id, "Input.dispatchMouseEvent",
                              {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})
            if "error" in resp:
                return f"点击失败：{json.dumps(resp['error'], ensure_ascii=False)}"
            await asyncio.sleep(0.05)
    return f"已点击 ({x:.1f}, {y:.1f})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("expr", nargs="?", help="要执行的 JS 表达式")
    ap.add_argument("--eval-file", help="从文件读取 JS")
    ap.add_argument("--targets", action="store_true", help="列出可调试页面")
    ap.add_argument("--tap", metavar="X,Y", help="在页面坐标处派发真实点击（见 tap() 注释）")
    ap.add_argument("--tap-sel", metavar="CSS选择器",
                    help="先解析元素中心再点击；比手算坐标可靠（布局一变坐标就错）")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="等待求值结果的上限（秒）；含长 await 的脚本要调大")
    ap.add_argument("--screenshot", metavar="OUT.png",
                    help="由渲染器截图（比 adb screencap 可靠，见函数注释）")
    args = ap.parse_args()

    if not ADB.exists():
        print(f"找不到 adb：{ADB}", file=sys.stderr)
        return 1

    sock = discover_socket()
    subprocess.run([str(ADB), "forward", f"tcp:{CDP_PORT}",
                    f"localabstract:{sock}"], capture_output=True, text=True)

    targets = fetch_targets()
    if args.targets:
        for t in targets:
            print(f"  {t.get('type'):<10} {t.get('title', '')[:60]}  {t.get('url', '')[:70]}")
        return 0

    page = next((t for t in targets if t.get("type") == "page"), None)
    if page is None:
        print("没有可用的 page 目标。用 --targets 看看当前有什么。", file=sys.stderr)
        return 1

    if args.screenshot:
        print(asyncio.run(screenshot(page["webSocketDebuggerUrl"], args.screenshot)))
        return 0

    if args.tap or args.tap_sel:
        if args.tap_sel:
            center = json.loads(asyncio.run(evaluate(
                page["webSocketDebuggerUrl"],
                "(()=>{const e=document.querySelector(%s); if(!e) return null;"
                " const r=e.getBoundingClientRect();"
                " return [(r.left+r.right)/2,(r.top+r.bottom)/2];})()"
                % json.dumps(args.tap_sel),
                args.timeout)))
            if not center:
                print(f"找不到元素：{args.tap_sel}", file=sys.stderr)
                return 1
            x, y = center
        else:
            try:
                x, y = (float(v) for v in args.tap.split(","))
            except ValueError:
                print("--tap 需要形如 120,340 的坐标", file=sys.stderr)
                return 1
        print(asyncio.run(tap(page["webSocketDebuggerUrl"], x, y, args.timeout)))
        return 0

    expr = Path(args.eval_file).read_text(encoding="utf-8") if args.eval_file else args.expr
    if not expr:
        ap.print_help()
        return 1

    print(asyncio.run(evaluate(page["webSocketDebuggerUrl"], expr, args.timeout)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
