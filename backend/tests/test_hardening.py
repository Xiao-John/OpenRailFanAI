"""本轮 P2 加固的无网络回归测试（M11.1 审计修复回归）。

对应 `审计报告（历史）` / `audit-tests-docs.md` 的 P2 项：
1. `rail.line` 表头定位只取首次出现 → 现解析**所有**候选表并取最像径路的那张
2. `rail.line` 末段无"总Xkm"时把区间里程当总里程 → 现标注为近似值
3. `rail.line` 站名带行政区后缀（北京市/上海市）直接失败 → 现自动去后缀重试
4. `cnrail.map` / `railre` / `emu.routing` 的 URL 未转义 → 现全部 `quote(..., safe="")`
5. 离线车次目录**在请求路径同步下载 15MB** → 现不再下载，改为提示预热
6. 缓存无 TTL、损坏即抛 JSONDecodeError → 现 TTL + 原子写 + 明确报错 + 下载失败回退旧缓存
7. `run_all.sh` 首个失败即中断、无汇总 → 现跑完全部并汇总

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_hardening.py
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from app.data import train_db
from app.tools import cnrail, emu_routing, railre
from app.tools import rail_line as rail_line_mod
from app.tools.rail_line import RailLineTool, _strip_admin_suffix, parse_route_table
from app.tools.train_schedule import TrainScheduleTool

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------- 1~3. rail.line ----------

_STATION_TABLE = """<table><tr><th>序号</th><th>车站全名</th><th>车站电报码</th></tr>
<tr><td>1</td><td>北京</td><td>-BJP</td></tr>
<tr><td>2</td><td>上海</td><td>-SHH</td></tr></table>"""


def _route_table(rows: list[tuple[str, str, str, str, str]]) -> str:
    body = "".join(
        f"<tr><td>{line}</td><td>{st}</td><td>{short}</td><td>-{code}</td><td>{km}</td></tr>"
        for line, st, short, code, km in rows
    )
    return (
        "<table><tr><th>线路</th><th>车站全名</th><th>车站简称</th>"
        f"<th>车站电报码</th><th>里程</th></tr>{body}</table>"
    )


_ROUTE_ROWS = [
    ("", "北京", "北", "BJP", "0km"),
    ("京沪线", "北京南", "北", "VNP", "9km/总9km"),
    ("京沪高速线", "德州东", "德", "DIP", "314km/总323km"),
]


def test_rail_line_picks_route_table_not_station_list():
    """站名表排在径路表之前时，仍必须解析出径路（旧实现会静默返回 0 行）。"""
    html = _STATION_TABLE + "<div>" + "x" * 500 + "</div>" + _route_table(_ROUTE_ROWS)
    rows = parse_route_table(html)
    assert len(rows) == 3, f"未选中径路表：{rows}"
    assert rows[-1]["station"] == "德州东" and rows[-1]["cum_km"] == 323.0, rows[-1]
    print(f"[PASS] 多候选表：选中径路表并解析 {len(rows)} 行（站名表在前也不受影响）")

    # 反向顺序同样正确
    rows2 = parse_route_table(_route_table(_ROUTE_ROWS) + _STATION_TABLE)
    assert len(rows2) == 3, rows2
    print("[PASS] 表顺序反转后仍解析正确")


def test_rail_line_total_km_confidence():
    """末段缺"总Xkm"时必须标为近似值（旧实现把区间里程当总里程）。"""
    rows = [
        ("", "北京", "北", "BJP", "0km"),
        ("京沪线", "北京南", "北", "VNP", "9km/总9km"),
        ("京沪高速线", "济南西", "济", "JGK", "400km"),          # 无"总"字
    ]
    parsed = parse_route_table(_route_table(rows))
    assert parsed[-1]["cum_from_total"] is False, parsed[-1]
    assert parsed[1]["cum_from_total"] is True, parsed[1]

    class _Stub(RailLineTool):
        async def _fetch(self, from_st: str, to_st: str, settings) -> str:  # type: ignore[override]
            return _route_table(rows)

    res = asyncio.run(_Stub().invoke({"from_station": "北京", "to_station": "济南"}))
    assert res.ok, res.error
    assert res.data["total_km_confident"] is False, res.data
    assert res.data["total_km"] == 400.0, res.data
    assert "近似" in res.text or "可能有偏差" in res.text, res.text
    assert "近似值" in (res.note or ""), res.note
    print(f"[PASS] 末段无累计里程 -> 标注近似（{res.text.splitlines()[0][:46]}）")

    # 有"总Xkm"时应为可信值
    class _Stub2(RailLineTool):
        async def _fetch(self, from_st: str, to_st: str, settings) -> str:  # type: ignore[override]
            return _route_table(_ROUTE_ROWS)

    res2 = asyncio.run(_Stub2().invoke({"from_station": "北京", "to_station": "德州"}))
    assert res2.data["total_km_confident"] is True and res2.data["total_km"] == 323.0, res2.data
    print("[PASS] 末段有累计里程 -> 视为可信值")


def test_rail_line_admin_suffix_retry():
    """带行政区后缀的站名应自动去后缀重试（"北京市/上海市"）。"""
    assert _strip_admin_suffix("北京市") == "北京"
    assert _strip_admin_suffix("内蒙古自治区") == "内蒙古"
    assert _strip_admin_suffix("北京") == "北京"

    class _Retry(RailLineTool):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, str]] = []

        async def _fetch(self, from_st: str, to_st: str, settings) -> str:  # type: ignore[override]
            self.calls.append((from_st, to_st))
            if from_st.endswith("市") or to_st.endswith("市"):
                return "<html><body>未找到径路</body></html>"     # 全名查不到
            return _route_table(_ROUTE_ROWS)

    tool = _Retry()
    res = asyncio.run(tool.invoke({"from_station": "北京市", "to_station": "上海市"}))
    assert res.ok, res.error
    assert tool.calls == [("北京市", "上海市"), ("北京", "上海")], tool.calls
    assert res.data["used_from"] == "北京" and res.data["used_to"] == "上海", res.data
    assert "规范化" in (res.note or ""), res.note
    print(f"[PASS] 站名后缀重试 -> {tool.calls}，note 已说明规范化")

    # 规范站名不应产生二次请求
    tool2 = _Retry()
    asyncio.run(tool2.invoke({"from_station": "北京", "to_station": "上海"}))
    assert len(tool2.calls) == 1, tool2.calls
    print("[PASS] 规范站名只请求一次（无多余往返）")


# ---------- 4. URL 转义 ----------

def test_user_input_is_url_escaped():
    """用户可控的站名/车次不得原样拼进 URL（防 `../` 穿越与查询注入）。"""
    from urllib.parse import quote

    evil = "北京/../../admin?x=1&y=2"
    expected_seg = quote(evil, safe="")      # 「/」「?」等必须被百分号编码

    r = asyncio.run(cnrail.CnRailTool().invoke({"station": evil}))
    url = r.data["map_url"]
    seg = url.split("/zh/")[-1]
    assert seg == expected_seg, f"cnrail URL 未按预期转义：{url}"
    assert "/" not in seg and "?" not in seg, f"路径分隔符/查询符未被编码：{seg}"
    print(f"[PASS] cnrail.map 转义 -> {url}")

    # railre：直接拦截取文函数以捕获 URL（不依赖网络，也不允许"静默跳过"）
    captured: dict = {}

    async def _fake_get_text(url: str, **_kw):
        captured["url"] = url
        raise RuntimeError("停止：仅用于捕获 URL")

    original_get_text = railre.get_text
    railre.get_text = _fake_get_text               # type: ignore[assignment]
    try:
        rr = asyncio.run(railre.RailReTool().invoke({"station": evil}))
    finally:
        railre.get_text = original_get_text        # type: ignore[assignment]
    assert "url" in captured, "未捕获到 railre 请求 URL"
    seg2 = captured["url"].rsplit("/", 1)[-1]
    assert seg2 == expected_seg, f"railre URL 未按预期转义：{captured['url']}"
    assert rr.ok is False, "取文失败时应为 ok=False"
    print(f"[PASS] railre 转义 -> {captured['url']}")

    # emu.routing：非法形态直接被校验拒绝（连请求都不会发）
    er = asyncio.run(emu_routing.EmuRoutingTool().invoke({"emu_no": evil}))
    assert er.ok is False and "格式不正确" in er.error, er
    print("[PASS] emu.routing 非法输入被形态校验拦下（不发请求）")


# ---------- 5~6. 离线车次目录 ----------

def test_no_cache_download_in_request_path():
    """缓存缺失时**不得**在请求路径下载（应提示预热）。"""
    originals = (train_db._TRAIN_CACHE, train_db._train_db)
    downloads = {"n": 0}

    async def _fake_download(*_a, **_kw):
        downloads["n"] += 1
        raise AssertionError("请求路径不应触发缓存下载")

    original_download = train_db._download_raw
    original_infer = train_db.get_train_db  # 仅为对称，实际 patch 见下
    try:
        train_db._TRAIN_CACHE = Path("/nonexistent-dir/.train_cache.json")
        train_db._train_db = None
        train_db._download_raw = _fake_download           # type: ignore[assignment]
        # 推断失败 → 工具直接进入"离线兜底"分支（不触碰 12306）
        import app.tools.train_schedule as ts

        # ⚠️ 必须把**所有**联网入口都堵住，否则本用例会真打 12306，行为随当天时点漂移
        # （实测：车已发车时走"已发车分支"，未发车时走别的分支，断言时对时错）。
        _patched = {
            "infer_endpoints_from_offline": ts.rt.infer_endpoints_from_offline,
            "resolve_train_identity": getattr(ts.rt, "resolve_train_identity", None),
            "resolve_station_code": getattr(ts.rt, "resolve_station_code", None),
            "query_tickets": getattr(ts.rt, "query_tickets", None),
            "query_route_stations": getattr(ts.rt, "query_route_stations", None),
        }
        ts.rt.infer_endpoints_from_offline = lambda *_a, **_kw: None      # type: ignore[assignment]
        if _patched["resolve_train_identity"] is not None:
            async def _no_ident(*_a, **_kw):
                return None
            ts.rt.resolve_train_identity = _no_ident                     # type: ignore[assignment]
        if _patched["resolve_station_code"] is not None:
            async def _stub_code(name):
                return ("VNP", "北京南")
            ts.rt.resolve_station_code = _stub_code                      # type: ignore[assignment]
        if _patched["query_tickets"] is not None:
            async def _no_tickets(*_a, **_kw):
                raise ts.Realtime12306Error("离线用例：不发请求")
            ts.rt.query_tickets = _no_tickets                            # type: ignore[assignment]
        if _patched["query_route_stations"] is not None:
            async def _no_stops(*_a, **_kw):
                return []
            ts.rt.query_route_stations = _no_stops                       # type: ignore[assignment]
        try:
            res = asyncio.run(TrainScheduleTool().invoke({"train": "G1"}))
        finally:
            for _name, _fn in _patched.items():
                if _fn is not None:
                    setattr(ts.rt, _name, _fn)
    finally:
        train_db._TRAIN_CACHE, train_db._train_db = originals
        train_db._download_raw = original_download            # type: ignore[assignment]

    # 本用例的**意图**是"请求路径里不得同步下载 15MB 缓存"（✅35）。
    # 2026-09-15 起 train.schedule 在 12306 不可用时会先落到**本地 GTFS 快照**（读本地库里
    # 已有的数据，不触发任何下载），因此断言改为：下载次数为 0 + 结果来源不得是"下载得到的缓存"。
    assert downloads["n"] == 0, "请求路径仍触发了缓存下载"
    if res.ok:
        assert (res.data or {}).get("source") == "local-gtfs", res
        assert "本地 GTFS 快照" in (res.text or ""), res.text[:200]
        print(f"[PASS] 缓存缺失 -> 未下载（{downloads['n']} 次）；改由本地 GTFS 快照作答，耗时 0s")
    else:
        assert "预热" in (res.note or ""), f"未提示预热：{res.note}"
        print(f"[PASS] 缓存缺失 -> 不再同步下载，耗时 0s；提示：{res.note[:48]}…")


def test_cache_ttl_and_corrupt_cache():
    """缓存 TTL 判定与损坏缓存的明确报错。"""
    originals = (train_db._TRAIN_CACHE, train_db._train_db)
    tmp = Path("/tmp/railfan-hardening-cache.json")
    try:
        # 损坏缓存 → 明确 RuntimeError（而不是 JSONDecodeError）
        tmp.write_text("{ this is not json", encoding="utf-8")
        train_db._TRAIN_CACHE = tmp
        train_db._train_db = None
        db = train_db.get_train_db()
        try:
            db.train_count
        except RuntimeError as e:
            assert "损坏" in str(e) and "预热" in str(e), e
            print(f"[PASS] 损坏缓存 -> 明确报错：{str(e)[:52]}…")
        else:
            raise AssertionError("损坏缓存未报错")

        # 新鲜缓存 → 不 stale；超期 → stale
        tmp.write_text(json.dumps({"G1": {"train_code": "G1"}}), encoding="utf-8")
        assert train_db.is_cache_stale() is False, "新建缓存不应判为过期"
        old = time.time() - 40 * 86400
        import os

        os.utime(tmp, (old, old))
        assert train_db.is_cache_stale() is True, "40 天前的缓存应判为过期"
        print("[PASS] 缓存 TTL：新缓存不过期，40 天前判为过期（默认 TTL 30 天）")

        # 过期 + 下载失败 → 回退旧缓存（不让兜底数据凭空消失）
        async def _fail_download(*_a, **_kw):
            raise RuntimeError("模拟网络故障")

        original_download = train_db._download_raw
        train_db._download_raw = _fail_download        # type: ignore[assignment]
        try:
            data = asyncio.run(train_db.build_train_cache())
        finally:
            train_db._download_raw = original_download  # type: ignore[assignment]
        assert "G1" in data, f"下载失败未回退旧缓存：{data}"
        print("[PASS] 缓存超期且下载失败 -> 回退使用旧缓存")
    finally:
        train_db._TRAIN_CACHE, train_db._train_db = originals
        if tmp.exists():
            tmp.unlink()


# ---------- 7. 测试脚本自身 ----------

def test_placeholder_api_key_is_not_ready():
    """`.env.example` 的占位 Key 不得被当成"已配置 LLM"。

    否则 LLM_MOCK=false 时会拿占位串调真实接口 → 401 → 用户看到误导性的"鉴权失败"。
    """
    from app.config import Settings

    assert Settings(llm_api_key="").llm_ready is False
    assert Settings(llm_api_key="   ").llm_ready is False
    assert Settings(llm_api_key="your-api-key-here").llm_ready is False
    assert Settings(llm_api_key="YOUR-API-KEY-HERE").llm_ready is False
    assert Settings(llm_api_key="sk-real-looking-key-0123456789").llm_ready is True

    # .env.example 里的占位值本身必须是"未就绪"
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^LLM_API_KEY=(.*)$", env_example, re.M)
    assert m, ".env.example 缺少 LLM_API_KEY"
    assert Settings(llm_api_key=m.group(1).strip()).llm_ready is False, (
        f".env.example 的占位 Key 被判为已配置：{m.group(1)!r}"
    )
    print(f"[PASS] 占位 Key 不算已配置（.env.example 值={m.group(1)!r} -> llm_ready=False）")


def test_run_all_runs_all_suites_and_summarizes():
    """run_all.sh 必须跑完全部套件并汇总（不再首个失败即中断）。"""
    script = (REPO_ROOT / "backend/tests/run_all.sh").read_text(encoding="utf-8")
    assert "set -uo pipefail" in script, "仍在使用 set -e（首个失败会中断后续套件）"
    assert "set -euo pipefail" not in script, "仍存在 set -e"
    assert "FAILED" in script and "测试汇总" in script, "缺少失败汇总逻辑"
    assert "exit 1" in script, "失败时未返回非零退出码"
    # 列出的每套测试都必须真实存在
    import re

    suites = re.findall(r"^\s+(test_\w+)\s*$", script, re.M)
    assert len(suites) >= 15, f"套件数量异常：{len(suites)}"
    for s in suites:
        assert (REPO_ROOT / "backend/tests" / f"{s}.py").exists(), f"run_all.sh 引用了不存在的套件：{s}"
    print(f"[PASS] run_all.sh：{len(suites)} 套件全部存在、失败不中断、结尾有汇总")


def test_upgrade_python_has_rollback_trap():
    """upgrade_python.sh 必须先备份旧 venv 并在失败时回滚。"""
    script = (REPO_ROOT / "scripts/upgrade_python.sh").read_text(encoding="utf-8")
    assert "trap " in script and "回滚" in script, "缺少失败回滚"
    assert 'mv "$VENV" "$BACKUP"' in script, "未先备份旧虚拟环境"
    assert "rm -rf .venv\n" not in script.split("trap")[0], "仍在 trap 之前直接删除旧 venv"
    print("[PASS] upgrade_python.sh：先备份 + trap 失败回滚")


def main():
    test_rail_line_picks_route_table_not_station_list()
    test_rail_line_total_km_confidence()
    test_rail_line_admin_suffix_retry()
    test_user_input_is_url_escaped()
    test_no_cache_download_in_request_path()
    test_cache_ttl_and_corrupt_cache()
    test_placeholder_api_key_is_not_ready()
    test_run_all_runs_all_suites_and_summarizes()
    test_upgrade_python_has_rollback_trap()
    print("\nP2 加固回归测试全部通过 ✔")


if __name__ == "__main__":
    main()
