"""Android 一体化版本的 Python 入口。

职责：
  1. 就位运行期配置（前端静态目录、工作目录、生产语义）；
  2. 在 127.0.0.1 的**空闲端口**上以 uvicorn 启动 `app.main:app`（仓库里同一个后端）；
  3. **自检**：起服务后立刻回环请求 `/` 与 `/src/main.js`，确认前端真的被托管；
  4. 把端口与自检结论回报给 Java 侧，由其用系统 WebView 加载页面。

为什么必须自检（这一条来自真机教训）：
    首版在 WebView 加载失败时只呈现一片白屏，而真机排障拿不到 logcat。
    自检把"服务起来了但前端没托管 / 静态资源 404"这类问题**在加载页面前**就变成
    一条可读的结论，而不是让用户对着白屏。实测它能直接抓到"assets 解包时把目录
    结构拍平导致 ./src/main.js 404"这种缺陷。
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import socket
import traceback

# ---- 日志缓冲：出问题时把最后若干行显示到屏幕（真机上没法看 logcat）----
_LOG_BUFFER: collections.deque[str] = collections.deque(maxlen=120)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_BUFFER.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger().addHandler(_BufferHandler())
_log = logging.getLogger("railfan.android")

HOST = "127.0.0.1"
# WebView 首屏真正依赖的资源：首页 + 入口脚本。
# 入口脚本单独检查是刻意的 —— 静态资源 404 不会让 WebView 报错，只会让页面"看起来白"。
REQUIRED_PATHS = ("/", "/src/main.js")


def recent_logs(limit: int = 40) -> str:
    lines = list(_LOG_BUFFER)[-limit:]
    return "\n".join(lines)


def _pick_free_port() -> int:
    """让内核分配一个空闲端口（固定端口在真机上会与其它应用或残留进程冲突）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def _self_check(port: int) -> tuple[bool, str]:
    """回环请求首页与入口脚本，返回 (是否通过, 结论文本)。"""
    import http.client

    results: list[str] = []
    ok = True
    for path in REQUIRED_PATHS:
        try:
            conn = http.client.HTTPConnection(HOST, port, timeout=15)
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read(4000)
            conn.close()
            status = resp.status
            size = len(body)
            results.append(f"{path} → HTTP {status}（{size}B）")
            if status != 200:
                ok = False
            elif path.endswith(".js") and b"import" not in body and b"function" not in body:
                # 200 但内容不像脚本：多半是被回退到了 index.html（SPA fallback）或空文件
                ok = False
                results.append(f"  ⚠ {path} 返回 200 但内容不像 JS（可能被静态托管回退成首页）")
        except Exception as e:  # noqa: BLE001
            ok = False
            results.append(f"{path} → 请求失败：{type(e).__name__}: {e}")
    return ok, "；".join(results)


def serve(activity=None, webapp_dir: str = "", data_dir: str = "") -> None:
    """启动后端并回报结果。

    activity   —— MainActivity 实例（Chaquopy 的 PyObject）；
    webapp_dir —— Java 侧从 assets 解包出的前端目录（对应 FRONTEND_DIR）；
    data_dir   —— 应用私有可写目录（放 dict.db 等运行期数据）。
    """
    port = _pick_free_port()

    # 配置读取发生在 import 时，环境变量必须在此前设好
    if webapp_dir:
        os.environ["FRONTEND_DIR"] = webapp_dir
    os.environ.setdefault("APP_ENV", "production")

    # 工作目录切到可写目录：settings 里的相对路径（如 DICT_DB_PATH=data/dict.db）
    # 都相对 CWD 解析，而 APK 内的默认 CWD 不可写。
    if data_dir:
        try:
            os.chdir(data_dir)
        except OSError:
            _log.warning("切换到数据目录失败：%s", data_dir, exc_info=True)

    try:
        import app  # noqa: F401 —— 导入即完成依赖兼容层安装（app/__init__.py）

        from app import _compat

        if _compat.enabled_modules:
            _log.info("已启用依赖替身：%s", _compat.enabled_modules)

        from app.main import app as asgi_app

        import uvicorn

        config = uvicorn.Config(
            asgi_app, host=HOST, port=port, log_level="info", access_log=False,
        )
        server = uvicorn.Server(config)

        async def run_until_ready() -> None:
            task = asyncio.create_task(server.serve())
            # 轮询 server.started，确保端口已在监听后再继续
            while not server.started and not task.done():
                await asyncio.sleep(0.05)
            if task.done() and not server.started:
                await task          # 把启动异常抛到外层统一处理
                return

            _log.info("本地后端已监听 http://%s:%d", HOST, port)
            ok, detail = await asyncio.to_thread(_self_check, port)
            if not ok:
                # 自检不过就直接把结论摆到用户面前，而不是让他对着白屏猜
                _notify(activity, "onStartupFailed",
                        f"服务已在 127.0.0.1:{port} 启动，但前端自检未通过：\n{detail}\n\n"
                        f"FRONTEND_DIR={webapp_dir}\n\n最近日志：\n{recent_logs()}")
                # 仍然继续提供服务，便于用户重试/进一步排查
            else:
                _log.info("自检通过：%s", detail)
            _notify(activity, "onServerReady", port, detail)
            await task

        asyncio.run(run_until_ready())
    except Exception:
        detail = traceback.format_exc()
        _log.error("后端启动失败:\n%s", detail)
        _notify(activity, "onStartupFailed", f"{detail}\n\n最近日志：\n{recent_logs()}")
        raise


def _notify(activity, method: str, *args) -> None:  # noqa: ANN001
    """回调 Java；失败只记日志，不打断服务本身。"""
    if activity is None:
        return
    try:
        activity.callAttr(method, *args)
    except Exception:  # noqa: BLE001
        _log.exception("回调 Java %s 失败", method)


if __name__ == "__main__":  # 便于在桌面上冒烟测试本入口
    serve(webapp_dir=os.environ.get("FRONTEND_DIR", ""))
