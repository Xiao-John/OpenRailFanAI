"""供应商路由：列出可用供应商 + 测试自定义供应商连通性。

社区版要求"用户自备 Key"，因此前端需要：
1. `GET /api/providers` —— 拿到全部供应商（含内置目录）的**脱敏**视图，
   用于渲染下拉框：只告诉前端"有没有配 Key"，**永不返回 Key 本身**。
2. `POST /api/providers/test` —— 「测试连接」。用户填完 base_url/Key/模型后
   点一下就能知道能不能用、走的是哪种方言、有哪些模型可选。

安全（重要）
    `base_url` 与 `api_key` 都来自请求体，属于**用户可控的出站目标**：
    - SSRF：默认只允许公网地址；`LLM_ALLOW_PRIVATE_BASE_URL=true` 才放行内网
      （本地 Ollama/LM Studio 需要）。生产环境（APP_ENV=production）下强制校验。
    - 泄密：响应只回"是否可用 + 方言 + 延迟 + 脱敏诊断"，**不回显 Key**，
      Key 也不写日志（`ChatRequest.api_key` 已设 repr=False）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import client as llm_client
from app.llm import providers as providers_mod

_log = logging.getLogger("railfan.api.providers")

router = APIRouter(tags=["providers"])


class ProviderTestRequest(BaseModel):
    """测试连接入参：可以不选 id 而直接给 base_url/api_key（临时自定义供应商）。"""

    provider: str | None = Field(None, description="已配置的供应商 id；与 base_url 二选一")
    base_url: str | None = Field(None, description="自定义 base_url")
    api_key: str | None = Field(None, repr=False, description="该供应商的 Key（仅本次探测使用，不落库不写日志）")
    model: str | None = Field(None, description="要测试的模型名")
    api: str | None = Field(None, description="auto / chat_completions / responses")


def _guard_base_url(provider) -> None:
    """校验出站目标，阻断把请求指向内网/元数据地址（SSRF）。

    策略实现在 `providers.guard_request_base_url`，与 `/api/chat` 的 LLM 出站路径**共用**：
    两处各写一份迟早会漂移，留下没被守卫的路径（这正是最初的漏洞形态）。
    """
    try:
        providers_mod.guard_request_base_url(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/providers")
async def list_providers() -> dict:
    """列出全部供应商（脱敏）与当前生效者，供前端渲染选择器。"""
    settings = get_settings()
    try:
        items = providers_mod.list_public(settings)
    except ValueError as e:
        # 供应商配置写错时不 500：把错误如实带回前端，用户才知道去哪儿改
        return {"active": "", "providers": [], "config_error": str(e)}

    try:
        active = providers_mod.default_provider_id(settings)
    except ValueError:
        active = ""
    return {
        "active": active,
        "llm_ready": settings.llm_ready,
        "mock": settings.llm_mock,
        "allow_private_base_url": settings.llm_allow_private_base_url,
        "dialects": list(providers_mod.DIALECTS),
        "providers": items,
    }


@router.post("/providers/models")
async def list_provider_models(req: ProviderTestRequest) -> dict:
    """拉取某供应商的可用模型清单（用于设置页的模型下拉框）。

    用 POST 而不是 GET：Key 可能由前端临时提供（BYOK），放进 query string 会进
    访问日志/浏览器历史。请求体与 /providers/test 同形，前端两处可复用同一份参数。
    """
    overrides = {
        k: v
        for k, v in {
            "base_url": req.base_url,
            "api_key": req.api_key,
            "model": req.model,
            "api": req.api,
        }.items()
        if v
    }
    try:
        provider = providers_mod.resolve_provider(req.provider, overrides)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _guard_base_url(provider)

    _log.info("拉取模型清单：id=%s base_url=%s has_key=%s",
              provider.id, provider.base_url, bool(provider.api_key))
    return await llm_client.list_models(provider)


@router.post("/providers/test")
async def test_provider(req: ProviderTestRequest) -> dict:
    """测试一个供应商能否真正跑通（含方言自动探测与模型清单）。"""
    overrides = {
        k: v
        for k, v in {
            "base_url": req.base_url,
            "api_key": req.api_key,
            "model": req.model,
            "api": req.api,
        }.items()
        if v
    }

    try:
        provider = providers_mod.resolve_provider(req.provider, overrides)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _guard_base_url(provider)

    _log.info(
        "测试供应商：id=%s base_url=%s api=%s model=%s has_key=%s",
        provider.id, provider.base_url, provider.api, provider.model, bool(provider.api_key),
    )
    return await llm_client.probe_provider(provider)
