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


@app.get("/health")
def health() -> dict:
    """健康检查。"""
    return {
        "status": "ok",
        "llm_ready": settings.llm_ready,
        "model": settings.llm_model,
        "auth": "disabled",   # 不需要登录
    }


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
    app.mount(
        "/",
        StaticFiles(directory=str(_frontend_dir), html=True),
        name="frontend",
    )
else:
    # 不静默失败：打错路径时表现为"主页 404 但 /api 正常"，很难排查
    logging.getLogger("railfan.main").warning(
        "前端静态目录不存在：%s（主页将 404；打包场景请设置 FRONTEND_DIR）", _frontend_dir
    )
