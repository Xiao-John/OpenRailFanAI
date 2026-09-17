"""通用网页搜索工具（境内可用引擎）。

DuckDuckGo 在中国境内不可达（ConnectTimeout），因此改用境内可访问的搜索引擎：
- 主引擎：Bing 中国（cn.bing.com）
- 兜底：百度（www.baidu.com）

无需 API Key，返回结果标题 + 链接 + 摘要，供生成层引用。

相关性闸门（M11.1 修复）
-----------------------
早期实现"只要解析出结果就采纳"，实测同一查询
（"CR400AF 动车组 交路"）Bing 返回的是 BOSS直聘 / 论文 / 无关站内页，
而百度 5/5 高度相关 —— 却因为"Bing 有结果"而**永不回退**，
导致 knowledge 型问题的引用来源不可信。

现在：对每个引擎的结果做查询相关性打分，**采纳第一个"相关结果数达标"的引擎**；
全部不达标时按最相关者择优，全为 0 时如实返回 ok=False（宁缺毋滥）。
"""
from __future__ import annotations

import re
from urllib.parse import quote


from app.config import get_settings
from app.tools._http import BROWSER_HEADERS, format_error, get_client
from app.tools.base import Tool, ToolResult

# Bing 结果块：<li class="b_algo"> ... </li>
_BING_BLOCK_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
# 通用：<h2 ...><a href="URL" ...>TITLE</a></h2>
_H2_A_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# 百度结果块：<div ... class="result ..."> ... </div>（结构多变，用标题锚点兜底）
_BAIDU_TITLE_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S
)

# 结果日期：2024-12-26 / 2024/12/26 / 2024年12月26日 / 12月26日
_DATE_PATTERNS = (
    re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?"),
    re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日"),
)

_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_CJK_SEG_RE = re.compile(r"[\u4e00-\u9fff]+")

# 至少要有这么多条"与查询相关"的结果，才认为该引擎可用
_MIN_RELEVANT = 2


def _clean(html: str) -> str:
    """去标签 + 压缩空白。"""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html)).strip()


def _query_tokens(query: str) -> set[str]:
    """把查询切成判别性词元：英文/数字词 + 中文 bigram（及整段中文）。"""
    q = (query or "").lower()
    tokens: set[str] = set(_WORD_RE.findall(q))
    for seg in _CJK_SEG_RE.findall(q):
        if len(seg) == 1:
            tokens.add(seg)
        else:
            tokens.update(seg[i:i + 2] for i in range(len(seg) - 1))
            if len(seg) >= 3:
                tokens.add(seg)
    return {t for t in tokens if t}


def _extract_date(item: dict) -> str:
    """从标题/摘要/URL 抽取日期（YYYY-MM-DD），供"时效优先"判断。

    2026-09-14 修复 H02：搜索结果不带日期时，模型会把"已开通"说成"尚未开通"
    （新旧条目混杂且无从判断时效）。能抽到日期的就渲染出来，抽不到的明确标注。
    """
    hay = " ".join(str(item.get(k) or "") for k in ("title", "snippet", "url"))
    m = _DATE_PATTERNS[0].search(hay)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _DATE_PATTERNS[1].search(hay)
    if m:
        # 只有月日时**不臆造年份**（"预计年底开通"这类描述里的 12-26 可能是未来），
        # 只给出 MM-DD 并保留"日期不完整"的信号，避免模型据此误判新旧
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return ""


def _relevance(query_tokens: set[str], item: dict) -> int:
    """结果与查询的相关性打分（命中词元数；标题命中权重更高）。"""
    title = str(item.get("title") or "").lower()
    body = str(item.get("snippet") or "").lower()
    url = str(item.get("url") or "").lower()
    score = 0
    for t in query_tokens:
        if t in title:
            score += 2
        elif t in body or t in url:
            score += 1
    return score


def _parse_bing(html: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for block in _BING_BLOCK_RE.findall(html):
        m = _H2_A_RE.search(block)
        if not m:
            continue
        url, title = m.group(1), _clean(m.group(2))
        if not title or not url.startswith("http"):
            continue
        snippet = ""
        sm = _SNIPPET_RE.search(block)
        if sm:
            snippet = _clean(sm.group(1))
        entry = {"title": title, "url": url, "snippet": snippet[:300]}
        entry["date"] = _extract_date(entry)
        results.append(entry)
        if len(results) >= limit:
            break
    return results


def _parse_baidu(html: str, limit: int) -> list[dict]:
    results: list[dict] = []
    for m in _BAIDU_TITLE_RE.finditer(html):
        url, title = m.group(1), _clean(m.group(2))
        if not title or not url.startswith("http"):
            continue
        entry = {"title": title, "url": url, "snippet": ""}
        entry["date"] = _extract_date(entry)
        results.append(entry)
        if len(results) >= limit:
            break
    return results


class WebSearchTool2(Tool):
    name = "web.search"
    description = "网页搜索（Bing 中国 / 百度兜底，无需 API Key）"

    async def invoke(self, params: dict) -> ToolResult:
        query = (params.get("q") or params.get("query") or params.get("keyword") or "").strip()
        if not query:
            return ToolResult(ok=False, error="缺少搜索关键词 (q)")
        limit = int(params.get("limit") or 5)
        tokens = _query_tokens(query)

        settings = get_settings()
        # 时效过滤（freshness）：Bing 支持 ex1 时间过滤（ez1=24h / ez2=一周 / ez3=一个月）；
        # 百度无等价参数，此时只对 Bing 生效，并在 note 中如实说明。
        freshness = str(params.get("freshness") or "").strip().lower()
        bing_url = f"https://cn.bing.com/search?q={quote(query)}"
        if freshness in ("day", "week", "month"):
            code = {"day": "ez1", "week": "ez2", "month": "ez3"}[freshness]
            bing_url += f'&filters=ex1%3a"{code}"'
        engines = [
            ("bing", bing_url, _parse_bing),
            ("baidu", f"https://www.baidu.com/s?wd={quote(query)}", _parse_baidu),
        ]

        errors: list[str] = []
        attempts: list[tuple[str, int, list[dict], int]] = []   # engine, 相关数, 相关结果, 原始数
        client = await get_client()
        for engine, url, parser in engines:
            try:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                resp.raise_for_status()
            except Exception as e:
                errors.append(f"{engine}: {format_error(e)}")
                continue

            parsed = parser(resp.text, limit)
            if not parsed:
                errors.append(f"{engine}: 未解析出结果（页面结构可能变化）")
                attempts.append((engine, 0, [], 0))
                continue

            scored = sorted(
                ((_relevance(tokens, r), r) for r in parsed),
                key=lambda x: x[0],
                reverse=True,
            )
            relevant = [r for s, r in scored if s > 0]
            attempts.append((engine, len(relevant), relevant, len(parsed)))

            if len(relevant) >= min(_MIN_RELEVANT, limit):
                break                      # 该引擎结果可信，无需再打下一个引擎

        if not attempts:
            return ToolResult(
                ok=False,
                error="搜索失败：" + "；".join(errors),
                note="Bing 中国与百度均不可用，请检查网络",
            )

        # 择优：相关结果最多的引擎；全为 0 则如实失败（不返回无关结果充当答案）
        best_engine, best_score, best_results, raw_count = max(attempts, key=lambda a: a[1])
        if best_score == 0:
            detail = "；".join(f"{e}: 相关 0/{n}" for e, _, _, n in attempts)
            return ToolResult(
                ok=False,
                error=f"未找到与「{query}」相关的结果（引擎均返回了无关内容，已全部丢弃）",
                note=f"搜索结果相关性校验未通过（{detail}）；建议换关键词或改用其它数据源",
            )

        dropped = max(0, raw_count - len(best_results))
        text_lines = [
            f"{i + 1}. {('[' + r['date'] + '] ') if r.get('date') else '[日期未知] '}"
            f"{r['title']}\n   {r['url']}"
            + (f"\n   {r['snippet']}" if r["snippet"] else "")
            for i, r in enumerate(best_results)
        ]
        dated = sum(1 for r in best_results if r.get("date"))
        fresh_label = f"【{ {'day': '近 24 小时', 'week': '近一周', 'month': '近一月'}[freshness] }·时效过滤】" if freshness in ("day", "week", "month") else ""
        note = f"{fresh_label}{best_engine} 搜索结果（相关 {len(best_results)} 条，其中 {dated} 条带日期"
        if dropped:
            note += f"，已过滤 {dropped} 条与查询无关的结果"
        note += "）"
        if freshness in ("day", "week", "month") and best_engine != "bing":
            note += "；注意：时效过滤仅必应支持，本次结果来自百度，未应用时间过滤"

        return ToolResult(
            ok=True,
            data={
                "query": query,
                "engine": best_engine,
                "results": best_results,
                "relevant_count": len(best_results),
                "filtered_out": dropped,
                "engine_stats": [
                    {"engine": e, "relevant": s, "parsed": n} for e, s, _, n in attempts
                ],
            },
            text="\n".join(text_lines),
            sources=[r["url"] for r in best_results[:3]],
            note=note,
        )
