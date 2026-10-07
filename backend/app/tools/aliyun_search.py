"""阿里云 CleverSee / IQS 搜索客户端（统一搜索 + 网页解析）。

为什么用它（2026-10-06 实测）
-----------------------------
1. **搜索即带正文**：`contents.mainText=True` 时每条结果直接带 300–3200 字**原文正文**——
   本项目的 `build_photo_spots.py` 原先要自己抓网页（`fetch_body`），而自建抓取实测
   知乎 403、百度百科 403 是常态。用官方接口后**这一步整个不需要了**，
   也顺带消掉了 SSRF 校验与"抓不到"的降级分支。
2. **五个引擎 + 独立额度**（控制台口径：各 1000 次免费）：国内版 LiteBasic、国内版 Auto、
   国际版 GlobalBasic、国际版 GlobalAdvanced、通用版 Generic。**额度互相独立**，
   一家用尽可切下一家 —— 这正是"百家饭"要的效果。
3. **QPS 高**：LiteBasic 10 QPS、ReadPage 5 QPS（控制台标注），可并发建库。
4. `CNLiteBasic` 单次最多 **50 条**（Generic 只有 10 条），建库首选。
5. 结果带 `hostAuthorityScore` / `websiteAuthorityScore` / `rerankScore` / `publishedTime`，
   比只有标题摘要的通道更适合做质量闸门。

接口
----
- 搜索：`POST https://cloud-iqs.aliyuncs.com/search/unified`
  `Authorization: Bearer <API Key>`
  body：`query`(1–500 字符，官方建议 ≤30)、`engineType`、`contents{mainText,summary,rerankScore,...}`、
  `advancedParams{numResults, startPublishedDate, endPublishedDate}`、`timeRange`、`category`
- 网页解析：`POST https://cloud-iqs.aliyuncs.com/readpage/basic`，body `{"url": ...}`，
  响应 `data.text` 为正文

计费（实测 `costCredits`）
--------------------------
- 搜索本身：`CNLiteBasic` 不计（0）；`Generic`/`CNAuto` 各 1；`GenericAdvanced` 搜索 1 + 增值 1
- `contents.summary=True` **单独计 1 个增值 credit**；**`mainText` 不计费** ——
  故建库默认**不开 summary**，正文照样有。
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

_log = logging.getLogger("railfan.aliyun")

# 控制台里的 5 个联网搜索引擎（额度各自独立）
ENGINES = {
    "lite": "CNLiteBasic",          # 国内版 LiteBasic：1–50 条，不计搜索 credit，建库首选
    "auto": "CNAuto",               # 国内版 Auto：1–10 条，智能信源路由
    "global_basic": "GlobalBasic",  # 国际版 GlobalBasic
    "global_advanced": "GlobalAdvanced",  # 国际版 GlobalAdvanced：1–20 条，全球深度检索
    "generic": "Generic",           # 通用版 Generic：10 条
}
# 建库默认优先级：条数多、消耗低、境内内容优先
ENGINE_PRIORITY = ("lite", "auto", "generic", "global_basic", "global_advanced")

_READPAGE_BASIC = "/readpage/basic"
_SEARCH = "/search/unified"
# query 官方上限 500 字符，但官方建议 ≤30 以求效果；这里给一个宽松的硬上限
QUERY_HARD_LIMIT = 200


class AliyunUnavailable(RuntimeError):
    """未配置 Key 或调用失败。调用方据此降级，不得让异常穿透。"""


class AliyunQuotaExhausted(AliyunUnavailable):
    """额度用尽（用户已在控制台**关闭"用尽自动转付费"** → 用尽后返回 403）。

    为什么要单独一个异常：建库会连发几百条查询，若额度中途用尽，
    剩下的查询会**全部 403 空跑**（浪费几分钟、刷满日志，且每条都记为"失败"污染统计）。
    调用方应据此**立即中止整轮**并如实报告进度，而不是继续跑完。
    """


def enabled() -> bool:
    return bool((get_settings().aliyun_iqs_api_key or "").strip())


def _base() -> str:
    return (get_settings().aliyun_iqs_base_url or "").rstrip("/")


def _headers() -> dict:
    key = (get_settings().aliyun_iqs_api_key or "").strip()
    if not key:
        raise AliyunUnavailable("未配置 ALIYUN_IQS_API_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def search(
    query: str,
    *,
    engine: str = "lite",
    num_results: int = 50,
    main_text: bool = True,
    summary: bool = False,
    time_range: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """统一搜索。返回归一化结果列表。

    `main_text=True` 时每条带 `content`（原文正文），**建库据此免去二次抓取**。
    未配置 Key / 调用失败 / 业务报错一律抛 `AliyunUnavailable`。
    """
    if not enabled():
        raise AliyunUnavailable("未配置 ALIYUN_IQS_API_KEY")

    q = (query or "").strip()
    if not q:
        raise AliyunUnavailable("查询词为空")
    if len(q) > QUERY_HARD_LIMIT:
        q = q[:QUERY_HARD_LIMIT]
        _log.info("query 超 %d 字符，已截断", QUERY_HARD_LIMIT)

    engine_type = ENGINES.get(engine, engine if engine in ENGINES.values() else "CNLiteBasic")
    contents: dict = {}
    if main_text:
        contents["mainText"] = True
    if summary:
        contents["summary"] = True
    # rerankScore 默认就返回，显式打开不影响计费
    contents["rerankScore"] = True

    body: dict = {
        "query": q,
        "engineType": engine_type,
        "contents": contents,
        "advancedParams": {"numResults": max(1, min(int(num_results or 10), 50))},
    }
    if time_range:
        body["timeRange"] = str(time_range)
    if start_date:
        body["advancedParams"]["startPublishedDate"] = str(start_date)
    if end_date:
        body["advancedParams"]["endPublishedDate"] = str(end_date)

    from app.tools._http import format_error

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=max(20.0, settings.http_timeout),
                                     trust_env=False) as client:
            resp = await client.post(_base() + _SEARCH, headers=_headers(), json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        code = getattr(getattr(e, "response", None), "status_code", "?")
        detail = ""
        try:
            detail = str(e.response.text)[:160]
        except Exception:  # noqa: BLE001
            pass
        # 额度耗尽判定（用户已在控制台关闭"用尽自动转付费"，用尽后返回 403）：
        #   官方错误码 `Retrieval.PackageExhausted`（实测），或 403/429 且文案提到额度/配额。
        # 用错误码优先，避免把"鉴权失败 403"之类误判成额度用尽而中止整轮。
        low = detail.lower()
        if "packageexhausted" in low or "exhausted" in low or "quota" in low \
                or ("403" == str(code) and "retrieval" in low) \
                or (code in (403, 429) and ("额度" in detail or "package" in low)):
            raise AliyunQuotaExhausted(f"HTTP {code} {detail}") from e
        if code in (403, 429):
            raise AliyunQuotaExhausted(f"HTTP {code} {detail}") from e
        raise AliyunUnavailable(f"HTTP {code} {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise AliyunUnavailable(format_error(e)) from e

    return normalize(data)


def normalize(data: dict) -> list[dict]:
    """把 `pageItems[]` 归一化。

    字段注意：`link` 是真实 URL；`mainText` 是正文（可能为空）；
    `hostname` 可能为 None；两个 authority 分数是**字符串**（需转 float 或留 None）。
    """
    out: list[dict] = []
    for x in (data or {}).get("pageItems") or []:
        if not isinstance(x, dict):
            continue
        url = str(x.get("link") or "").strip()
        if not url:
            continue
        host = x.get("hostname")
        out.append({
            "title": str(x.get("title") or ""),
            "url": url,
            "snippet": str(x.get("snippet") or ""),
            # content 优先长正文，退回增强摘要，再退回 snippet
            "content": str(x.get("mainText") or x.get("summary") or ""),
            "date": str(x.get("publishedTime") or ""),
            "website": str(host) if host and host != "None" else "",
            "authority_score": _to_float(x.get("hostAuthorityScore")
                                         or x.get("websiteAuthorityScore")),
            "rerank_score": _to_float(x.get("rerankScore")),
            "engine": str(x.get("_engine") or ""),
        })
    return out


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def read_page(url: str) -> str:
    """网页解析（ReadPage 普通版）：按 URL 取正文。

    只在搜索结果**没带 `mainText`** 时才需要（例如正文过长、或该引擎未回正文）。
    失败抛 `AliyunUnavailable`。
    """
    if not enabled():
        raise AliyunUnavailable("未配置 ALIYUN_IQS_API_KEY")
    url = (url or "").strip()
    if not url.startswith("http"):
        raise AliyunUnavailable("URL 非法")

    from app.tools._http import format_error

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=max(20.0, settings.http_timeout),
                                     trust_env=False) as client:
            resp = await client.post(_base() + _READPAGE_BASIC, headers=_headers(),
                                     json={"url": url})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        code = getattr(getattr(e, "response", None), "status_code", "?")
        raise AliyunUnavailable(f"HTTP {code}") from e
    except Exception as e:  # noqa: BLE001
        raise AliyunUnavailable(format_error(e)) from e

    d = data.get("data") if isinstance(data, dict) else None
    text = ""
    if isinstance(d, dict):
        text = str(d.get("text") or d.get("mainText") or d.get("content") or "")
    elif isinstance(data, dict):
        text = str(data.get("text") or "")
    if len(text) < 40:
        raise AliyunUnavailable("正文过短（页面可能为脚本渲染）")
    return text
