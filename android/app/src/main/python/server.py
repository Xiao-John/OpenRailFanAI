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

# 启动超时（秒）：超过则明确报错，而不是无限等待（上一版正是无限等待，界面永久卡住）
STARTUP_TIMEOUT_S = 90.0


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


def _notify(activity, method: str, *args) -> None:  # noqa: ANN001
    """调用 Java 侧的回调方法。

    **注意调用方式**：Java 对象传进 Python 后是 Chaquopy 的 `JavaObject`，
    要像普通 Python 属性那样直接调用 —— `activity.onServerReady(port)`。
    这里曾误写成 `activity.callAttr(...)`：那是**Java 侧调用 Python 对象**的 API
    （PyObject 的方法），于是**每一个回调都抛 AttributeError 并被吞掉** ——
    后端其实已经正常监听、前端自检也通过，但界面永远停在"调用 server.serve()…"，
    表现出来就是永久卡死。这个 AttributeError 只有真机 logcat 看得到。

    失败只记日志、不打断服务；同时写进日志缓冲，便于界面显示。
    """
    if activity is None:
        return
    try:
        getattr(activity, method)(*args)
    except Exception:  # noqa: BLE001
        _log.exception("回调 Java %s 失败", method)
        _LOG_BUFFER.append(f"!! 回调 Java {method} 失败（界面不会更新）")


def _beat(activity, stage: str) -> None:  # noqa: ANN001
    """把一个**启动阶段**回报给界面。

    这是上一版最缺的东西：真机上卡住时，界面只知道"调用了 serve()"，
    不知道卡在哪一步（导入 app？导入 uvicorn？起服务？），只能靠猜。
    每步都报一次，卡住时最后一条就是答案。
    """
    _log.info("[启动] %s", stage)
    _notify(activity, "onBootStage", stage)


def _ensure_ca_bundle() -> str:
    """让 httpx2 改用**文件形式的 CA 证书束**，而不是平台信任库。

    背景（Android 上实测到的 TLS 故障）：
        `httpx2` 的 `create_ssl_context()` 默认走 `truststore`（读**平台**信任库），
        而 Chaquopy 的 Python 在 Android 上读不到系统 CA —— 于是**所有 HTTPS 请求**
        都失败：
            APIConnectionError ← ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]
            certificate verify failed: unable to get local issuer certificate
        受影响的是 openai SDK 与 mcp-server-12306（两者都依赖 httpx2）；
        我们自己的 `httpx`（12306 抓取）默认用 certifi，不受影响。

    修法：httpx2 会**优先采用** `SSL_CERT_FILE`/`SSL_CERT_DIR`，因此指向一个确实
    存在的证书束文件即可。必须逐个候选检查 `isfile` —— `certifi.where()` 在
    Chaquopy 下可能指向 .imy 归档内的路径（不是真实文件），直接交给
    `ssl.create_default_context(cafile=...)` 一样会失败。

    已显式设置 SSL_CERT_FILE 时不覆盖（尊重使用者自带的 CA）。
    """
    import ssl

    if os.environ.get("SSL_CERT_FILE"):
        return os.environ["SSL_CERT_FILE"]

    candidates: list[str] = []
    try:
        import certifi

        candidates.append(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    try:
        paths = ssl.get_default_verify_paths()
        candidates.extend([paths.cafile or "", paths.openssl_cafile or ""])
    except Exception:  # noqa: BLE001
        pass
    # Chaquopy 的 Python 自带一份证书束，位置随发行版变化，兜底再找一下
    candidates.append(os.path.join(os.path.dirname(ssl.__file__ or ""), "cacert.pem"))

    for cand in candidates:
        if cand and os.path.isfile(cand):
            os.environ["SSL_CERT_FILE"] = cand
            _log.info("TLS 信任库改用证书束文件：%s", cand)
            return cand
    _log.warning("未找到可用的 CA 证书束文件，TLS 校验可能失败（候选：%s）", candidates)
    return ""


def serve(activity=None, webapp_dir: str = "", data_dir: str = "") -> None:
    """启动后端并回报结果。

    activity   —— MainActivity 实例（Chaquopy 的 PyObject）；
    webapp_dir —— Java 侧从 assets 解包出的前端目录（对应 FRONTEND_DIR）；
    data_dir   —— 应用私有可写目录（放 dict.db 等运行期数据）。
    """
    _beat(activity, "选择空闲端口…")
    port = _pick_free_port()

    # 必须在任何 httpx2 客户端创建之前完成（openai SDK / mcp_12306 都会自建客户端）
    _beat(activity, "准备 TLS 信任库…")
    _ensure_ca_bundle()

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
        _beat(activity, "导入 app 包（含依赖兼容层）…")
        import app  # noqa: F401 —— 导入即完成依赖兼容层安装（app/__init__.py）

        from app import _compat

        if _compat.enabled_modules:
            _log.info("已启用依赖替身：%s", _compat.enabled_modules)

        _beat(activity, "导入后端应用 app.main…")
        from app.main import app as asgi_app

        _beat(activity, "导入 uvicorn…")
        import uvicorn

        _beat(activity, "构造 ASGI 配置…")
        config = uvicorn.Config(
            asgi_app, host=HOST, port=port, log_level="info", access_log=False,
        )
        server = uvicorn.Server(config)

        async def run_until_ready() -> None:
            _beat(activity, "启动 uvicorn 服务…")
            task = asyncio.create_task(server.serve())
            # 轮询 server.started；**带超时**：上一版没有超时，若 uvicorn 因为任何原因
            # 既没 started 也没结束，这个循环会永远转下去 —— 界面就永远停在"调用 serve()…"。
            deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_S
            while not server.started and not task.done():
                if asyncio.get_running_loop().time() > deadline:
                    _notify(activity, "onStartupFailed",
                            f"uvicorn 在 {STARTUP_TIMEOUT_S:.0f} 秒内未能开始监听"
                            f"（127.0.0.1:{port}）。\n最近日志：\n{recent_logs()}")
                    raise TimeoutError("uvicorn 启动超时")
                await asyncio.sleep(0.05)
            if task.done() and not server.started:
                await task          # 把启动异常抛到外层统一处理
                return

            _beat(activity, f"服务已监听 127.0.0.1:{port}，执行前端自检…")
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


if __name__ == "__main__":  # 便于在桌面上冒烟测试本入口
    serve(webapp_dir=os.environ.get("FRONTEND_DIR", ""))
