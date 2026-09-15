"""Android 一体化版本的 Python 入口。

职责：
  1. 就位运行期配置（前端静态目录、禁用无关的文件日志）；
  2. 在 127.0.0.1 的**空闲端口**上以 uvicorn 启动 `app.main:app`（仓库里的同一个后端）；
  3. 把端口回报给 Java 侧，由其用系统 WebView 加载页面。

与 PoC 版的区别：这里加载的是**真实后端**（backend/app），不再是内联的最小应用。

为什么是"起本地 HTTP 服务 + WebView 访问 127.0.0.1"而不是 file:// 或
WebViewAssetLoader：前端要用 fetch/SSE 调 /api/*，需要真实 origin；
同源加载还让前端的相对路径（window.__API_BASE__ 为空串）无需任何改动即可工作。
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import traceback

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("railfan.android")

# 与 WebView 侧约定的主机（只监听回环，不对外暴露）
HOST = "127.0.0.1"


def _pick_free_port() -> int:
    """让内核分配一个空闲端口。

    不用固定端口：真机上固定端口会与其它应用或上次未退出的进程冲突，
    而"启动即失败"对用户完全不可诊断。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def serve(activity=None, webapp_dir: str = "", data_dir: str = "") -> None:
    """启动后端。

    activity   —— MainActivity 实例（Chaquopy 的 PyObject），用于回调 onServerReady；
    webapp_dir —— Java 侧从 assets 解包出来的前端静态目录（对应 FRONTEND_DIR）；
    data_dir   —— 应用私有可写目录，用于放 dict.db 等运行期数据。
    """
    port = _pick_free_port()

    # 后端读取配置是在 import 时发生的，所以环境变量必须在此之前设好
    if webapp_dir:
        os.environ["FRONTEND_DIR"] = webapp_dir
    if data_dir:
        os.environ.setdefault("RAILFAN_DATA_DIR", data_dir)
        # 工作目录切到可写目录：settings 里的相对路径（如 DICT_DB_PATH=data/dict.db）
        # 都相对 CWD 解析，而 APK 内的默认 CWD 不可写。
        try:
            os.chdir(data_dir)
        except OSError:
            _log.warning("切换到数据目录失败：%s", data_dir, exc_info=True)

    # 生产语义：Android 包不该暴露 /docs（未鉴权会完整列出接口面）
    os.environ.setdefault("APP_ENV", "production")

    try:
        import app  # noqa: F401 —— 导入即完成依赖兼容层安装（app/__init__.py）

        from app import _compat

        if _compat.enabled_modules:
            _log.info("已启用依赖替身：%s", _compat.enabled_modules)

        from app.main import app as asgi_app

        import uvicorn

        config = uvicorn.Config(
            asgi_app,
            host=HOST,
            port=port,
            log_level="info",
            access_log=False,          # 真机上日志量大且无价值
        )
        server = uvicorn.Server(config)

        async def run_until_ready() -> None:
            task = asyncio.create_task(server.serve())
            # 轮询 server.started，确保端口**已在监听**后再通知 Java，
            # 否则 WebView 可能先于监听发起请求而白屏。
            while not server.started and not task.done():
                await asyncio.sleep(0.05)
            if task.done() and not server.started:
                await task              # 把启动异常抛出到外层统一处理
                return
            _log.info("本地后端已监听 http://%s:%d", HOST, port)
            _notify(activity, "onServerReady", port)
            await task

        asyncio.run(run_until_ready())
    except Exception:
        detail = traceback.format_exc()
        _log.error("后端启动失败:\n%s", detail)
        _notify(activity, "onStartupFailed", detail)
        raise


def _notify(activity, method: str, arg) -> None:  # noqa: ANN001
    """回调 Java；失败只记日志，不打断服务本身。"""
    if activity is None:
        return
    try:
        activity.callAttr(method, arg)
    except Exception:  # noqa: BLE001
        _log.exception("回调 Java %s 失败", method)


if __name__ == "__main__":  # 便于在桌面上冒烟测试本入口
    serve(webapp_dir=os.environ.get("FRONTEND_DIR", ""))
