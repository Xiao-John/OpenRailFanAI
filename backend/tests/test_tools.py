"""工具层验证（M3.1）：注册表与各工具行为。

运行：cd backend && PYTHONPATH=. ./.venv/bin/python tests/test_tools.py
"""
from __future__ import annotations

import asyncio

from app.tools import registry

# 记录因外部依赖不可达而跳过的检查（结尾汇总打印，避免"静默全绿"）
_skips: list[str] = []


async def test_registry():
    names = {t.name for t in registry.list_enabled()}
    # M3.1：确认所有预期工具均已注册且启用
    expected = {
        "web.fetch", "web.search",
        "cnrail.map", "railre", "emu.routing", "rail.line", "rail.line_stations",
        "station.lookup", "station.screen", "rail.mileage", "train.schedule", "ticket.query",
        "jprailfan", "freight.95306", "kmrail.freight", "sytlj.ticket",
    }
    missing = expected - names
    assert not missing, f"缺少工具: {missing}"
    # t12306 未配置 T12306_BASE 时应被停用
    assert "t12306.search_tickets" not in names, "未配置 T12306_BASE 不应启用 t12306"
    print(f"[PASS] registry: {len(names)} tools enabled={sorted(names)}")


async def test_station_lookup():
    r = await registry.invoke_by_name("station.lookup", {"name": "北京"})
    assert r.ok, r
    matches = r.data.get("matches", [])
    assert len(matches) > 0, r
    assert matches[0]["code"] == "BJP", r.data
    print(f"[PASS] station.lookup(北京) -> {matches[0]['name']} ({matches[0]['code']})")

    r2 = await registry.invoke_by_name("station.lookup", {"name": "吉林"})
    assert r2.ok and r2.data.get("count", 0) > 0, r2
    print(f"[PASS] station.lookup(吉林) -> {r2.data['count']} matches")


async def test_train_schedule():
    """仅给车次 + 未来日期 → 自动推断起止站并返回实时数据。"""
    r = await registry.invoke_by_name("train.schedule", {"train": "G1", "date": "明天"})
    if r.ok and (r.data or {}).get("source") == "12306-realtime":
        assert r.data.get("from_station"), r.data
        print(
            f"[PASS] train.schedule(G1, 明天) 实时 -> "
            f"{r.data['from_station']}→{r.data['to_station']} "
            f"{r.data.get('start_time')}-{r.data.get('arrive_time')}"
        )
    elif r.ok:
        # 12306 不可达时的离线降级
        _skips.append("train.schedule 实时链路（降级为离线目录）")
        print(f"[SKIP] train.schedule(G1) 降级为离线: {r.note}")
    else:
        _skips.append("train.schedule 实时链路（工具失败）")
        print(f"[SKIP] train.schedule(G1) 未成功: {r.error}")

    # 模糊搜索（离线目录，作为静态兜底能力）
    r2 = await registry.invoke_by_name("train.schedule", {"train": "D2"})
    assert r2.ok, r2
    print(f"[PASS] train.schedule(search D2) -> {r2.data.get('count', 0)} matches")


async def test_train_schedule_departed_hint():
    """无日期（默认今天）且车次已发车时，应作为有效结论返回而非工具失败。"""
    r = await registry.invoke_by_name("train.schedule", {"train": "G1"})
    if r.ok and (r.data or {}).get("status") == "departed":
        assert "已发车" in (r.text or ""), r.text
        print(f"[PASS] train.schedule(G1) 已发车 -> ok=True, status=departed")
    elif r.ok:
        _skips.append("train.schedule 已发车提示（今日未发车，场景不成立）")
        print("[SKIP] train.schedule(G1) 今日仍可查到（未发车）")
    else:
        # 车次不属于该区间时才应失败
        _skips.append("train.schedule 已发车提示（工具未成功）")
        print(f"[SKIP] train.schedule(G1) 未成功（非已发车场景）: {r.error}")


async def test_ticket_query():
    """12306 实时余票查询（无需 T12306_BASE 反代）。"""
    r = await registry.invoke_by_name(
        "ticket.query",
        {"from_station": "北京", "to_station": "上海", "date": "明天", "limit": 3},
    )
    if r.ok:
        d = r.data
        assert d["count"] > 0, d
        assert d["from_code"] and d["to_code"], d
        print(f"[PASS] ticket.query 北京→上海 -> {d['count']} 趟（{d['train_date']}）")
        print(f"        {r.text.splitlines()[1][:80]}")
    else:
        print(f"[SKIP] ticket.query 12306 不可达: {r.error}")


async def test_ticket_query_missing_params():
    r = await registry.invoke_by_name("ticket.query", {"from_station": "北京"})
    assert not r.ok and "缺少参数" in (r.error or ""), r
    print("[PASS] ticket.query 缺参时优雅报错")


async def test_rail_line():
    """两站间最短径路：线路序列 + 车站 + 里程。"""
    r = await registry.invoke_by_name(
        "rail.line", {"from_station": "北京", "to_station": "上海"}
    )
    if not r.ok:
        print(f"[SKIP] rail.line 黄河铁路网不可达: {r.error}")
        return
    d = r.data
    assert d["station_count"] >= 2, d
    assert d["line_count"] >= 1, d
    assert d["total_km"] and d["total_km"] > 0, d
    assert d["lines"], d
    # 首个车站应为发站，末个为到站
    assert "北京" in d["route"][0]["station"], d["route"][0]
    assert "上海" in d["route"][-1]["station"], d["route"][-1]
    print(f"[PASS] rail.line 北京→上海 -> {d['total_km']:.0f}km, "
          f"{d['line_count']} 条线路, {d['station_count']} 站")
    print(f"        线路: {' → '.join(d['lines'][:5])}…")


async def test_rail_line_missing_params():
    r = await registry.invoke_by_name("rail.line", {"from_station": "北京"})
    assert not r.ok and "缺少参数" in (r.error or ""), r
    print("[PASS] rail.line 缺参时优雅报错")


async def test_rail_line_same_station():
    r = await registry.invoke_by_name("rail.line", {"from_station": "北京", "to_station": "北京"})
    assert not r.ok and "相同" in (r.error or ""), r
    print("[PASS] rail.line 发到站相同 -> 拒绝")


async def test_cnrail_map():
    r = await registry.invoke_by_name("cnrail.map", {"location": "吉林"})
    assert r.ok and r.data.get("map_url"), r
    print(f"[PASS] cnrail.map -> {r.data.get('map_url')}")


async def test_web_fetch_live():
    r = await registry.invoke_by_name("web.fetch", {"url": "https://example.com/"})
    assert r.ok, r
    assert "Example" in str(r.data.get("title")), r
    print(f"[PASS] web.fetch(example.com) -> title={r.data.get('title')}")


async def test_web_search():
    """境内搜索引擎（Bing CN / 百度）应可用并返回结果，且命中后会读正文。

    正文抓取**不保证成功**（实测 baike.baidu.com 一律 403、境外站点可能超时），
    所以这里只断言"字段与口径齐备"，不断言抓到了几条 —— 但那几个口径必须有：
    `pages` 要如实反映每条的成败，note 里要写明成功/失败条数。
    """
    r = await registry.invoke_by_name("web.search", {"q": "中国铁路 最新资讯", "limit": 3})
    if r.ok:
        results = (r.data or {}).get("results", [])
        assert results, r.data
        pages = (r.data or {}).get("pages")
        assert isinstance(pages, list), f"没有 pages 字段（正文抓取未接线）：{r.data.keys()}"
        for p in pages:
            assert {"url", "ok", "truncated", "error"} <= set(p), p
            assert p["ok"] or p["error"], f"抓取失败却没给原因：{p}"
        ok_n = sum(1 for p in pages if p["ok"])
        print(f"[PASS] web.search -> 引擎={r.data.get('engine')}, {len(results)} 条结果"
              f"；正文抓取得到 {ok_n} 条（尝试与失败明细在 note 里）")
        if ok_n:
            assert "【网页正文】" in r.text, "抓到了正文却没进事实块"
        print(f"        {results[0]['title'][:50]}")
    else:
        print(f"[SKIP] web.search 搜索引擎均不可达: {r.error}")


async def test_web_search_no_query():
    r = await registry.invoke_by_name("web.search", {})
    assert not r.ok and "关键词" in (r.error or ""), r
    print("[PASS] web.search 缺关键词时优雅报错")


async def test_t12306_disabled():
    r = await registry.invoke_by_name("t12306.search_tickets", {})
    assert not r.ok and ("未启用" in (r.error or "") or "不存在" in (r.error or "")), r
    from app.tools.t12306 import T12306Tool

    r2 = await T12306Tool().invoke({})
    assert not r2.ok and "T12306_BASE" in (r2.error or "") and r2.note, r2
    print("[PASS] t12306 未配置时注册表过滤 + 工具自身优雅提示")


async def test_extra_sources():
    """额外数据源：**必须给出可判定的结论**，不能只打印就算通过。

    这些源实测长期不可用（TLS 证书失败 / 超时），因此断言的重点不是"必须成功"，
    而是：ok 与失败原因**自洽且可读**，即
      - ok=True  → 必须真的拿到正文（非空）
      - ok=False → 必须有 error，且 note 里说明**具体原因类别**（tls/timeout/connect/dns/http），
                   不得再出现早期那种一律"站点可能不可达或需要特殊网络环境"的模糊归因
    """
    for name in ("jprailfan", "freight.95306", "kmrail.freight", "sytlj.ticket"):
        r = await registry.invoke_by_name(name, {})
        if r.ok:
            assert (r.text or "").strip(), f"{name} ok=True 但正文为空（可能是 SPA 外壳/空页）"
            print(f"[PASS] {name} -> ok=True，正文 {len(r.text)} 字符")
        else:
            assert r.error, f"{name} 失败但未给出 error"
            note = r.note or ""
            assert note, f"{name} 失败但未给出 note（用户无法判断原因）"
            assert any(k in note for k in ("tls", "timeout", "connect", "dns", "http", "不可用")), (
                f"{name} 的 note 归因不明确：{note}"
            )
            assert "特殊网络环境" not in note or "不可用" in note, (
                f"{name} 仍在使用早期模糊归因：{note}"
            )
            print(f"[PASS] {name} -> ok=False 且原因可读：{note[:70]}")


async def main():
    await test_registry()
    await test_station_lookup()
    await test_train_schedule()
    await test_train_schedule_departed_hint()
    await test_ticket_query()
    await test_ticket_query_missing_params()
    await test_rail_line()
    await test_rail_line_missing_params()
    await test_rail_line_same_station()
    await test_cnrail_map()
    await test_web_fetch_live()
    await test_web_search()
    await test_web_search_no_query()
    await test_t12306_disabled()
    await test_extra_sources()
    if _skips:
        # 断网/数据源不可达时，本套会退化为"只验证优雅降级"。
        # 明确打印出来，避免"静默全绿"被误读为链路已验证。
        print(f"\n[WARN] 本次有 {len(_skips)} 项因外部依赖不可达被跳过：")
        for s in _skips:
            print(f"       · {s}")
        print("       （这些项验证的是实时链路，跳过不代表通过）")
    print("\n工具层测试全部通过 ✔")


if __name__ == "__main__":
    asyncio.run(main())