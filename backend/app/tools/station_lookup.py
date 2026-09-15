"""站点查询工具（基于 12306 站点库）。

搜索策略（2026-09-14 重写排序，修复 R1 E08/E03）
--------------------------------------------------------------------
mcp 的 `search_stations_validated` **硬上限 10 条**（实测 limit=80 仍只回 10），
且其顺序对支线/小站不利：查"北京"会返回 `房山东、后吕村`，却漏掉
**北京丰台、清河**（二者 12306 站序 num 分别为 229 / 1055，排在 10 条之外）。

现在改为：
1. **本地索引排序**（`_rt12306.all_stations()` 自带 `city` 同城归属与 `num` 站序≈重要度）：
   优先级 = 电报码精确 > 站名完全相同 > 站名以查询词开头 > 同城车站 > 站名包含查询词 > 拼音/简拼；
   同级按 `num` 升序。
2. 本地无命中时回退 mcp 校验（保留"该站不存在"的权威判定），并给**近似站名候选**（含同音提示）。
3. 结果始终声明"匹配 M 个、展示前 N 个"，避免"截断冒充全集"。
"""
from __future__ import annotations

from mcp_12306.services.ticket_service import search_stations_validated

from app.config import get_settings
from app.tools import _rt12306 as rt
from app.tools._http import format_error
from app.tools.base import Tool, ToolResult


# 拼音索引（惰性构建一次）：拼音 → [站名]，用于同音纠错（"太安"→"泰安"）
_PY_INDEX: dict[str, list[str]] = {}
_PY_INDEX_READY = False


def _pinyin_of(text: str) -> str:
    """整串拼音（无声调）；pypinyin 缺失时返回空串（功能降级，不影响主流程）。"""
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return ""
    return "".join(lazy_pinyin(text))


def _pinyin_index() -> dict[str, list[str]]:
    """构建/复用「拼音 → 站名列表」索引（仅含 2-4 字站名，避免噪声）。"""
    global _PY_INDEX_READY
    if _PY_INDEX_READY:
        return _PY_INDEX
    try:
        names = list(rt.all_stations().keys())
    except Exception:  # noqa: BLE001
        return {}
    for name in names:
        core = name.rstrip("站")
        if not (2 <= len(core) <= 4):
            continue
        py = _pinyin_of(core)
        if py:
            _PY_INDEX.setdefault(py, []).append(name)
    _PY_INDEX_READY = True
    return _PY_INDEX


def _suggest_stations(keyword: str, limit: int = 8) -> list[tuple[str, str]]:
    """找与 keyword 最接近的站名：**同音优先**，其次形近/位置重合。

    中文场景下 difflib 的字符相似度对同音字完全无效（"太安" vs "泰安" 相似度 0），
    因此引入拼音（pypinyin）："太安"→taian 与 "泰安"→taian 完全一致 → 直接命中。
    没有 pypinyin 时退化为位置重合打分（首字/末字/长度）。
    """
    try:
        stations = rt.all_stations()
    except Exception:  # noqa: BLE001 —— 建议只是锦上添花，失败不影响主流程
        return []
    if not stations:
        return []

    q = keyword.rstrip("站")
    ordered: list[str] = []

    # ① 同音站（拼音完全一致）
    q_py = _pinyin_of(q)
    if q_py:
        for name in _pinyin_index().get(q_py, []):
            if name != q and name not in ordered:
                ordered.append(name)

    # ② 形近/位置重合打分（首字 +2 / 长度 +2 / 末字 +2）
    scored: list[tuple[int, str]] = []
    for name in stations:
        if not name or name == q or name in ordered:
            continue
        score = 0
        if name[:1] == q[:1]:
            score += 2
        if len(name) == len(q):
            score += 2
        if name[-1:] == q[-1:]:
            score += 2
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
    for _s, name in scored:
        ordered.append(name)

    return [(name, stations[name]["code"]) for name in ordered[:limit]]


def _rank_local(keyword: str, limit: int) -> tuple[list[dict], int]:
    """本地索引搜索 + 排序；返回 (前 limit 条, 命中总数)。"""
    stations = rt.all_stations()
    if not stations:
        return [], 0

    q = keyword.strip()
    q_lower = q.lower()
    q_no_suffix = q.rstrip("站")

    def score(name: str, rec: dict) -> tuple | None:
        if rec.get("code", "").upper() == q.upper() or name == q or name == q_no_suffix:
            tier = 0                                    # 电报码/站名精确命中
        elif name.startswith(q):
            tier = 1                                    # 站名以查询词开头（北京 → 北京南）
        elif rec.get("city") in (q, q_no_suffix):
            tier = 2                                    # 同城车站（如"清河"属北京）
        elif q in name:
            tier = 3                                    # 站名包含查询词
        elif rec.get("py_short") == q_lower or str(rec.get("pinyin", "")).startswith(q_lower):
            tier = 4                                    # 拼音/简拼命中
        else:
            return None
        return (tier, rec.get("num", 10**9), len(name), name)

    hits = [(name, rec, score(name, rec)) for name, rec in stations.items()]
    hits = [(n, r, s) for n, r, s in hits if s is not None]
    hits.sort(key=lambda x: x[2])
    ordered = [
        {"name": n, "code": r["code"], "pinyin": r.get("pinyin", ""),
         "py_short": r.get("py_short", ""), "city": r.get("city", ""),
         "num": r.get("num", 10**9)}
        for n, r, _s in hits
    ]
    return ordered[:limit], len(ordered)


class StationLookupTool(Tool):
    name = "station.lookup"
    description = "查询中国铁路站点信息（站名/电报码/拼音码，12306 站点库）"

    async def invoke(self, params: dict) -> ToolResult:
        keyword = (params.get("name") or params.get("station") or params.get("keyword") or "").strip()
        if not keyword:
            return ToolResult(ok=False, error="缺少查询关键词")

        try:
            await rt.ensure_loaded()
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                ok=False,
                error=f"12306 站点库加载失败：{format_error(e)}",
                note="需 Python ≥ 3.10（mcp-server-12306 依赖）",
            )

        limit = int(params.get("limit") or get_settings().station_list_limit)

        # ---- 1) 本地索引排序（绕开 mcp 的 10 条硬上限，并按 12306 站序排重要度）----
        stations, matched_total = _rank_local(keyword, limit)
        if stations:
            lines = [
                f"{s['name']}（电报码 {s['code']}"
                + (f"，城市 {s['city']}" if s.get("city") else "")
                + (f"，拼音 {s['pinyin']}" if s.get("pinyin") else "")
                + "）"
                for s in stations
            ]
            shown = len(stations)
            note = f"匹配 {matched_total} 个站点，展示前 {shown} 个（按 12306 站序≈重要度排序）"
            if matched_total > shown:
                note += "；结果已截断，如需更多请加关键词缩小范围"
            return ToolResult(
                ok=True,
                data={"success": True, "used_keyword": keyword, "count": matched_total,
                      "stations": stations,
                      # 兼容旧字段名（既有消费者/test_tools 读 matches）
                      "matches": stations},
                text="\n".join(lines),
                sources=["https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"],
                note=note,
                total=matched_total, shown=shown, truncated=matched_total > shown,
                filters={"关键词": keyword},
            )

        # ---- 2) 本地无命中 → mcp 校验（权威判定"站不存在"）+ 近似候选 ----
        data: dict = {"success": False}
        used_keyword = keyword
        # 依次尝试候选词（原词 → 截断占位符 → 去行政后缀），命中即停
        for candidate in rt.station_candidates(keyword):
            data = rt.parse_mcp_result(
                await search_stations_validated({"query": candidate, "limit": 10})
            )
            if data.get("success"):
                used_keyword = candidate
                break

        if not (data.get("success") and data.get("stations")):
            # 失败时给近似站名建议：错别字/近音字（"太安"→"泰安"）在这里被纠正，
            # 而不是让用户对着"未找到"发愁（2026-09-14 修复 E07）
            suggestions = _suggest_stations(keyword)
            note = "可尝试使用电报码（如 BJP）或拼音（如 beijing）搜索"
            text = ""
            if suggestions:
                same_py = _pinyin_of(keyword.rstrip("站"))
                homophones = [
                    n for n, _c in suggestions
                    if same_py and _pinyin_of(n.rstrip("站")) == same_py
                ]
                label = "**同音站**" if homophones else "名称相近的候选站"
                text = (
                    f"未找到该站。{label}："
                    + "、".join(f"{n}（{c}）" for n, c in suggestions)
                    + "。请核对后重试（若输入的是同音字/形近字，候选通常已含正确站名）。"
                )
                note = "未精确命中；已给出近似站名候选，请确认后重试"
            return ToolResult(
                ok=False,
                error=data.get("error", f"未找到匹配站点：{keyword}"),
                text=text,
                note=note,
            )

        stations = data.get("stations", [])
        lines = [
            f"{s['name']}（电报码 {s['code']}，拼音 {s.get('pinyin','')}）"
            for s in stations
        ]
        note = f"匹配 {len(stations)} 个站点"
        if used_keyword != keyword:
            note += f"（已将“{keyword}”按“{used_keyword}”检索）"

        return ToolResult(
            ok=True,
            data={"matches": stations, "count": len(stations), "query": used_keyword},
            text="\n".join(lines),
            sources=["https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"],
            note=note,
        )