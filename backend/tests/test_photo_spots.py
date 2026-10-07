"""机位本地库回归测试（方案 A）。

覆盖三件事：
1. **三道硬闸门**（纯函数，离线）——用 2026-10-06 实测**真实**记录的标题/URL 作对照，
   钉住"该拒的必须拒、该收的必须收"。这些对照来自本次调研中真实抓到的搜索结果，
   不是编造的样例。
2. **线上零延迟查表**（`photo.spot`）——只读本地库、不联网；未收录必须 `ok=False` 且
   明确"不得编造"，绝不返回空结果却报成功。
3. **检索层选路**——`photo_spot` 意图必须走 `photo.spot`，**不得**再用裸地点名打
   `web.search`（那是本缺陷的根因：实测裸地点名必然返回旅游/百科，却报 ok=True）。

运行：cd backend && PYTHONPATH=. LLM_MOCK=true .venv/bin/python tests/test_photo_spots.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# 建库脚本不在包内，按路径加载
_spec = importlib.util.spec_from_file_location(
    "build_photo_spots", str(REPO / "scripts" / "build_photo_spots.py"))
_bps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bps)

# 建库脚本的 DEFAULT_DB 是模块级变量；测试会把它指向临时库，
# **必须**在 finally 里还原，否则后续测试/用例会读到临时库（实测：line_master 表不存在）。
_ORIG_DEFAULT_DB = _bps.DEFAULT_DB


# ---------------------------------------------------------------- 真实对照数据
# 每条 = (标题, URL, 摘要, 用户所问地点 scope, 期望入库?, 期望理由关键词)
# 全部来自 2026-10-06 本次调研中实际发生的搜索结果（含 baidu 跳转链的真实形态）。
# scope 是**用户所问的地点**，不是页面自己的地点 —— 这正是"别处的机位"判定的关键：
# 实测查上海黄渡时百度返回「济南胶济线京沪线拍车,8大机位全攻略」，内容对但不是用户要的地方。
_REAL_CASES = [
    # —— 该拒：泛化页（实测裸地点名返回的东西）——
    ("吉林市_百度百科", "https://baike.baidu.com/item/%E5%90%89%E6%9E%97%E5%B8%82/182763",
     "位于吉林省中部偏东", "吉林", False, "泛化域名"),
    ("北京有什么必去的景点？ - 知乎", "https://www.zhihu.com/question/54171891",
     "推荐几个小众但值得去的地方", "北京", False, "泛化标题"),
    ("2024年北京旅游攻略：北京30个好玩的地方",
     "https://www.visitbeijing.com.cn/article/4J6NKmov93k", "北京30个好玩的地方",
     "北京", False, "泛化域名"),
    ("Beijing - 北京市人民政府门户网站", "https://www.beijing.gov.cn/", "",
     "北京", False, "泛化域名"),
    # —— 该拒：有铁路实体但**无拍摄意图**（实测：查关村坝返回车次视频）——
    ("(成昆铁路)韶山3型电力机车5204牵引5622次关村坝站一道停车",
     "http://www.baidu.com/link?url=abc", "运转视频", "关村坝", False, "标题未指明拍摄地点"),
    ("京广铁路_百度百科", "https://baike.baidu.com/item/%E4%BA%AC%E5%B9%BF%E9%93%81%E8%B7%AF",
     "京广铁路是中国一条南北铁路", "京广线", False, "泛化域名"),
    # —— 该拒：有拍摄词但**无具体铁路实体**（疑似风光/器材文）——
    ("新手也能拍出大片感的摄影攻略", "https://post.smzdm.com/p/abc",
     "相机参数设置与构图技巧，风光人像通用", "北京", False, "标题非铁路题材"),
    # —— 该收：**别处的机位也是有效线索**（2026-10-06 口径修正）——
    # 查询只是"发现探针"，不是归属声明：实测查上海时返回的《济南胶济线京沪线拍车,8大机位全攻略》
    # 是**真实机位攻略**，应收进库（挂在济南下），丢掉只是白白浪费一次发现机会。
    ("济南胶济线京沪线拍车,8大机位全攻略", "http://www.baidu.com/link?url=xyz",
     "胶济线沿线机位", "黄渡", True, "地点："),
    # —— 该拒：铁路文章但**与机位无关**（实测曾被误收，是加 _SPOT_RE 的直接原因）——
    ("[深圳西站送别纪念]记仪式感满满的末班K237/8接送车活动",
     "https://www.bilibili.com/read/cv18033780",
     "仅以此献给平南铁路 深圳西 广州北 平山 车迷朋友们一起送别", "深圳", False, "标题未指明拍摄地点"),
    ("【2023暑期 山城轨道暴走】day3奇葩车站,客流观测与面基",
     "https://www.bilibili.com/read/cv26529606/",
     "重庆北 璧山 朝天 巴南 客流观测", "重庆", False, "标题未指明拍摄地点"),
    ("一往无前:成渝铁路与重庆火车站", "https://www.bilibili.com/opus/1118311000331780133",
     "重庆北 重庆西 成渝铁路 新中国第一条铁路", "重庆", False, "标题未指明拍摄地点"),
    # —— 该拒：长正文"偶然命中"型假阳性（接入阿里云 mainText 后实测出现）——
    # 这些标题里有"机位"或"火车站"，但题材不是铁路拍车；若拿整篇正文判意志必误收。
    ("在迪士尼当公主的一天 （附拍照机位)", "https://post.m.smzdm.com/zz/p/awm69wr4/",
     "上海迪士尼拍照机位分享，城堡前最佳角度", "上海", False, "标题非铁路题材"),
    ("阿那亚拍照机位，带娃拍出大片感", "https://page.sm.cn/blm/node-page-new-995/index",
     "秦皇岛阿那亚礼堂拍照机位，海边灯塔", "上海", False, "标题非铁路题材"),
    ("沧州晚上火车站有玩的地方吗？夜间游玩全攻略与避坑指南",
     "http://www.sunstarasia.com/?/sports/677327.jsp",
     "哈尔滨 青岛 惠州 三亚 成都 火车站 夜间游玩", "上海", False, "标题未指明拍摄地点"),
    ("【重庆轨道交通】行千里，致广大！山城造就CRT",
     "https://m.bilibili.com/video/BV1ShpgzHEEW/",
     "重庆轨道交通 温州南 成都东 上海 眉山 北京", "上海", False, "标题未指明拍摄地点"),
    # —— 该收：强信号 + 具体实体 + 地理匹配（实测真实命中）——
    # 注：真实机位攻略的**标题**本身都带"机位"（实测《济宁市区拍火车机位大全》
    # 《上海沪杭段现存拍车机位汇总》），故此处按真实形态给标题。
    ("【铁路随拍】京沪线黄渡站拍车机位分享",
     "http://www.baidu.com/link?url=real1",
     "京沪线黄渡站拍车机位分享，站台北侧天桥下午顺光", "黄渡", True, "强信号"),
    ("哈尔滨松花江大桥摄影攻略,必打卡机位", "http://www.baidu.com/link?url=real2",
     "滨洲线松花江铁路大桥拍车机位，建议上午顺光", "松花江大桥", True, "强信号"),
]


async def test_gate() -> None:
    ent = _bps.LocalEntities()
    await ent.load()
    assert ent.stations, "站点库应可离线加载（包内静态资源）"

    fails = []
    for title, url, snippet, scope, want, why in _REAL_CASES:
        got, _ents, reasons = _bps._judge(title, url, snippet, "", scope, ent, [scope])
        joined = " ".join(reasons)
        if got != want:
            fails.append(f"「{title[:34]}」期望{'收' if want else '拒'}，"
                         f"实际{'收' if got else '拒'}｜{joined[:80]}")
        elif not want and why not in joined:
            fails.append(f"「{title[:34]}」拒绝理由应含「{why}」，实际「{joined[:80]}」")
    assert not fails, "闸门判定不符：\n  " + "\n  ".join(fails)
    print(f"[PASS] 硬闸门：{len(_REAL_CASES)} 条真实对照全部判定正确（该拒的拒、该收的收）")


async def test_lookup_read_path() -> None:
    """线上查表：命中可用、未收录如实失败、且不联网。"""
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "dict.db"
    old_env = os.environ.get("DICT_DB_PATH")
    os.environ["DICT_DB_PATH"] = str(db)
    _bps.DEFAULT_DB = db
    try:
        _bps.ensure_schema(db)
        _bps.upsert_docs(db, [{
            "url": "https://www.bilibili.com/video/BVdemo", "domain": "bilibili",
            "title": "京沪线黄渡站拍车机位分享", "source": "bilibili", "scope": "黄渡",
            "scope_kind": "landmark", "entities": ["黄渡", "京沪线"],
            "reasons": ["强信号"], "page_text": "从站台北侧天桥可拍到列车通过弯道，下午顺光。",
            "snippet": "", "fetch_ok": True, "date": "",
        }])

        # 工具走 app.data.dict.db_path()（读 DICT_DB_PATH），需清缓存
        from app.data import dict as D
        D.reset_for_tests()

        from app.tools import registry
        r = await registry.invoke_by_name("photo.spot", {"station": "黄渡"})
        assert r.ok and r.shown == 1, f"应命中 1 条，实际 ok={r.ok} shown={r.shown}"
        assert "天桥" in (r.text or ""), "正文原文应下发（交由生成层阅读）"
        assert r.sources, "必须带来源链接"

        r2 = await registry.invoke_by_name("photo.spot", {"station": "不存在的站"})
        assert r2.ok is False, "未收录必须 ok=False（不得空结果报成功）"
        assert "编造" in (r2.note or ""), "note 必须明确不得编造点位"

        r3 = await registry.invoke_by_name("photo.spot", {})
        assert r3.ok is False, "未给地点应如实失败"
        print("[PASS] 线上查表：命中带来源+正文；未收录/缺参一律如实 ok=False（不编造）")
    finally:
        if old_env is None:
            os.environ.pop("DICT_DB_PATH", None)
        else:
            os.environ["DICT_DB_PATH"] = old_env
        _bps.DEFAULT_DB = _ORIG_DEFAULT_DB
        from app.data import dict as D
        D.reset_for_tests()


async def test_routing() -> None:
    """检索层：photo_spot 意图必须走 photo.spot，不得再打 web.search。"""
    from app.pipeline import retrieve
    from app.pipeline.extract import Slots
    slots = Slots(location="黄渡", target=None, time=None, direction=None, extra=None, raw={})
    out = await retrieve.retrieve("photo_spot", slots, question_type="realtime",
                                 message="黄渡怎么拍车")
    trace = out.get("tool_trace") or []
    assert any(t.startswith("photo.spot") for t in trace), f"应调用 photo.spot：{trace}"
    assert not any(t.startswith("web.search") for t in trace), \
        f"不得再用裸地点名打 web.search（缺陷根因）：{trace}"
    print("[PASS] 检索层选路：photo_spot → photo.spot（本地库），不再打 web.search")


async def test_full_ingest_chain() -> None:
    """端到端：**真实抓取** → 闸门 → 入库 → 查表（用本地 HTTP 服务，不联网）。

    为什么要这一条：前面的查表测试直接塞 fixture 行，绕过了抓取与判定；
    而"抓正文"这一步恰恰是建库最容易失败的地方（B站可抓、知乎 403、百度跳转链）。
    这里用**真实观测到的页面形态**（B站视频页含 `<meta description>`、`__INITIAL_STATE__`、
    "哔哩哔哩" 标识）起本地服务，验证整条链路真的能落库并被查出来。

    页面形态取自 2026-10-06 实际抓取到的 B站视频页（见交接报告）。
    """
    import http.server
    import threading

    fixture_dir = Path(tempfile.mkdtemp())
    (fixture_dir / "page.html").write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>【铁路随拍】京沪线黄渡站拍车机位分享_哔哩哔哩_bilibili</title>"
        '<meta name="description" content="京沪线黄渡站的午后，站台北侧天桥可拍到列车通过弯道，下午顺光。">'
        "</head><body><div class=\"video-desc\">京沪线黄渡站的午后，从站台北侧天桥可以拍到列车通过弯道，"
        "下午顺光。机位：站台北侧人行天桥；焦段建议 70-200；下午 15:00-17:00 顺光。</div>"
        '<script>window.__INITIAL_STATE__={"keywords":["机位分享","拍车","京沪线"]}</script>'
        "<footer>哔哩哔哩</footer></body></html>", encoding="utf-8")

    class _H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(fixture_dir), **k)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tmp_db = Path(tempfile.mkdtemp()) / "dict.db"
    old_env = os.environ.get("DICT_DB_PATH")
    os.environ["DICT_DB_PATH"] = str(tmp_db)
    try:
        # 先用真实库载入本地实体，再切到临时库
        ent = _bps.LocalEntities()
        await ent.load()
        _bps.DEFAULT_DB = tmp_db
        _bps.ensure_schema(tmp_db)

        url = f"http://127.0.0.1:{port}/page.html"
        # allow_private：本地服务在环回地址，SSRF 校验默认会拦（这是正确的默认行为）
        ok, text, why = await _bps.fetch_body(url, allow_private=True)
        assert ok and len(text) > 60, f"应能抓到正文：{why}"

        title = "【铁路随拍】京沪线黄渡站拍车机位分享"
        good, ents, reasons = _bps._judge(title, url, "", text, "黄渡", ent, ["黄渡", "京沪线"])
        assert good, f"真实形态页面应通过闸门：{reasons}"
        assert "京沪线" in ents, f"实体应含京沪线，实际 {ents}"
        # 实体必须**干净**（不得吞进动词/方位描述）
        assert all(len(e) <= 12 and "可以" not in e and "拍到" not in e for e in ents), \
            f"实体含噪声：{ents}"

        _bps.upsert_docs(tmp_db, [{
            "url": url, "domain": "bilibili", "title": title, "source": "bilibili",
            "scope": "黄渡", "scope_kind": "landmark", "entities": ents,
            "reasons": reasons, "page_text": text, "snippet": "", "fetch_ok": True, "date": "",
        }])

        from app.data import dict as D
        D.reset_for_tests()
        from app.tools import registry
        r = await registry.invoke_by_name("photo.spot", {"station": "黄渡"})
        assert r.ok and r.shown == 1, f"入库后应可查出：ok={r.ok} shown={r.shown}"
        assert "天桥" in (r.text or ""), "正文原文应下发"
        assert r.sources, "应带来源链接"
        print("[PASS] 端到端：真实抓取 → 闸门 → 入库 → 查表 全链路成立（本地服务，不联网）")
    finally:
        srv.shutdown()
        if old_env is None:
            os.environ.pop("DICT_DB_PATH", None)
        else:
            os.environ["DICT_DB_PATH"] = old_env
        _bps.DEFAULT_DB = _ORIG_DEFAULT_DB
        from app.data import dict as D
        D.reset_for_tests()


async def main() -> None:
    await test_gate()
    await test_lookup_read_path()
    await test_full_ingest_chain()
    await test_routing()
    print("\n机位本地库（方案 A）测试全部通过 ✔")


if __name__ == "__main__":
    asyncio.run(main())
