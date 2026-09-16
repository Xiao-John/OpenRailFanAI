"""FastAPI 应用入口。

- /health 健康检查
- /api/chat 三层流水线（意图→抽取→检索→生成）
- 根路径 "/" 托管前端静态页面（frontend/），可直接打开对话界面。

无需登录：对话匿名可用（不引入账户与计费，数据与模型调用全部按需进行）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

_log = logging.getLogger("railfan.main")

settings = get_settings()


app = FastAPI(
    title="OpenRailFanAI",
    version="0.1.0",
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
        """给静态资源加 `no-cache`。

        为什么必须加：这套前端是**无构建步骤**的（每次安装/更新都是直接替换文件），
        而 URL 跨安装**完全不变**（Android 上端口是刻意固定的，为的是保住本机状态）。
        没有 `Cache-Control` 时浏览器/WebView 会按启发式规则自行缓存，
        于是"我明明重装了、界面却没变"——实测重装后看不到刚修好的界面就是这类问题。
        `no-cache` 是"每次都要回源校验"（不是禁用缓存），本地回环上代价可以忽略。

        顺带：APK 版本的 "关于" 卡片会显示构建标记（打包时写入的 build.json），
        用来直接确认"装的到底是哪一次的包"。
        """

        def file_response(self, *args, **kwargs):      # type: ignore[no-untyped-def]
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    app.mount(
        "/",
        _NoCacheStatic(directory=str(_frontend_dir), html=True),
        name="frontend",
    )
else:
    # 不静默失败：打错路径时表现为"主页 404 但 /api 正常"，很难排查
    logging.getLogger("railfan.main").warning(
        "前端静态目录不存在：%s（主页将 404；打包场景请设置 FRONTEND_DIR）", _frontend_dir
    )
