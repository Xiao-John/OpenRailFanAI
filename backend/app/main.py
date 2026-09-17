"""FastAPI 应用入口。

- /health 健康检查
- /api/chat 三层流水线（意图→抽取→检索→生成）
- 根路径 "/" 托管前端静态页面（frontend/），可直接打开对话界面。

无需登录：对话匿名可用（不引入账户与计费，数据与模型调用全部按需进行）。
"""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

_log = logging.getLogger("railfan.main")

settings = get_settings()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """进程退出时关掉共享的抓取 client（见 `app/tools/_http.py:get_client`）。

    不关会留一条 "Unclosed client session" 告警；这里只做收尾，不做启动预热
    （首个请求自己会懒建连接池）。
    """
    yield
    from app.tools._http import aclose_client

    await aclose_client()


app = FastAPI(
    title="OpenRailFanAI",
    version="0.1.0",
    lifespan=_lifespan,
    # 生产环境默认不暴露接口文档（/docs 会完整列出接口面，且无鉴权），
    # 需要时显式打开 ENABLE_API_DOCS=true 并置于内网/鉴权之后。
    docs_url="/docs" if (settings.enable_api_docs and not settings.is_production) else None,
    redoc_url=None,
    openapi_url="/openapi.json" if (settings.enable_api_docs and not settings.is_production) else None,
)

# 开发用：允许跨域（v1 最简前端可跨域调用）
# TODO: 部署到公网时改为白名单（默认允许跨域便于本地调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态资源压缩（前端未压缩约 130 KB）。
# 回环（Android 一体化）上省下的是"几十微秒"，真正受益的是**局域网部署**：
# 手机连桌面后端跑 WebView 时，第一个页面加载要传 130 KB。
# ⚠️ SSE（text/event-stream）在 Starlette 的排除名单里，不会被压 —— 流式不受影响。
app.add_middleware(GZipMiddleware, minimum_size=1024)


def app_version() -> str:
    """应用版本 —— 唯一来源是仓库根的 VERSION 文件。

    为什么不让前端硬编码：以前界面写死 "v0.6"，而 Android 包的版本是另一个号，
    同时出现两个互不相干的版本号，用户根本无从判断自己装的是哪一版。
    读不到就如实返回 "dev"（从源码直接跑的自建部署就该显示 dev），不要编一个号。
    """
    try:
        v = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
        return v or "dev"
    except OSError:
        return "dev"


@app.get("/health")
def health() -> dict:
    """健康检查。"""
    return {
        "status": "ok",
        "version": app_version(),
        "llm_ready": settings.llm_ready,
        "model": settings.llm_model,
        "auth": "disabled",   # 不需要登录
    }


@app.get("/api/version")
def version() -> dict:
    """应用版本（前端「关于」显示它；Android 包会优先读打包时写入的 build.json）。"""
    return {"version": app_version()}


def _include_routes() -> None:
    from app.api.chat import router as chat_router
    from app.api.providers import router as providers_router

    app.include_router(chat_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")


_include_routes()


# 前端静态托管：默认取仓库根目录下的 frontend/（含 index.html）；
# 打包分发（Android/PyInstaller）时仓库结构不存在，用 FRONTEND_DIR 指定解包后的目录。
_frontend_dir = (
    Path(settings.frontend_dir).expanduser()
    if settings.frontend_dir
    else Path(__file__).resolve().parents[2] / "frontend"
)
if _frontend_dir.is_dir():

    class _NoCacheStatic(StaticFiles):
        """静态资源默认加 `no-cache`（回源校验）。

        为什么必须加：这套前端是**无构建步骤**的（每次安装/更新都是直接替换文件），
        而 URL 跨安装**完全不变**（Android 上端口是刻意固定的，为的是保住本机状态）。
        没有 `Cache-Control` 时浏览器/WebView 会按启发式规则自行缓存，
        于是"我明明重装了、界面却没变"——实测重装后看不到刚修好的界面就是这类问题。
        `no-cache` 是"每次都要回源校验"（不是禁用缓存），本地回环上代价可以忽略。

        分级缓存走 `/v/<stamp>/` 前缀（见 `_versioned_asset`）：只有**带版本戳**的
        URL 才给 `immutable` 长缓存，无戳的依旧回源校验 —— 这样"改了文件却没改版本号"
        不会拿到旧内容。

        顺带：APK 版本的 "关于" 卡片会显示构建标记（打包时写入的 build.json），
        用来直接确认"装的到底是哪一次的包"。
        """

        def file_response(self, *args, **kwargs):      # type: ignore[no-untyped-def]
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    _static = _NoCacheStatic(directory=str(_frontend_dir), html=True)

    _IMMUTABLE = "public, max-age=31536000, immutable"
    _ASSET_REF_RE = re.compile(r'(?P<attr>src|href)="\./(?P<rel>src/[^"?#]+)"')

    def _frontend_stamp() -> str:
        """前端资源版本戳 = `index.html` 与 `src/**` 里**最新的 mtime**（纳秒十六进制）。

        为什么不用 `VERSION`：VERSION 只在发版时变，而开发时改了 JS 却不改版本号，
        拿它当缓存键会**直接命中旧 JS** —— 本项目已经踩过"重装了、界面却没变"。
        mtime 天然"改了就变"，且不需要引入构建步骤。6 次 stat ≈ 几十微秒，不做缓存。
        """
        newest = 0
        for p in [_frontend_dir / "index.html", *(_frontend_dir / "src").rglob("*")]:
            try:
                newest = max(newest, p.stat().st_mtime_ns)
            except OSError:
                continue
        return f"{newest:x}"

    def _stamp_entry_assets(html: str) -> str:
        """把首页里的入口资源（`./src/xxx.js`）指向带版本戳的 URL。"""
        stamp = _frontend_stamp()
        return _ASSET_REF_RE.sub(
            lambda m: f'{m.group("attr")}="./v/{stamp}/{m.group("rel")}"', html
        )

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    def _index_html() -> HTMLResponse:
        """首页：**始终** `no-cache`（回源只读一次本地文件），入口资源带版本戳。"""
        raw = (_frontend_dir / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(_stamp_entry_assets(raw), headers={"Cache-Control": "no-cache"})

    @app.get("/v/{stamp}/{path:path}")
    async def _versioned_asset(stamp: str, path: str, request: Request):
        """带版本戳的前端资源（长缓存）。

        为什么用**路径前缀**而不是给入口加 `?v=`：前端是无构建的 ES 模块图
        （main.js → pages.js → store.js…）。`?v=` 只挂在入口那一个 URL 上，
        被 `import` 的模块拿不到版本号，分级缓存会只对 5 个文件里的 1 个生效；
        而 `/v/<stamp>/src/main.js` 里的相对导入（`./store.js`）会**自动继承**前缀，
        5 个模块一起进长缓存，一行 JS 都不用改写。

        只放行 `src/` 下的资源：这个前缀是给"长缓存"用的，能给它加戳的只有前端资源；
        不限制的话 `index.html` 一类也会走上 immutable（一旦被谁缓存一年就很麻烦）。
        路径安全交给 `StaticFiles.lookup_path`（realpath + commonpath 校验）。
        """
        if not path.startswith("src/"):
            raise HTTPException(status_code=404)
        scope = dict(request.scope)
        scope["path"] = "/" + path
        resp = await _static.get_response(path, scope)
        # 戳对得上 = 内容与首页同代 → 可以长缓存；对不上（旧页面里的旧 URL）仍按回源处理
        resp.headers["Cache-Control"] = _IMMUTABLE if stamp == _frontend_stamp() else "no-cache"
        return resp

    app.mount("/", _static, name="frontend")
else:
    # 不静默失败：打错路径时表现为"主页 404 但 /api 正常"，很难排查
    logging.getLogger("railfan.main").warning(
        "前端静态目录不存在：%s（主页将 404；打包场景请设置 FRONTEND_DIR）", _frontend_dir
    )
