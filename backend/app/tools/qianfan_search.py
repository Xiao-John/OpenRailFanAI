"""百度 AI 搜索（千帆官方 API）客户端 —— `web_search` 接口。

为什么要有这个模块（2026-10-06 实测结论）
----------------------------------------
机位这类**众包数据**必须靠批量检索才能建库，而网页版搜索引擎对批量取数会反爬：

- 网页版百度：累计约 70 次检索即返回 `wappass.baidu.com/.../captcha/tuxing_v2.html`
  **图形验证码**（等 60s 仍如此），且结果 URL 全是 `baidu.com/link?url=` **跳转链** ——
  拿不到真实域名，无法判定信源、也无法稳定抓正文；
- 网页版 Bing：不返回验证码，但会**返回退化结果**（查"成昆铁路"返回汉字「成」的字典词条）。

官方 API 是正规通道，且比网页版更适配本项目：

1. **不触发反爬**：官方额度内正常调用；官方文档注明每月免费 1500 次（按天发放），超额按量后付费；
2. **`url` 是真实网页地址**（不是跳转链）→ 能判定信源、能作为稳定去重键；
3. **官方支持 `search_filter.match.site` 定向站点** —— `site:` 在网页版引擎上实测无效，
   只有 API 才真的生效。这对机位是决定性的：直接限定 B站/知乎/贴吧，检索质量完全不同
   （实测可直接取到《上海铁路枢纽三十大著名机位推荐》《全国各地热门拍车机位大汇总》等整篇攻略）；
4. 带 `date`（发布时间）与 `authority_score`（权威性评分），可用于时效与信源质量判断。

**注意**：`content` 官方注明"显示 2000 字以内的相关信息原文片段（当前与 snippet 内容一致）"，
即它是**较长摘要、不是全文**；需要完整点位描述时仍要用 `url` 抓一次正文。

接口要点（摘自官方文档）
------------------------
- `POST https://qianfan.baidubce.com/v2/ai_search/web_search`
- `Authorization: Bearer <API Key>`，`Content-Type: application/json`
- body：`messages[{role,content}]` 必填；`content` **限 72 字符**（一汉字占 2 字符），超长只取前 72
- 常用可选：`search_source`（固定 `baidu_search_v2`）、`resource_type_filter[{type,top_k}]`、
  `search_filter.match.site[]`、`search_recency_filter`（week/month/semiyear/year）、
  `edition`（standard/turbo）
- 响应：`references[]`，含 `id/type/title/url/date/snippet/content/website/web_anchor/icon/
  authority_score/rerank_score/web_extensions`；异常时返回 `code` + `message`
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

_log = logging.getLogger("railfan.qianfan")

# 官方限定的 query 字符上限（一汉字占 2 字符）
QUERY_HARD_LIMIT = 72


class QianfanUnavailable(RuntimeError):
    """未配置 Key 或调用失败。调用方据此降级，不得让异常穿透。"""


def enabled() -> bool:
    return bool((get_settings().qianfan_api_key or "").strip())


def _clip_query(query: str, limit: int = QUERY_HARD_LIMIT) -> str:
    """按**字符**截断（一汉字算 2），与官方口径一致。

    官方说明"输入过长的内容时，只取前 72 个字符检索"，这里主动截断并在调用方留痕，
    避免"我以为搜的是整句、其实只搜了前半句"这种静默降级。
    """
    q = (query or "").strip()
    if not q:
        return ""
    if limit <= 0:
        return q
    if len(q) * 2 <= limit:      # 全按汉字最坏情况估算已够短
        return q
    # 逐字符累计（汉字 2、其余 1），到上限即止
    out, used = [], 0
    for ch in q:
        cost = 2 if ord(ch) > 0x2E7F else 1
        if used + cost > limit:
            break
        out.append(ch)
        used += cost
    return "".join(out)


async def web_search(
    query: str,
    *,
    top_k: int | None = None,
    sites: list[str] | None = None,
    recency: str | None = None,
    edition: str | None = None,
) -> list[dict]:
    """调用官方 `web_search`，返回归一化后的结果列表。

    返回每项：`{title, url, snippet, content, date, website, authority_score, type, site}`。
    未配置 Key 或调用失败一律抛 `QianfanUnavailable`（由调用方决定降级路径）。
    """
    settings = get_settings()
    key = (settings.qianfan_api_key or "").strip()
    if not key:
        raise QianfanUnavailable("未配置 QIANFAN_API_KEY")

    clipped = _clip_query(query, settings.qianfan_query_max_chars)
    if not clipped:
        raise QianfanUnavailable("查询词为空")
    if clipped != (query or "").strip():
        _log.info("query 超官方 %d 字符上限，已截断：%r", settings.qianfan_query_max_chars, clipped)

    body: dict = {
        "messages": [{"content": clipped, "role": "user"}],
        "search_source": settings.qianfan_search_source,
        "resource_type_filter": [{"type": "web",
                                  "top_k": int(top_k or settings.qianfan_top_k)}],
    }
    if sites:
        body["search_filter"] = {"match": {"site": [str(s) for s in sites if s]}}
    if recency:
        body["search_recency_filter"] = str(recency)
    if edition or settings.qianfan_edition:
        body["edition"] = str(edition or settings.qianfan_edition)

    from app.tools._http import format_error

    try:
        async with httpx.AsyncClient(timeout=max(15.0, settings.http_timeout),
                                     trust_env=False) as client:
            resp = await client.post(
                settings.qianfan_search_url,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        code = getattr(getattr(e, "response", None), "status_code", "?")
        # 401/403 多为 Key 无效；429 为额度/频率问题 —— 都要能一眼看出
        raise QianfanUnavailable(f"HTTP {code}") from e
    except Exception as e:  # noqa: BLE001
        raise QianfanUnavailable(format_error(e)) from e

    # 业务错误：官方在 200 响应里用 code + message 表示
    if isinstance(data, dict) and data.get("code"):
        raise QianfanUnavailable(f"{data.get('code')}: {str(data.get('message'))[:120]}")

    return normalize(data)


def normalize(data: dict) -> list[dict]:
    """把官方 `references[]` 归一化成内部结果结构。

    字段陷阱（官方/第三方实测记录）：`website` 可能是空串或字面量"无"；
    `author_info` 在非百家号结果里也存在但为空对象 —— 故一律做兜底。
    """
    out: list[dict] = []
    for x in (data or {}).get("references") or []:
        if not isinstance(x, dict):
            continue
        url = str(x.get("url") or "").strip()
        if not url:
            continue
        site = x.get("website")
        if site in (None, "", "无"):
            site = ""
        we = x.get("web_extensions") or {}
        author = (we.get("author_info") or {}) if isinstance(we, dict) else {}
        out.append({
            "title": str(x.get("title") or ""),
            "url": url,
            "snippet": str(x.get("snippet") or ""),
            # content 是较长片段（≤2000 字），优先于 snippet
            "content": str(x.get("content") or ""),
            "date": str(x.get("date") or ""),
            "website": str(site or ""),
            "author": str((author or {}).get("name") or ""),
            "authority_score": x.get("authority_score"),
            "rerank_score": x.get("rerank_score"),
            "type": str(x.get("type") or "web"),
            "site": str(site or ""),
        })
    return out
