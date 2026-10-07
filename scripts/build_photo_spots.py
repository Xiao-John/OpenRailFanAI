#!/usr/bin/env python3
"""构建本地「机位线索」库（`backend/data/dict.db` 的 `photo_spot_doc` 表，gitignored）。

为什么要本地化（2026-10-06 拍板）
--------------------------------
机位**不存在结构化数据源**，实测口径：
- OSM 全中国 `tourism=viewpoint` 共 7727 个，名称涉铁路的 17 个，真机位词（机位/拍车/看车）**3 个**；
- 12306 / rail.re / 黄河铁路网 / GTFS 均无拍摄点字段；
- 车迷自制机位站 `chinaonrails.org` 已不可达（DNS 解析到 23.91.96.190，80/443 全拒连）。

有效内容只存在于车迷社区（B 站/知乎/微博/海子网/什么值得买/搜狐…），且是**非结构化散文**。
因此本脚本的目标**不是**从散文里抽结构化点位（实测那条路必然失败，见下），而是：

    离线把「确实在讲某个地点拍车」的页面**连同正文一起**收进本地库；
    线上零延迟查表，由生成层读正文原文作答与引用。

为什么不用正则抽「点位+方位」字段
--------------------------------
现网 `web.search` 的相关性闸门就是纯词元判定：实测 `成昆铁路 关村坝站 拍车机位` 时，
百度返回 5 条成昆铁路**车次视频**、0 条含机位内容，闸门却判"相关 4 条"放行 ——
用户问机位，拿到一堆车次视频。用正则从"北侧 500 米天桥"这类散文里抽结构化字段是同一类错误。
**抽取目标换成"文档"**后，正则只做能硬校验的判定（见 `_judge`），点位描述留给生成层读懂。

三道硬闸门（宁可少，不可编）
----------------------------
1. **拍摄意图**：正文/标题必须有拍摄行为词，且**不得**只命中"摄影器材/旅游"语境；
2. **具体铁路实体**：必须命中本地站点库（3384 站）/ 线路表（758 条）/ 桥·道口·编组站形态词；
3. **地理相关**：实体或正文必须与**本次查询的地点**对得上 —— 否则是"别处的机位"，对用户无用。
   （实测反例：查上海黄渡，返回的却是"济南胶济线京沪线 8 大机位全攻略"。）

礼遇约束（对社区站点）
----------------------
低频（引擎请求间隔下限）、并发受限（默认 2）、可识别 UA、失败即跳过、**只在离线建库时跑**，
不在请求路径里联网。个人站点（海子网等）额外限制抓取条数。

用法
----
    # 小批量试跑（不写库，只看判定结果）
    python3 scripts/build_photo_spots.py --cities 北京,成都 --dry-run -v

    # 正式建库（默认只写入 DICT_DB_PATH 指向的库）
    python3 scripts/build_photo_spots.py --all --db /tmp/spot.db

    # 只统计现状
    python3 scripts/build_photo_spots.py --stats
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

# 与后端配置一致（DICT_DB_PATH 可覆盖；默认 backend/data/dict.db，已 gitignore）
_db_env = Path(os.environ.get("DICT_DB_PATH", "data/dict.db"))
DEFAULT_DB = _db_env if _db_env.is_absolute() else (BACKEND / _db_env)

# 正文入库上限：机位攻略多为长文，取 20k 字足够覆盖点位描述，同时避免库体积失控
PAGE_TEXT_MAX = 20_000
# 搜索接口自带正文的可用下限：太短（只是摘要）就仍走自建抓取
_PREFETCH_MIN_CHARS = 200
_PAGE_TEXT_HARD = 20_000
# 单条发现的引擎请求间隔下限（秒）—— 对搜索引擎保持低频
SEARCH_MIN_INTERVAL_S = 1.2
# 并发上限：对外部站点并发抓正文
FETCH_CONCURRENCY = 3

from app.data.photo_spots import SCHEMA, migrate as migrate_photo_schema, stamp as stamp_photo_revision



# --------------------------------------------------------------------------- 判定规则
# 拍摄行为词：核心是"机位/拍车/看车"这类车迷术语
# 「拍摄地点」强信号：**必须有**其中之一，文档才可能入档。
#
# 为什么单独加这一条（2026-10-06 用真实数据实测得出）：早先"有拍摄词 + 有铁路实体 + 地理匹配"
# 就放行，结果把一堆**与机位无关**的铁路文章收了进来 —— 实测 9 篇里 6 篇是这类：
# 《深圳西站送别纪念…K237/8 接送车活动》《山城轨道暴走 day3》《一往无前：成渝铁路与重庆火车站》。
# 它们命中"拍车/车迷"只是因为正文里提到，并非在讲机位。
# 机位是"地点"属性：文档必须**明确指出拍摄地点**才算线索。
# 标题里的"拍摄地点"词。分两级：
#   车迷专用（拍车/拍火车/看车/摄铁…）：出现即证明铁路题材，直接通过；
#   泛拍摄（机位/拍照/约拍/摄影/圣地…）：**必须**再满足铁路语境（见下方 _RAIL_CTX_RE），
#     否则《在迪士尼当公主的一天（附拍照机位)》《阿那亚拍照机位》会混进来。
# 为什么要收"约拍/拍照"这类泛词：实测《沈阳约拍圣地｜JK女孩的**火车公园**梦》
# 《沈阳火车头**拍照**圣地》标题里都没有"机位/拍车"，但正文写的是**克俭公园**
# —— 沈阳的经典拍车点（含"下午4-6点最佳""从皇姑屯地铁站下站"）。
# 只认"机位/拍车"会把这些真·大地点线索整批丢掉。
_SPOT_RE = re.compile(
    r"机位|拍车点|看车点|拍摄点|拍车地点|摄铁|拍火车|拍车|机位攻略|机位分享|"
    r"机位推荐|机位大全|机位合集|约拍|拍照|摄影|圣地")
# **地点形态词**：这些词本身就在回答"在哪里"（道口/立交桥/机务段…）。
# 与 `_RAIL_FORM_RE` 的区别：后者含 `铁路|高铁|客专` 等**题材词** ——
# 那些只说明"这跟铁路有关"，不说明地点。实测若拿它当"地点证据"，
# 《【日常拍车·南昌**铁路**】…》《【中国**铁路**】再遇SS9-0127》都会被当成"有地点"而放行。
_LANDMARK_FORM_RE = re.compile(
    r"[\u4e00-\u9fff]{2,10}(?:大桥|铁路桥|铁桥|道口|编组站|枢纽|展线|灯泡线|"
    r"隧道口|跨线桥|天桥|立交|联络线|线路所|站台|机务段|车辆段|动车所|折返段)")

# **车迷专用**拍摄词：出现即证明铁路题材（不必再找铁路词）。
# 与泛化的"拍照机位"区分开 —— 这是挡住迪士尼/阿那亚那类假阳性的关键：
# 它们的标题是"拍照机位"（通用摄影），而"拍车/拍火车/看车"只可能出现在铁路语境。
# ⚠️ "拍车"必须加**否定后顾**：它是"**法拍车**""**天天拍车**"的子串 ——
# 那是**汽车拍卖/二手车**业务，与铁路毫无关系。实测它们靠"车迷词命中"混过铁路语境关：
# 《包头法拍车提车代办…解押过户》《长沙天天拍车地址》《上海天天拍车-高价卖车》。
# 同理"看车"要防"看车展"之类，但实测暂无噪声，先只修已知的。
# 车次/车组号（标题内）：K4276次 / T254 / CR400AFZ / DF7C 等
_TRAIN_CODE_IN_TITLE_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:[GDCTZKYSLN]\d{1,4}|CRH?\d?[A-Z]*\d*|DF\d|HXD\d|SS\d)"
    r"|\d{1,4}次")
# "地点类"词：出现才算在讲"在哪里拍"
_SPOT_LOC_RE = re.compile(
    r"机位|地点|机位点|拍摄点|拍摄地|打卡地|场地|圣地|拍车点|看车点|机位合集|"
    r"机位分享|机位推荐|机位大全|拍照地|取景地|攻略|指南|合集|盘点|总结")

_RAILFAN_SHOOT_RE = re.compile(
    r"(?<![法天])拍车|拍火车|看车|蹲车|摄铁|拍机|追车|车迷")

# 铁路语境硬要求：出现任意一个才算铁路题材（挡掉影视/舞蹈/风光的"机位"）
_RAIL_CTX_RE = re.compile(
    r"铁路|铁道|火车|列车|车次|动车|高铁|机车|车迷|运转|编组站|道口|站台|车站|"
    r"线路所|客运段|机务段|车辆段|铁路局|局段|内燃|电力机车|韶山|和谐|复兴号|CRH|CR400")

# 核心拍摄语义词（宽于 _SPOT_RE，但仍比泛拍摄词窄）：用于判定"拍摄意图"
_SHOOT_CORE_RE = re.compile(r"机位|拍车|拍机|看车|蹲车|蹲守|拍火车|列车拍摄|拍车点|看车点|摄铁")
# 泛拍摄词：本身太宽（人像/风光/器材都能命中），必须**另有铁路实体**才算（见 _judge）
_SHOOT_WEAK_RE = re.compile(r"拍摄|取景|摄影|角度|机位图|打卡")
# 铁路实体形态（本地库之外的桥/道口/编组站/线路点）
_RAIL_FORM_RE = re.compile(
    r"[\u4e00-\u9fff]{2,10}(?:大桥|铁路桥|铁桥|道口|编组站|枢纽|展线|灯泡线|曲线|弯道|"
    r"隧道口|跨线桥|天桥|立交|联络线|客专|高铁|铁路|线路所)")

# 形态词匹配会**贪婪**吞进主语/动词（实测："从站台北侧天桥可以拍到列车通过弯道" 被整段切出来）。
# 实体名是要喂给生成层的，必须干净；这里用确定性的词首/词尾噪声表过滤。
# 为什么不改成正则加边界：中文没有词边界，靠正则做不了；噪声表可枚举、可复核、可回归。
_FORM_NOISE_PREFIX = (
    "从", "到", "去", "在", "往", "向", "由", "经", "过", "沿", "前", "后", "旁", "附近",
    "站台", "可以", "能够", "这里", "那里", "拍摄", "拍", "看", "到达", "前往", "位于",
    "距离", "靠近", "经过", "通过", "沿着", "沿着", "位于", "有一", "有个", "这个", "那个",
)
_FORM_NOISE_SUBSTR = (
    "可以", "能够", "拍摄", "拍到", "建议", "推荐", "适合", "怎么", "如何", "就是", "不是",
    "附近", "左右", "以内", "以外", "之后", "之前", "的时候", "的话", "顺着", "沿着",
)
# 句子片段填充词（多字）：以这些开头的候选是切出来的句子，不是地标名
_FILLER_LEAD = (
    "首先", "然后", "接着", "另外", "此外", "同时", "因此", "所以", "但是", "不过",
    "探访", "介绍", "前往", "位于", "沿着", "顺着", "经过", "通过", "距离", "靠近",
    "这里", "那里", "我们", "可以", "能够", "拍摄", "建议", "推荐", "适合", "主要是",
)

# 地标名长度上限：真实地标常带线路/地点前缀，如 "滨洲线松花江铁路大桥"(11)、
# "哈尔滨松花江大桥"(8)。上限设 12 以容纳这些，同时仍挡掉整句被吞的长串。
_FORM_MAX_LEN = 12


# 形态词后缀（用于把贪婪匹配**截到后缀处**）。匹配时按"最早结束 + 同位置最长"挑选，
# 不能只按元组顺序：否则"铁路桥"会被"铁路"抢先截断成残名（实测）。
_FORM_SUFFIXES = (
    "铁路桥", "跨线桥", "隧道口", "编组站", "线路所", "联络线", "灯泡线",
    "大桥", "铁桥", "道口", "枢纽", "展线", "天桥", "立交",
    "客专", "高铁", "铁路",
)
# 注意：`大桥`/`铁桥` 必须在表内，否则 "松花江铁路大桥" 会先被 "铁路" 截成
# "松花江铁路"，再补上线路前缀变成 "滨洲线松花江铁路" —— 一个**不存在**的地名（实测）。
# 匹配时按长度降序，保证"最长后缀优先"（铁路桥 > 大桥 > 铁路）。
_FORM_SUFFIXES_BY_LEN = tuple(sorted(_FORM_SUFFIXES, key=len, reverse=True))


def _cut_at_suffix(raw: str) -> str:
    """把贪婪匹配截到**最长形态后缀**结束处（同后缀取最早出现）。

    为什么按"最长后缀"而不是"最早结束"：
      "滨洲线松花江铁路大桥…" 里 "铁路" 结束得更早(8)，截出来是 "滨洲线松花江铁路" —— 
      一个**不存在**的地名；而最长后缀 "大桥" 结束于 10，截出正确的 "滨洲线松花江铁路大桥"。
      后缀越长越具体，必须优先。
    """
    s = (raw or "").strip()
    for suf in _FORM_SUFFIXES_BY_LEN:      # 已按长度降序
        i = s.find(suf)
        if i >= 0:
            return s[: i + len(suf)]
    return s


def _clean_form(raw: str) -> str | None:
    """清洗形态词实体：先截到后缀，再剥词首介词，最后校验。

    实测的贪婪匹配形态与期望：
      "滨洲线松花江铁路大桥拍车机位"        → "滨洲线松花江铁路大桥"
      "前往西四环丰台铁路大桥附近收录火车"   → "西四环丰台铁路大桥"
      "哈尔滨松花江大桥摄影攻略"            → "哈尔滨松花江大桥"
      "从站台北侧天桥可以拍到列车通过弯道"   → 弃（方位描述/动词短语，不是地标名）
    """
    s = _cut_at_suffix(raw)
    if not s:
        return None

    # 剥词首介词：**必须按长度降序**，否则"前"会先于"前往"命中，剥成"往西四环…"（实测）
    for pre in sorted(_FORM_NOISE_PREFIX, key=len, reverse=True):
        if s.startswith(pre) and len(s) > len(pre) + 1:
            s = s[len(pre):]
            break

    if len(s) < 3 or len(s) > _FORM_MAX_LEN:
        return None
    # 必须以形态后缀收尾：挡掉"拍到列车通过弯道"这类**动词短语**误匹配
    if not s.endswith(_FORM_SUFFIXES):
        return None
    # 且不得以多字填充词开头 —— 那说明切到的是**句子片段**而不是地标名
    # （实测泄漏：'首先来介绍一下南翔编组站'、'探访摄影南翔编组站'）。
    # 注：只判**多字**词，不判单字 —— "探"、"介" 开头的真实地名（如 探沂）不能被误杀。
    for fill in _FILLER_LEAD:
        if s.startswith(fill):
            return None
    # 纯方位描述不是地标名（"站台北侧天桥"里的"北侧"只说明方位）
    for loc in ("北侧", "南侧", "东侧", "西侧", "左侧", "右侧", "附近", "一带", "周边"):
        if loc in s:
            return None
    return s or None
# 拍摄语境词（与实体同现时提高置信度）
_CONTEXT_RE = re.compile(
    r"机位|拍车|看车|取景|构图|焦段|镜头|长焦|逆光|顺光|侧光|上午|下午|傍晚|日落|日出|"
    r"时刻表|几点|时刻|角度|机位图|乘车|地铁|公交|步行|到达|导航|定位|坐标")

# 泛化页黑名单：百科/政务/旅游/电商/无关商业站（域名级）
_BAD_DOMAIN_RE = re.compile(
    r"baike\.baidu\.com|wikipedia\.org|wikiwand|gov\.cn|"
    # 注意：**不拦 UGC 点评站**（大众点评/美团）。
    # 实测《沈阳火车头拍照圣地》《坐标沈阳·绿皮火车的公园》正是含"克俭公园"的真实
    # 用户点评（带最佳时段与到达方式）—— 早先把 dianping 当"旅游站"拦掉是误杀。
    # 拦的是**旅游预订/攻略聚合**站（携程/去哪儿/马蜂窝），它们产出的是通用旅游文案。
    r"ctrip|qunar|mafengwo|trip\.com|ly\.com|visitbeijing|"
    r"jd\.com|taobao|tmall|kuaidi100|express|job|zhipin|51job|"
    r"weather\.com|tianqi|airquality|tuchong|图虫|vcg\.com|699pic|zcool", re.I)
# 泛化页黑名单：标题级（旅游攻略/百科/政务/新闻通稿）
_BAD_TITLE_RE = re.compile(
    r"百度百科|维基百科|旅游攻略|必去|好玩的地方|十大|景点|门票|天气|"
    r"市人民政府|人民政府|政务|政策|通知公告|招标|公示|"
    r"招聘|快递|单号查询|军事|抗战|历史|票价查询|时刻表查询|"
    # 汽车拍卖/二手车业务（"拍车"的同形词陷阱，实测混入过）
    r"法拍车|天天拍车|二手车|拍卖|竞拍|过户|解押|代办|卖车|收购|估值|"
    # 法律问答（"铁路边拍火车违法吗"这类是咨询帖，不含机位信息）
    r"违法|处罚|罚款|拘留|规定|条例|"
    # "拍车门"是**盗窃手法**（拍车门盗窃），不是拍火车 —— 实测混入 CCTV《天网》法治节目
    r"拍车门|盗窃|智擒|扒窃|"
    # **虚拟/游戏**拍车（火车模拟器）没有真实地点，对机位库无用
    r"虚拟|模拟器|游戏|MSTS|RailWorks|TS20|模拟火车")

# 正文抓取白名单：正文可抓（实测 B 站 ✅ / 搜狐 ✅ / 什么值得买 ✅）；其余只存摘要
FETCHABLE_DOMAIN_RE = re.compile(
    r"bilibili\.com|sohu\.com|smzdm\.com|toutiao\.com|hasea\.com|"
    r"bbs\.|tieba\.baidu\.com|weibo\.c|xiaohongshu|douyin\.com|"
    r"thepaper\.cn|163\.com|qq\.com|ifeng\.com|sina\.com\.cn", re.I)

# 已知抓不到正文的站（实测 403/412）：只存摘要，并在 reasons 里如实标注
BLOCKED_DOMAIN_RE = re.compile(r"zhihu\.com|baike\.baidu\.com|bilibili\.com/read", re.I)

_SOURCE_MAP = (
    ("bilibili.com", "bilibili"), ("sohu.com", "sohu"), ("zhihu.com", "zhihu"),
    ("smzdm.com", "smzdm"), ("toutiao.com", "toutiao"), ("hasea.com", "hasea"),
    ("weibo.c", "weibo"), ("tieba.baidu", "tieba"), ("xiaohongshu", "xiaohongshu"),
    ("thepaper", "thepaper"), ("163.com", "netease"),
)


def source_of(domain: str) -> str:
    d = (domain or "").lower()
    for k, v in _SOURCE_MAP:
        if k in d:
            return v
    return d or "unknown"


# 正文里出现的站点标识 → 真实来源。为什么需要：百度结果的 URL 是跳转链（见 `one()` 注释），
# 域名信息只能从**页面正文**反推；这一步只用于"来源标注"，判定仍以正文内容为准。
_CONTENT_SOURCE = (
    ("哔哩哔哩", "bilibili"), ("bilibili", "bilibili"),
    ("搜狐", "sohu"), ("知乎", "zhihu"), ("什么值得买", "smzdm"),
    ("今日头条", "toutiao"), ("海子铁路网", "hasea"), ("百度贴吧", "tieba"),
    ("微博", "weibo"), ("小红书", "xiaohongshu"), ("澎湃", "thepaper"),
    ("网易", "netease"), ("腾讯", "tencent"), ("新浪", "sina"),
)


def _domain_from_content(text: str, fallback: str) -> str:
    head = (text or "")[:3000]
    for kw, name in _CONTENT_SOURCE:
        if kw in head:
            return name
    return fallback


# --------------------------------------------------------------------------- 本地实体表
class LocalEntities:
    """本地站点库（3384 站）+ 线路表（758 条），用于**硬校验**实体是否真实存在。

    为什么要对着本地库校验：正则能切出"石门子站""大虹桥"这类**假实体**，
    只有"该名字确实在 12306 站点库/线路表里"才算数 —— 这是本项目一贯的站点质量纪律。
    """

    def __init__(self) -> None:
        self.stations: set[str] = set()
        self.lines: set[str] = set()
        self._loaded = False

    async def load(self) -> None:
        if self._loaded:
            return
        from app.tools import _rt12306 as rt

        try:
            await rt.ensure_loaded()
            self.stations = {str(n) for n in rt.all_stations().keys()}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 站点库加载失败，实体校验将退化：{type(e).__name__}: {e}")
        if DEFAULT_DB.exists():
            with sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True) as c:
                self.lines = {str(r[0]) for r in c.execute("SELECT line FROM line_master")}
        self._loaded = True

    def station_in_text(self, text: str) -> list[str]:
        """文本中出现的**真实**站名（最长优先，避免"北京"吃掉"北京南"）。"""
        hits = [s for s in self.stations if len(s) >= 2 and s in text]
        # 去重：若"北京南"命中，则"北京"不再单独计（除非它单独出现且贡献信息）
        hits.sort(key=len, reverse=True)
        kept: list[str] = []
        for h in hits:
            if not any(h in k and h != k for k in kept):
                kept.append(h)
        return kept

    def line_in_text(self, text: str) -> list[str]:
        return [l for l in self.lines if len(l) >= 3 and l in text]


# --------------------------------------------------------------------------- 判定
def _judge(title: str, url: str, snippet: str, page_text: str,
           scope: str, ent: LocalEntities, scope_entities: list[str]) -> tuple[bool, list[str], list[str]]:
    """三道硬闸门。返回 (是否入库, 命中实体, 判定理由)。

    只做能硬校验的判定；**不**从正文抽取点位与方位字段。
    """
    reasons: list[str] = []
    # ⚠️ 意图判定**只看标题+摘要**，不看长正文。
    # 为什么：接入阿里云后每条结果自带 300–3200 字正文，若拿整篇判意志必"偶然命中"——
    # 实测《在迪士尼当公主的一天（附拍照机位）》《阿那亚拍照机位，带娃拍出大片感》
    # 《沧州晚上火车站有玩的地方吗》全部被放行（长正文里既有"机位"也有铁路词）。
    # 人分诊也是先看标题：一篇文章讲什么，标题最诚实。正文只用来抽实体、定地点。
    # ⚠️ 只信**标题**，不信 snippet：搜索摘要由查询生成，会**回显查询里的词**——
    # 实测查询"福州 拍火车机位"时，《福州镜沙黑沙滩，拍照机位穿搭交通全在这》（旅游文）
    # 的摘要里就带"拍火车机位"，于是被"车迷拍摄词命中"放行。摘要是查询的回声，不是页面的自述。
    head = title
    blob = f"{title} {snippet} {page_text[:6000]}"

    if _BAD_DOMAIN_RE.search(url):
        return False, [], ["拒：泛化域名（百科/政务/旅游/电商）"]
    if _BAD_TITLE_RE.search(title):
        return False, [], ["拒：泛化标题（旅游/百科/政务/新闻通稿）"]

    # 第一道：**标题/摘要**必须明确指出拍摄地点（机位/拍车点…）。
    if not _SPOT_RE.search(head):
        return False, [], ["拒：标题未指明拍摄地点（无机位/拍车点类词）"]

    # 第二道：**标题/摘要**必须是铁路题材。
    # "机位"是通用影像词（影视/舞蹈/风光都在用），必须有铁路词佐证；
    # 判据 = 关键词表 ∪ 本地线路表 ∪ 本地站名表（后两者是白名单，天然准确）。
    # 判据分两级：
    #   ① 标题含**车迷专用**拍摄词（拍车/拍火车/看车…）→ 本身就是铁路题材，直接通过；
    #   ② 只含泛化的"机位/拍照机位"→ 必须另有铁路词或**本地线路名**佐证。
    # ⚠️ 刻意**不**用"站名"作佐证：`上海`/`秦皇岛` 本身就是站名库里的站名，
    # 于是《在迪士尼当公主的一天（附拍照机位)》《阿那亚拍照机位》都会被判成铁路题材（实测踩到）。
    # 站名太容易与城市名重合，做"题材"证据不可靠；做"定位"证据才可靠（见下方 scope）。
    title_forms = [c for c in (_clean_form(m.group(0))
                               for m in _RAIL_FORM_RE.finditer(head)) if c]
    if not (_RAILFAN_SHOOT_RE.search(head)
            or _RAIL_CTX_RE.search(head)
            or ent.line_in_text(head)
            or title_forms):
        return False, [], ["拒：标题非铁路题材（机位为通用影像词，需铁路词佐证）"]

    has_core = bool(_SHOOT_CORE_RE.search(blob))
    if not has_core:
        return False, [], ["拒：无车迷拍摄词"]

    # 第三道：区分"**在哪里拍**"与"**拍了哪趟车**"。
    # 实测《（国铁拍车好货分享，4K超清）…K4276次包头市内运转纪实》《（云拍车）T254通过》
    # 《南局福段拍车CR400AFZ》都在讲"拍了哪趟车"，**不含任何机位信息**，
    # 对机位库是噪声。判据：标题里有车次号/车组号、却没有任何"地点类"词 → 判为运转视频。
    # "地点"判据必须包含**地标形态词**（道口/立交桥/机务段/站台/大桥…）——
    # 它们正是"在哪里拍"的答案。只用一个固定词表会**误杀真机位**：实测把
    # 《兰新铁路兰州五泉南路**立交桥**拍车——K377次》《…通过西**机务段路道口**》
    # 《煤八路**道口**首遇DF4D》《通过广深线**射击场道口**》全判成了运转视频。
    # 故：地点 = 地点类词 ∪ 地标形态词 ∪ 本地线路名（**不含裸站名** ——
    # "南昌站拍车日常"只说站在哪，没给机位）。
    has_loc = (bool(_SPOT_LOC_RE.search(head)) or bool(_LANDMARK_FORM_RE.search(head))
               or bool(ent.line_in_text(head)))
    if _TRAIN_CODE_IN_TITLE_RE.search(head) and not has_loc:
        return False, [], ["拒：车次运转视频（只有车次、无机位/地点）"]

    # 具体铁路实体：本地站点库 + 线路表 + 桥/道口形态词
    stations = ent.station_in_text(blob)
    lines = ent.line_in_text(blob)
    forms = [c for c in (_clean_form(m.group(0)) for m in _RAIL_FORM_RE.finditer(blob)) if c]
    entities = list(dict.fromkeys(stations[:6] + lines[:6] + forms[:8]))
    if not entities:
        return False, [], ["拒：无具体铁路实体（本地库校验未通过）"]

    # 弱拍摄词必须另有"车迷语境"，否则很可能是人像/风光/器材文
    if not has_core:
        if not _CONTEXT_RE.search(blob):
            return False, [], ["拒：仅泛拍摄词且无拍摄语境（疑似风光/器材文）"]
        reasons.append("弱信号：泛拍摄词+语境词命中")
    else:
        reasons.append("强信号：车迷拍摄词命中")

    # 地点：文档必须能定到一个**具体地点**，但**不要求等于查询城市**。
    # 为什么去掉"地理不匹配就拒"：查询只是**发现探针**，不是归属声明。
    # 实测查"上海"时返回的《济南胶济线京沪线拍车,8大机位全攻略》是**真实机位攻略**，
    # 它该被收进库里（挂在"济南"下），而不是因为"不是上海"被丢掉 ——
    # 丢掉只是白白浪费一次发现机会，收下反而丰富了济南的覆盖。
    scope_hit = bool(scope and scope in blob)
    scope_hit = scope_hit or any(se in blob for se in scope_entities if len(se) >= 2)
    # 实体命中亦可作为"可定位"的证据（如标题里的站名/线路名）
    if not scope_hit and not entities:
        return False, [], ["拒：无法定位到具体地点"]
    reasons.append(f"地点：{scope or '（按标题实体）'}")

    reasons.append("实体：" + "/".join(entities[:5]))
    return True, entities, reasons


# --------------------------------------------------------------------------- 发现
class Finder:
    """候选发现：复用项目既有的 `web.search`（Bing 中国 / 百度）。

    为什么复用而不自己爬搜索页：实测 B 站搜索 API 返回 412、知乎 403，
    独立爬取发现层不成立；且 `web.search` 已带引擎级相关性闸门与正文抓取。

    `engine` 可定向到单一引擎（`baidu` / `bing` / `auto`）：
    2026-10-06 实测 Bing 中国会返回**退化结果**（查"成昆铁路"返回汉字「成」的字典词条），
    而 `web.search` 的引擎闸门可能把这类噪声判为"相关"；定向到百度可绕过该噪声。
    `min_interval` 是**硬限流**：建库对搜索引擎是低频离线行为，间隔必须足够大，
    否则会把出口打成验证码（本次实测约 70 次检索即触发）。
    """

    def __init__(self, ent: LocalEntities, verbose: bool = False,
                 engine: str = "auto", min_interval: float = SEARCH_MIN_INTERVAL_S,
                 sites: list[str] | None = None, aliyun_engine: str = "lite",
                 num_results: int = 50, dump: bool = False) -> None:
        self.ent = ent
        self.verbose = verbose
        self.engine = engine
        self.min_interval = max(0.0, float(min_interval))
        self.sites = sites or []
        self.aliyun_engine = aliyun_engine
        self.num_results = int(num_results or 50)
        self.dump = bool(dump)
        self._last = 0.0
        # 额度耗尽标记：置位后调用方**立即中止整轮**（见 AliyunQuotaExhausted 注释）
        self.quota_exhausted = False
        self.calls = 0
        self.raw_dump = {} if getattr(self, "dump", False) else None

    #: 落盘的原始结果（`--dump-json` 用）：查询 → 结果列表
    raw_dump: dict = None  # type: ignore[assignment]

    async def search(self, query: str, limit: int = 6) -> tuple[bool, list[dict], int, str]:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            if self.verbose:
                print(f"    [限流] 等待 {wait:.0f}s …")
            await asyncio.sleep(wait)
        self._last = time.time()

        if self.engine == "aliyun":
            return await self._search_aliyun(query, limit)
        if self.engine == "qianfan":
            return await self._search_qianfan(query, limit)
        if self.engine in ("baidu", "bing"):
            return await self._search_direct(query, limit)
        from app.tools import registry

        try:
            r = await registry.invoke_by_name("web.search", {"q": query, "limit": limit})
        except Exception as e:  # noqa: BLE001
            return False, [], 0, f"{type(e).__name__}: {e}"
        if not r.ok:
            return False, [], 0, (r.error or "搜索失败")[:120]
        results = (r.data or {}).get("results") or _parse_from_text(r.text)
        return True, results, len(results), ""

    async def _search_aliyun(self, query: str, limit: int) -> tuple[bool, list[dict], int, str]:
        """阿里云 CleverSee / IQS 统一搜索 —— 建库首选通道。

        为什么首选：`contents.mainText=true` 让**每条结果直接带原文正文**（实测 300–3200 字），
        建库因此**不需要再抓网页**（自建抓取知乎/百度百科 403 是常态）。
        另外 `CNLiteBasic` 单次最多 50 条且不计搜索 credit，控制台 QPS 为 10，适合批量。
        """
        from app.tools.aliyun_search import (
            AliyunQuotaExhausted, AliyunUnavailable, search as iqs_search)

        try:
            self.calls += 1
            results = await iqs_search(
                query, engine=self.aliyun_engine,
                num_results=max(self.num_results, limit), main_text=True,
                # summary 单独计 credit，而 mainText 已含正文 → 默认不开
                summary=False,
            )
        except AliyunQuotaExhausted as e:
            # 额度用尽 → 置位，由调用方中止整轮（不要继续把剩下的查询全打成 403）
            self.quota_exhausted = True
            return False, [], 0, f"额度用尽：{e}"
        except AliyunUnavailable as e:
            return False, [], 0, f"阿里云不可用：{e}"
        out = []
        for x in results:
            out.append({
                "title": x.get("title") or "",
                "url": x.get("url") or "",
                "snippet": x.get("snippet") or "",
                # 正文随结果一起带回 → 交给 one() 直接用
                "prefetched_text": x.get("content") or "",
                "date": x.get("date") or "",
                "website": x.get("website") or "",
                "authority_score": x.get("authority_score"),
            })
        return True, out, len(out), ""

    async def _search_qianfan(self, query: str, limit: int) -> tuple[bool, list[dict], int, str]:
        """官方百度 AI 搜索（千帆）—— 推荐通道。

        为什么不走网页版：批量取数会触发反爬（实测约 70 次即图形验证码、Bing 结果退化），
        且网页版百度返回 `baidu.com/link?url=` **跳转链**，拿不到真实域名。
        官方 API 不反爬、`url` 是真实地址、且**官方支持 `search_filter.match.site` 定向站点**
        （`site:` 在网页版引擎上实测无效）。`content` 是 ≤2000 字片段，多数情况可直接用。
        """
        from app.tools.qianfan_search import QianfanUnavailable, web_search

        try:
            results = await web_search(query, top_k=max(limit, 10), sites=self.sites or None)
        except QianfanUnavailable as e:
            return False, [], 0, f"千帆不可用：{e}"
        # 归一化到本脚本内部结构；content 当作 snippet 的更完整替代
        out = []
        for x in results:
            out.append({
                "title": x.get("title") or "",
                "url": x.get("url") or "",
                "snippet": x.get("content") or x.get("snippet") or "",
                "date": x.get("date") or "",
                "website": x.get("website") or "",
                "authority_score": x.get("authority_score"),
            })
        return True, out, len(out), ""

    async def _search_direct(self, query: str, limit: int) -> tuple[bool, list[dict], int, str]:
        """直接打单一引擎并解析（绕过 `web.search` 的引擎选择与闸门）。

        为的是把"取数"与"判定"分开：引擎噪声交给本脚本的三道硬闸门处理，
        而不是让上游闸门替我们决定——上游闸门只判关键词沾边，会把退化结果放行（实测）。
        """
        import importlib.util

        from urllib.parse import quote

        from app.tools._http import BROWSER_HEADERS, format_error, get_client

        if self.engine == "baidu":
            url = f"https://www.baidu.com/s?wd={quote(query)}"
            parser_path = ("_parse_baidu",)
        else:
            url = f"https://cn.bing.com/search?q={quote(query)}"
            parser_path = ("_parse_bing",)

        try:
            client = await get_client()
            resp = await client.get(url, headers=BROWSER_HEADERS)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return False, [], 0, format_error(e)[:120]

        text = resp.text
        if "wappass" in str(resp.url) or "安全验证" in text[:3000]:
            return False, [], 0, f"{self.engine} 触发人机验证（需冷却后重试）"

        # 复用 web_search 的解析器，避免两份正则漂移
        try:
            mod = importlib.import_module("app.tools.web_search")
            parser = getattr(mod, parser_path[0])
            results = parser(text, limit)
        except Exception as e:  # noqa: BLE001
            return False, [], 0, f"解析失败 {type(e).__name__}"
        return True, results, len(results), ""

    async def run(self, query: str, scope: str, scope_entities: list[str],
                  limit: int = 6) -> tuple[bool, int, int, str, list[dict]]:
        ok, results, raw, note = await self.search(query, limit)
        if not ok:
            return False, 0, raw, note, []
        # `--dump-json`：把**原始结果（含正文）**落盘。
        # 为什么需要：闸门规则是在真实数据上反复迭代的（同形词、地点词、运转视频…），
        # 每改一次规则就要重跑一遍查询 —— 既花额度又慢。落盘后可**离线反复复判**。
        if self.raw_dump is not None:
            self.raw_dump[query] = [dict(r) for r in results]
        kept = []
        for r in results:
            title = r.get("title") or ""
            url = r.get("url") or ""
            snippet = r.get("snippet") or ""
            if not url:
                continue
            good, entities, reasons = _judge(
                title, url, snippet, "", scope, self.ent, scope_entities)
            if good:
                # ⚠️ 必须把**搜索接口自带的正文**一并带出去。
                # 这里曾经漏掉 `prefetched_text`，导致 `one()` 拿不到正文、
                # 一律回退去抓网页 —— 而点评/汽车之家/优酷/百度系页面是 JS 渲染或 403，
                # 于是**整批被丢弃**（实测：全国盘点形态初判命中 337 篇，最终只入库 2 篇）。
                # 等于把阿里云 `mainText` 这个最大优势白白浪费了。
                kept.append({"title": title, "url": url, "snippet": snippet,
                             "prefetched_text": r.get("prefetched_text") or "",
                             "entities": entities, "reasons": reasons,
                             "date": r.get("date") or ""})
        return True, len(kept), raw, "", kept


def _parse_from_text(text: str) -> list[dict]:
    """兜底解析（`web.search` 的 data.results 缺失时用它的渲染文本）。"""
    out, cur = [], None
    for raw in (text or "").split("\n"):
        line = raw.rstrip()
        m = re.match(r"^(\d+)\.\s*(?:\[([^\]]*)\]\s*)?(.+)$", line.strip())
        if m and len(m.group(3)) > 4:
            if cur:
                out.append(cur)
            cur = {"title": m.group(3).strip(), "date": m.group(2) or "",
                   "url": "", "snippet": ""}
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("http"):
            cur["url"] = s
        elif s and not s.startswith("【"):
            cur["snippet"] += " " + s
    if cur:
        out.append(cur)
    return [r for r in out if r["url"]]


# --------------------------------------------------------------------------- 抓正文
async def fetch_body(url: str, *, allow_private: bool = False) -> tuple[bool, str, str]:
    """抓正文原文。返回 (是否成功, 文本, 说明)。失败不抛异常，交给调用方如实标注。

    `allow_private` 仅供**本地测试**（起本地 HTTP 服务喂 fixture 页面）时绕过 SSRF 校验；
    正常建库必须保持 False —— 真实数据源都是公网站点，放行内网等于开放 SSRF。
    """
    from app.tools._http import format_error, get_text, html_to_text

    try:
        html = await get_text(url, max_bytes=900_000, allow_private=allow_private)
        text = html_to_text(html, max_len=PAGE_TEXT_MAX)
        if len(text) < 40:
            return False, "", "正文过短（可能为脚本渲染页）"
        return True, text, ""
    except Exception as e:  # noqa: BLE001
        return False, "", format_error(e)[:120]


# --------------------------------------------------------------------------- 种子清单
# 为什么用「城市 + 已知线路/枢纽」而不是裸城市名：实测裸城市名（吉林市/北京南站 + 拍车机位）
# 会让引擎整体退化为百科与旅游页；必须给出**两个以上具体铁路实体**才进入车迷内容区。
SEED_CITIES = [
    "北京", "上海", "广州", "深圳", "成都", "重庆", "哈尔滨", "西安", "武汉", "郑州",
    "南京", "杭州", "昆明", "兰州", "乌鲁木齐", "沈阳", "长春", "济南", "太原", "南昌",
    "长沙", "南宁", "贵阳", "福州", "合肥", "石家庄", "呼和浩特", "银川", "西宁", "拉萨",
    # —— 第二批（2026-10-06 扩种子：额度尚有余量，把覆盖铺到更多地级市/枢纽）——
    "天津", "苏州", "无锡", "常州", "徐州", "宁波", "温州", "嘉兴", "绍兴", "金华",
    "厦门", "泉州", "佛山", "东莞", "珠海", "中山", "汕头", "湛江", "桂林", "柳州",
    "洛阳", "开封", "新乡", "安阳", "襄阳", "宜昌", "十堰", "株洲", "衡阳", "岳阳",
    "九江", "赣州", "上饶", "芜湖", "蚌埠", "阜阳", "连云港", "南通", "扬州", "盐城",
    "淄博", "潍坊", "烟台", "威海", "临沂", "济宁", "泰安", "德州", "聊城", "菏泽",
    "包头", "大同", "临汾", "运城", "长治", "榆林", "宝鸡", "咸阳", "渭南", "汉中",
    "天水", "嘉峪关", "张掖", "酒泉", "敦煌", "格尔木", "库尔勒", "哈密", "吐鲁番", "喀什",
    "齐齐哈尔", "牡丹江", "佳木斯", "大庆", "吉林", "通化", "丹东", "鞍山", "锦州", "营口",
    "绵阳", "德阳", "广元", "南充", "宜宾", "泸州", "内江", "攀枝花", "西昌", "达州",
    "遵义", "六盘水", "安顺", "凯里", "大理", "丽江", "曲靖", "玉溪", "楚雄", "日喀则",
]
# 公认有拍车价值/机位攻略的线路与枢纽（用于生成"线路+地点"实体对）
SEED_LINES = [
    "京沪线", "京广线", "京哈线", "陇海线", "兰新线", "成昆铁路", "宝成线", "襄渝线",
    "沪昆线", "京九线", "滨洲线", "滨绥线", "京承线", "京包线", "青藏线", "川黔线",
    "京沪高速线", "京广高速线", "哈大高速线", "成贵客专", "西成客专", "大丽铁路",
]
# 与线路配套的知名拍车地标（桥/道口/展线/编组站）——用于生成"实体+实体"查询。
# 注意：这份清单只是**检索种子**（用来把引擎引导进车迷内容区），不是"机位结论"；
# 真正入库的东西必须来自抓到的页面正文，且经三道闸门校验。人工清单不会直接进库。
SEED_LANDMARKS = [
    "丰台大桥", "武汉长江大桥", "南京长江大桥", "松花江大桥", "关村坝", "金口河",
    "石门子", "黄渡", "水南庄", "达坂城", "二郎庙", "观音山", "乌鞘岭", "沙坡头",
    "钱塘江大桥", "郑州北站", "丰台西编组站", "沈阳北站", "向塘", "青龙桥",
]

# 线路 + 本地库大站（"次"形态，实测 3/10；作为地标清单的补充，不依赖人工）
SEED_LINE_STATIONS = [
    ("京沪线", "徐州"), ("京沪线", "蚌埠"), ("京广线", "信阳"), ("成昆铁路", "峨眉"),
    ("滨洲线", "齐齐哈尔"), ("陇海线", "宝鸡"), ("京哈线", "锦州"), ("沪昆线", "株洲"),
    ("宝成线", "秦岭"), ("襄渝线", "安康"),
]


# 查询形态按**实测命中率**排序（2026-10-06 本机实测，走 web.search→百度）：
#   线路+地标（两个具体铁路实体）  …… 4/4   ★主力
#   线路+本地库大站               …… 3/10
#   地标+机位                    …… 2/6
#   城市+线路                    …… 0/12  ✗ 弃用
#   裸城市/裸站名+机位            …… 0/8   ✗ 弃用
# 为什么弃用城市类：城市名会把结果整体拉回"旅游/百科"语境（实测 0/6 城市查询全部返回省级百科与旅游攻略），
# 而车迷内容只在"具体铁路实体"语境下才被检索到。这是本项目机位问题的**根因**，不是调参能绕过的。
_QUERY_SHAPES = (
    ("line+landmark", "★主力"),
    ("landmark", "次"),
    ("line+station", "次"),
)


# 大地点**类型词**与**盘点词**（2026-10-06 用户洞察 + 实测验证）
#
# 为什么加这两组：机位的**具体点位**多靠口口相传与评论区，但**大地点**（公园/大桥/天桥/道口）
# 网上搜得到，且常被写成"盘点/圣地/大全"类文章 —— **一篇盘点文就含多个大地点**。
# 实测（沈阳）：`沈阳 铁路 天桥 拍车` 一条查询就取到
#   《沈阳车迷必去的五大拍车圣地》(老道口桥)、《本人常去机位大盘点》(三洞桥/火车头交园)、
#   《沈阳约拍圣地｜JK女孩的火车公园梦》(**克俭公园**，含最佳时段与到达方式)。
# 反例：直接搜地标名 `克俭公园 拍火车机位` 只回 34 条**无关**内容（引擎不认这种地名），
# 所以靠"类型词 + 盘点词"去撞盘点文，比逐个点名地标有效得多。
# 第一批：通用地物词（实测 道口/立交桥/编组站 最强 —— 都是**铁路设施名词**）
_LANDMARK_TYPES = ("公园", "大桥", "天桥", "道口", "立交桥", "编组站")
# 第二批：**铁路设施名词**（沿第一批的制胜规律扩展，2026-10-06）
# 为什么单列一组：上一轮得出"设施名词 > 通用地物词 > 盘点词"，
# 故继续沿这个方向扩：机务段/车辆段/动车所（整备场是经典机位）、
# 站台/站场（站内视角）、跨线桥/隧道口（经典取景位）、折返段/编组场。
_RAIL_FACILITY_TYPES = (
    "机务段", "车辆段", "动车所", "站台", "跨线桥", "隧道口",
    "折返段", "站场", "编组场", "老站", "铁路公园",
)
_ROUNDUP_WORDS = ("圣地", "盘点", "大全", "合集")


# **全国性盘点查询**（2026-10-06 实测：这是通过率最高的形态，87.5%）
#
# 为什么单独一组、且**不带城市名**：盘点文几乎都是"全国/各地"口径，标题里不带具体城市，
# 因此**城市前缀反而把它挡在召回之外** —— 上一轮用 `{城市} 拍车 盘点` 只有 40% 通过率，
# 而 `全国各地 拍车机位 汇总` 一条就命中《北京5个拍车机位超全总结》《南宁拍车别瞎跑！这9个
# 点位直接封神》《上海8大拍车机位指南》等整篇盘点。
#
# 为什么价值高：**一篇盘点含多个城市/多个大地点**，且配合 `photo.spot` 的**正文匹配**，
# 任何一个被提到的城市去查都能命中它 —— 单位查询的覆盖增量远高于城市查询。
# 代价也低：与城市数无关（几十条而非上万条），额度花得少。
_ROUNDUP_BASES = ("拍车机位", "拍火车机位", "铁路机位", "火车机位", "拍车点",
                  "看车点", "铁路摄影", "铁道摄影")
_ROUNDUP_SUFFIX = ("汇总", "大全", "合集", "盘点", "推荐", "超全总结", "地图", "攻略",
                   "整理", "收藏", "一览", "精选", "宝典", "清单")
_ROUNDUP_PREFIX = ("全国各地", "全国", "中国铁路", "国内", "全网", "各省", "各地")


def _national_roundup_queries() -> list[tuple[str, str, str, list[str]]]:
    """生成全国性盘点查询（scope 定为"全国"，实际地点由标题实体/正文决定）。"""
    out = []
    for pre in _ROUNDUP_PREFIX:
        for base in _ROUNDUP_BASES[:3]:
            out.append((f"{pre} {base} 汇总", "全国", "national", []))
            out.append((f"{pre} {base} 大全", "全国", "national", []))
            out.append((f"{pre} {base} 合集", "全国", "national", []))
    for base in _ROUNDUP_BASES:
        for suf in _ROUNDUP_SUFFIX:
            out.append((f"{base} {suf}", "全国", "national", []))
    # 探针里表现好的"整理/一览/精选"类说法（用户洞察：车迷实际用词）
    out.append(("各地拍车机位 整理", "全国", "national", []))
    out.append(("拍车点 精选 全国", "全国", "national", []))
    out.append(("火车迷 机位 收藏", "全国", "national", []))
    out.append(("各省 拍车机位", "全国", "national", []))
    out.append(("铁路拍摄地点 汇总", "全国", "national", []))
    return out


def build_queries(engine: str = "auto", shapes: set[str] | None = None,
                  types: tuple[str, ...] | None = None,
                  roundups: tuple[str, ...] | None = None
                  ) -> list[tuple[str, str, str, list[str]]]:
    """生成发现查询。返回 (query, scope, scope_kind, scope_entities)。

    `engine=qianfan` 用**另一套形态**：官方 API 支持 `search_filter.match.site` 定向站点，
    检索质量随即变化 —— 实测直接取到《上海铁路枢纽三十大著名机位推荐》
    《全国各地热门拍车机位大汇总》《北京地区必去的机位合集》等**整篇攻略**。
    因此不再需要"两个具体实体"去把网页版引擎逼进车迷语境，改为覆盖面更广的
    "城市/线路 + 拍火车机位"。

    非 qianfan（网页版引擎）仍用实体对形态：实测裸地名会让引擎退回旅游/百科语境
    （查"吉林市 铁路拍摄 机位"返回吉林市百度百科）。
    """
    out: list[tuple[str, str, str, list[str]]] = []
    # shapes 控制生成哪些形态，用于**按额度分配**：
    #   base=原有（城市/线路/地标）· type=大地点类型词 · roundup=盘点词
    use = shapes if shapes else {"base", "type", "roundup"}

    if str(engine) == "qianfan":
        if "base" in use:
            # 1) 城市 × 机位（site 定向后覆盖面最广）
            for c in SEED_CITIES:
                out.append((f"{c} 拍火车机位", c, "city", [c]))
                out.append((f"{c} 铁路 拍车点", c, "city", [c]))
            # 2) 线路/枢纽 × 机位
            for ln in SEED_LINES:
                out.append((f"{ln} 拍火车机位", ln, "line", [ln]))
            # 3) 地标（精确点）
            for lm in SEED_LANDMARKS:
                out.append((f"{lm} 机位", lm, "landmark", [lm]))
        if "type" in use:
            # 4) 大地点**类型词**：撞"某类地点"的内容（公园/大桥/天桥/道口…）
            #    带"铁路"二字（实测 `沈阳 铁路 天桥 拍车` 比 `沈阳 拍车 天桥` 命中更好）
            for c in SEED_CITIES:
                for t in (types or _LANDMARK_TYPES):
                    out.append((f"{c} 铁路 {t} 拍车", c, "city", [c]))
        if "national" in use:
            # **全国性盘点**（通过率最高；与城市数无关，额度极省）
            out.extend(_national_roundup_queries())
        if "roundup" in use:
            # 5) **盘点词**：撞多地点合集文（一篇含多个大地点，单位查询产出最高）
            for c in SEED_CITIES:
                for w in (roundups or _ROUNDUP_WORDS):
                    out.append((f"{c} 拍车 {w}", c, "city", [c]))
    else:
        # 1) 线路 × 地标（最强：两个具体铁路实体）
        for ln in SEED_LINES:
            for lm in SEED_LANDMARKS:
                out.append((f"{ln} {lm} 拍车机位", lm, "landmark", [lm, ln]))
        # 2) 地标单独（地标自带唯一性，如"松花江大桥"）
        for lm in SEED_LANDMARKS:
            out.append((f"{lm} 拍车机位 攻略", lm, "landmark", [lm]))
        # 3) 线路 + 线路上的大站
        for ln, st in SEED_LINE_STATIONS:
            out.append((f"{ln} {st} 拍车机位", st, "station", [st, ln]))

    seen = set()
    uniq = []
    for q in out:
        if q[0] in seen:
            continue
        seen.add(q[0])
        uniq.append(q)
    return uniq


def _norm_title(t: str) -> str:
    """标题归一化（去重键）。

    为什么还按标题去重：同一篇攻略常被多个站点转载/多入口发布，**各站抽取出的正文不同**
    （实测《震撼上演！官方教你拍火车》有 5 份，正文长度 940/933/1079/920/3236），
    正文指纹因此拦不住。标题比正文稳定得多。
    归一化：去空白 + 砍掉 `_站点名` 后缀 —— 实测 `…拍火车` 与 `…拍火车_新闻频道_中华网`
    是同一篇。
    """
    s = re.sub(r"[\s\u3000]+", "", t or "")
    if "_" in s:
        head = s.split("_")[0]
        if len(head) >= 8:          # 防止把短标题砍光
            s = head
    return s


def _dedup_by_content(rows: list[dict], seen: set[str],
                      seen_titles: set[str] | None = None) -> list[dict]:
    """按**正文指纹 + 归一化标题**去重（同时更新 `seen`，跨查询也不会重复入库）。"""
    out: list[dict] = []
    titles = seen_titles if seen_titles is not None else set()
    for r in rows:
        h = _content_hash(r.get("page_text") or "")
        nt = _norm_title(r.get("title") or "")
        if h in seen or (nt and nt in titles):
            continue
        seen.add(h)
        if nt:
            titles.add(nt)
        out.append(r)
    return out


# --------------------------------------------------------------------------- 入库
def ensure_schema(db: Path) -> None:
    """建表 + **幂等补列**（老库由 mirror_dict.py 建，可能没有后加的列）。"""
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as c:
        c.executescript(SCHEMA)
        migrate_photo_schema(c)
        c.commit()


def upsert_docs(db: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with sqlite3.connect(db) as c:
        for r in rows:
            c.execute(
                "INSERT INTO photo_spot_doc(url,domain,title,source,scope,scope_kind,entities,"
                "reasons,page_text,snippet,fetch_ok,char_count,published,fetched_at,content_hash)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(url) DO UPDATE SET"
                "  title=excluded.title, scope=excluded.scope, scope_kind=excluded.scope_kind,"
                "  entities=excluded.entities, reasons=excluded.reasons,"
                "  page_text=CASE WHEN length(excluded.page_text)>length(COALESCE(page_text,''))"
                "                 THEN excluded.page_text ELSE page_text END,"
                "  snippet=excluded.snippet, fetch_ok=excluded.fetch_ok,"
                "  char_count=excluded.char_count, fetched_at=excluded.fetched_at",
                (r["url"], r["domain"], r["title"], r["source"], r["scope"], r["scope_kind"],
                 json.dumps(r["entities"], ensure_ascii=False),
                 json.dumps(r["reasons"], ensure_ascii=False),
                 r.get("page_text", ""), r.get("snippet", ""),
                 1 if r.get("fetch_ok") else 0, len(r.get("page_text") or ""),
                 r.get("date", ""), now, _content_hash(r.get("page_text") or "")))
        stamp_photo_revision(c)
        c.commit()
    return len(rows)


def record_seed(db: Path, query: str, scope: str, kind: str,
                ok: bool, found: int, raw: int, note: str = "") -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO photo_spot_seed(query,scope,scope_kind,ok,found,raw,note,ran_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(query) DO UPDATE SET ok=excluded.ok, found=excluded.found,"
            "  raw=excluded.raw, note=excluded.note, ran_at=excluded.ran_at",
            (query, scope, kind, 1 if ok else 0, found, raw, note[:200], now))
        stamp_photo_revision(c)
        c.commit()


# --------------------------------------------------------------------------- 主流程
def _content_hash(text: str) -> str:
    """正文指纹（去重键）。

    为什么需要：同一篇攻略在 B站常有 `read/cvXXX`、`opus/XXX`、`read/mobile?id=XXX`
    多条 URL —— 实测《上海市南翔编组站及其周边摄影机位小结》被存了两遍。
    用 URL 去重挡不住，必须按正文内容去重。
    只取前 4000 字并压掉空白：同一篇文章不同入口的正文开头一致，尾部可能有推荐位差异。
    """
    import hashlib

    norm = re.sub(r"\s+", "", (text or "")[:4000])
    return hashlib.sha1(norm.encode("utf-8", "ignore")).hexdigest()


def _domain_of(url: str) -> str:
    return re.sub(r"^https?://([^/]+).*$", r"\1", url or "").lower()


def _title_from_text(text: str) -> str:
    """从抓取到的正文里取标题。

    ⚠️ `html_to_text` 会把换行压成空格，整页可能只有一个"首行"，直接取首行会把
    标题与正文连成一大段（实测）。这里按句读切一刀并限长，只保留像标题的那一截。
    宁可短也不要长：title 只用于展示与检索匹配，正文另有 `page_text` 保存。
    """
    s = (text or "").strip()
    if not s:
        return ""
    # 按常见句读/分隔符切出第一小段
    m = re.split(r"[。！？\n|｜]| - |_哔哩哔哩|_百度", s, maxsplit=1)
    head = (m[0] if m else s).strip()
    return head[:60]


_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


# 标题里出现的省级/城市级词（标题点名的地点最可信）
_SCOPE_TITLE_PREF = (
    "编组站", "站", "铁路桥", "大桥", "道口", "线路所", "机位", "枢纽",
)


def _prefer_scope_from_title(title: str, ent: "LocalEntities", fallback: str) -> str:
    """用**标题里出现的本地实体**作为文档 scope，取不到才退回查询地点。

    为什么不能直接用查询用的城市：实测查"上海"时，一篇《追车记--新成都西环线(上)》
    （正文顺带提了一句上海）被标成 scope=上海，于是用户查上海时先看到一篇讲成都的文章。
    标题是作者自己写的主题，比"这次碰巧用哪个城市查到的"可信得多。
    """
    cands = ent.station_in_text(title or "") + ent.line_in_text(title or "")
    if cands:
        return max(cands, key=len)
    # 标题里没有本地库实体时，退回查询地点（仍比乱标好）
    return fallback


def _infer_scope(text: str, ent: "LocalEntities") -> str:
    """从正文推断地点：本地站点库/线路表**最长匹配**优先。

    只认本地库里真实存在的名字（避免切出"石门子站"这类假实体）。
    """
    head = (text or "")[:4000]
    cands = ent.station_in_text(head) + ent.line_in_text(head)
    if not cands:
        return ""
    return max(cands, key=len)


async def run_urls(args: argparse.Namespace) -> int:
    """`--urls` 模式：直接入库人工提供的链接（**推荐路径**，零反爬风险）。

    为什么需要：搜索引擎是限流资源，把它当建库批量取数通道必然触发反爬（本次实测约 70 次
    检索即被封）。人能低成本地给出攻略链接，脚本负责解析/过滤/入库/去重/标来源 ——
    这样既不碰反爬，也能立刻得到"实际入库篇数"的真实统计。

    每行格式（`#` 开头为注释）：
        <url> [| 地点]
    地点省略时从标题/正文里推断（用本地站点库/线路表最长匹配）。
    """
    db = Path(args.db).expanduser() if args.db else DEFAULT_DB
    ent = LocalEntities()
    await ent.load()
    print(f"[info] 本地实体：站点 {len(ent.stations)} · 线路 {len(ent.lines)}")
    print(f"[info] 目标库：{db}")

    src = Path(args.urls).expanduser()
    if not src.exists():
        print(f"[error] 链接文件不存在：{src}")
        return 2
    items: list[tuple[str, str]] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            url, place = (x.strip() for x in line.split("|", 1))
        else:
            url, place = line, ""
        if url.startswith("http"):
            items.append((url, place))
    print(f"[info] 待入库链接 {len(items)} 条")
    if not items:
        return 2
    if not args.dry_run:
        ensure_schema(db)

    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    kept: list[dict] = []
    skipped: list[str] = []

    async def one(url: str, place: str) -> dict | None:
        async with sem:
            ok, text, why = await fetch_body(url)
        if not ok:
            skipped.append(f"{url}（正文未取到：{why}）")
            return None
        title = _title_from_text(text) or url
        scope = place or _infer_scope(text, ent)
        if not scope:
            skipped.append(f"{url}（无法确定地点，请在文件里用 `url | 地点` 指定）")
            return None
        good, entities, reasons = _judge(title, url, "", text, scope, ent, [scope])
        if not good:
            skipped.append(f"{url}（未过闸门：{' '.join(reasons)}）")
            return None
        domain = _domain_from_content(text, _domain_of(url))
        return {"url": url, "domain": domain, "source": source_of(domain), "title": title,
                "scope": scope, "scope_kind": "given" if place else "inferred",
                "entities": entities, "reasons": reasons, "page_text": text,
                "snippet": "", "fetch_ok": True, "date": ""}

    rows = [r for r in await asyncio.gather(*(one(u, p) for u, p in items)) if r]
    if rows and not args.dry_run:
        upsert_docs(db, rows)
    for r in rows:
        print(f"  ✓ {r['title'][:56]}")
        print(f"      {r['url'][:84]}")
        print(f"      地点={r['scope']} | 来源={r['source']} | 正文 {len(r['page_text'])} 字"
              f" | {' '.join(r['reasons'])[:70]}")
    for s in skipped:
        print(f"  ✗ {s}")

    print(f"\n[done] 提交 {len(items)} 条 → 入库 {len(rows)} 条，跳过 {len(skipped)} 条")
    if not args.dry_run:
        stats(db)
    return 0


def run_rescan(args: argparse.Namespace) -> int:
    """`--rescan`：用**当前闸门规则**重扫存量，删除不再通过的。

    为什么必须要有这一步：闸门是在真实数据上迭代出来的（同形词、域名黑名单、标题规则…），
    每次改进都意味着**之前入库的条目可能已不合格** —— 若只对新数据生效，
    库里会长期残留旧规则放行的噪声（实测：修掉"法拍车/天天拍车"后，
    存量里那几篇汽车拍卖内容还在）。
    重扫只读正文+标题重跑 `_judge`，不联网、不花额度。
    """
    db = Path(args.db).expanduser() if args.db else DEFAULT_DB
    if not db.exists():
        print("[error] 库不存在"); return 2
    ent = LocalEntities()
    asyncio.run(ent.load())
    ensure_schema(db)
    removed = 0
    with sqlite3.connect(db) as c:
        rows = list(c.execute("SELECT url,title,scope,page_text,snippet,entities FROM photo_spot_doc"))
        for url, title, scope, body, snippet, _ents in rows:
            good, _e, reasons = _judge(title or "", url or "", snippet or "",
                                       body or "", scope or "", ent, [scope or ""])
            if not good:
                c.execute("DELETE FROM photo_spot_doc WHERE url = ?", (url,))
                removed += 1
                print(f"  ✗ [{scope}] {(title or '')[:46]}（{' '.join(reasons)[:52]}）")
        if removed:
            stamp_photo_revision(c)
        c.commit()
    print(f"\n[done] 重扫删除 {removed} 篇；剩余 "
          f"{sqlite3.connect(db).execute('SELECT COUNT(*) FROM photo_spot_doc').fetchone()[0]} 篇")
    return 0


def run_dedup(args: argparse.Namespace) -> int:
    """`--dedup`：清理存量重复（同归一化标题保留**正文最长**的那条）。

    为什么需要单独一步：标题去重是后加规则，之前入库的重复需要一次清理；
    保留正文最长的，因为点位描述更可能完整（实测同一篇不同抽取 940 vs 3236 字）。
    """
    db = Path(args.db).expanduser() if args.db else DEFAULT_DB
    if not db.exists():
        print("[error] 库不存在"); return 2
    ensure_schema(db)
    deleted = 0
    with sqlite3.connect(db) as c:
        rows = list(c.execute("SELECT url,title,char_count FROM photo_spot_doc"))
        groups: dict[str, list[tuple[int, str]]] = {}
        for url, title, cc in rows:
            groups.setdefault(_norm_title(title or ""), []).append((int(cc or 0), url))
        for nt, items in groups.items():
            if len(items) < 2:
                continue
            items.sort(reverse=True)              # 正文最长的排前
            keep = items[0][1]
            for _cc, url in items[1:]:
                c.execute("DELETE FROM photo_spot_doc WHERE url = ?", (url,))
                deleted += 1
            print(f"  · {nt[:46]}：{len(items)} → 1（保留 {items[0][0]} 字）")
        if deleted:
            stamp_photo_revision(c)
        c.commit()
    print(f"\n[done] 清理重复 {deleted} 篇")
    stats(db)
    return 0


async def run_ingest_json(args: argparse.Namespace) -> int:
    """`--ingest-json`：直接吃**检索结果 JSON**（含正文），不再抓网页。

    为什么需要这个入口（2026-10-06 实测踩到）：
    1. **JS 渲染页抓不到正文**：`mbd.baidu.com` / `m.toutiao.com` 用 `fetch_body` 只能拿到
       "正文过短（可能为脚本渲染页）"，但它们**在搜索响应里是带 mainText 的**；
    2. **检索结果有波动**：同一查询两次召回不完全相同，某次没召回到的好页面，
       事后用 URL 补录时又抓不到正文，就永远进不来。
    把"取数"与"入库"解耦后：先用任意通道把结果落成 JSON（含正文），
    再离线、可重复地过闸门入库 —— 复核与重跑都不必再花一次额度。

    JSON 格式：`[{"title":..., "url":..., "content":..., "scope": "可选"}]`
    """
    db = Path(args.db).expanduser() if args.db else DEFAULT_DB
    ent = LocalEntities()
    await ent.load()
    print(f"[info] 本地实体：站点 {len(ent.stations)} · 线路 {len(ent.lines)}")

    src = Path(args.ingest_json).expanduser()
    if not src.exists():
        print(f"[error] 文件不存在：{src}")
        return 2
    try:
        items = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[error] JSON 解析失败：{e}")
        return 2
    if not isinstance(items, list):
        print("[error] 顶层应为数组")
        return 2
    print(f"[info] 待入库 {len(items)} 条（正文随结果带入，不再抓网页）")

    if not args.dry_run:
        ensure_schema(db)

    rows, skipped = [], []
    seen_hash: set[str] = set()
    seen_title: set[str] = set()
    # ⚠️ 必须**先加载库内已有**的指纹与标题：`upsert` 只按 URL 去重，
    # 而同一篇常以不同 URL 存在（B站 read/opus/mobile、各站转载）。
    # 不预加载的话，--ingest-json 会把库里已有的文章**再插一遍**（实测多插 115 篇重复）。
    if not args.dry_run and db.exists():
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as _c:
                if _c.execute("SELECT name FROM sqlite_master WHERE type='table'"
                              " AND name='photo_spot_doc'").fetchone():
                    seen_hash = {str(r[0]) for r in
                                 _c.execute("SELECT content_hash FROM photo_spot_doc"
                                            " WHERE content_hash IS NOT NULL")}
                    seen_title = {_norm_title(str(r[0])) for r in
                                  _c.execute("SELECT title FROM photo_spot_doc")}
        except sqlite3.Error:
            pass
    for it in items:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "").strip()
        title = str(it.get("title") or "").strip()
        # 兼容 --dump-json 落盘格式（正文在 prefetched_text）与手写格式（content/page_text）
        text = str(it.get("content") or it.get("prefetched_text")
                   or it.get("page_text") or "").strip()
        if not url or not text:
            skipped.append(f"{(title or url)[:50]}（缺 url 或正文）")
            continue
        # scope：显式给出 > 标题里的本地实体 > 跳过（无法定位）
        scope = str(it.get("scope") or "").strip() or _prefer_scope_from_title(title, ent, "")
        if not scope:
            scope = _infer_scope(text, ent)
        if not scope:
            skipped.append(f"{title[:50]}（无法确定地点）")
            continue
        good, entities, reasons = _judge(title, url, "", text, scope, ent, [scope])
        if not good:
            skipped.append(f"{title[:50]}（{' '.join(reasons)[:60]}）")
            continue
        h = _content_hash(text)
        nt = _norm_title(title)
        if h in seen_hash or (nt and nt in seen_title):
            skipped.append(f"{title[:50]}（重复：同正文或同标题）")
            continue
        seen_hash.add(h)
        if nt:
            seen_title.add(nt)
        domain = _domain_from_content(text, _domain_of(url))
        rows.append({"url": url, "domain": domain, "source": source_of(domain),
                     "title": title, "scope": scope, "scope_kind": "json",
                     "entities": entities, "reasons": reasons, "page_text": text,
                     "snippet": "", "fetch_ok": True, "date": str(it.get("date") or "")})

    if rows and not args.dry_run:
        upsert_docs(db, rows)
    for r in rows:
        print(f"  ✓ [{r['scope']}] {r['title'][:52]} ({len(r['page_text'])}字)")
    for s_ in skipped:
        print(f"  ✗ {s_}")
    print(f"\n[done] 提交 {len(items)} 条 → 入库 {len(rows)} 条，跳过 {len(skipped)} 条")
    if not args.dry_run:
        stats(db)
    return 0


async def run(args: argparse.Namespace) -> int:
    db = Path(args.db).expanduser() if args.db else DEFAULT_DB
    ent = LocalEntities()
    await ent.load()
    print(f"[info] 本地实体：站点 {len(ent.stations)} · 线路 {len(ent.lines)}")
    print(f"[info] 目标库：{db}")

    _shapes = {x.strip() for x in (args.shapes or "").split(",") if x.strip()}
    _types_raw = [x.strip() for x in (args.types or "").split(",") if x.strip()]
    if _types_raw == ["facility"]:
        _types = _RAIL_FACILITY_TYPES
    elif _types_raw == ["all"]:
        _types = tuple(_LANDMARK_TYPES) + tuple(_RAIL_FACILITY_TYPES)
    else:
        _types = tuple(_types_raw) or None
    _roundups = tuple(x.strip() for x in (args.roundups or "").split(",") if x.strip()) or None
    queries = build_queries("qianfan" if args.engine == "aliyun" else args.engine,
                            shapes=_shapes or None, types=_types, roundups=_roundups)
    if args.cities:
        want = [c.strip() for c in args.cities.split(",") if c.strip()]
        queries = [q for q in queries
                   if any(w in q[0] or w == q[1] for w in want)]
        # --cities 也允许直接填地标（如 黄渡），便于单点试跑
    if args.limit:
        queries = queries[: args.limit]
    print(f"[info] 发现查询 {len(queries)} 条（entity-pair 组合）")

    if not args.dry_run:
        ensure_schema(db)

    sites = [x.strip() for x in (args.sites or "").split(",") if x.strip()] if args.sites else None
    finder = Finder(ent, verbose=args.verbose, engine=args.engine,
                    min_interval=args.min_interval, sites=sites,
                    aliyun_engine=getattr(args, "aliyun_engine", "lite"),
                    num_results=getattr(args, "num_results", 50),
                    dump=bool(getattr(args, "dump_json", None)))
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    total_kept = total_raw = total_fetched = 0
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    seen_titles: set[str] = set()
    if not args.dry_run and db.exists():
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as _c:
                if _c.execute("SELECT name FROM sqlite_master WHERE type='table'"
                              " AND name='photo_spot_doc'").fetchone():
                    seen_hashes = {str(r[0]) for r in
                                   _c.execute("SELECT content_hash FROM photo_spot_doc"
                                              " WHERE content_hash IS NOT NULL")}
                    seen_titles = {_norm_title(str(r[0])) for r in
                                   _c.execute("SELECT title FROM photo_spot_doc")}
        except sqlite3.Error:
            pass
    t0 = time.time()

    for i, (query, scope, kind, scope_ents) in enumerate(queries, 1):
        ok, kept, raw, note, docs = await finder.run(query, scope, scope_ents, args.per_query)
        total_raw += raw
        if ok and docs:
            # 抓正文（并发受限）
            async def one(d: dict) -> dict:
                url = d["url"]
                domain = re.sub(r"^https?://([^/]+).*$", r"\1", url).lower()
                d["domain"] = domain
                d["source"] = source_of(domain)
                d["scope"] = scope
                d["scope_kind"] = kind
                if url in seen_urls:
                    return {}
                seen_urls.add(url)
                # 正文来源分两级：
                #  ① **搜索接口自带正文**（阿里云 IQS 的 `contents.mainText`，实测 300–3200 字）
                #     → 直接用，**不再抓网页**。这一步替掉了原先最脆的环节：
                #     自建抓取知乎/百度百科 403 是常态，且要过 SSRF 校验。
                #  ② 没有自带正文时才回退自建抓取（千帆/网页版引擎那条路）。
                prefetched = str(d.get("prefetched_text") or "").strip()
                if len(prefetched) >= _PREFETCH_MIN_CHARS:
                    good, text, why = True, prefetched[:_PAGE_TEXT_HARD], "搜索接口自带正文"
                else:
                    # ⚠️ 引擎现状（2026-10-06 实测）：cn.bing.com 返回**验证页**，web.search 因此
                    # 恒定降级到百度；而百度的结果 URL 全是 `baidu.com/link?url=...` 跳转链，
                    # 既拿不到真实域名、摘要也常为空。因此**不能**按域名决定抓不抓：
                    # 一律尝试抓正文，改用**正文内容**做判定与分类（这才是可信的证据）。
                    async with sem:
                        good, text, why = await fetch_body(url)
                if good:
                    # 地理匹配用**查询地点 ∪ 标题自带实体**：
                    # 只用查询城市会把"跨城标题"误杀 —— 实测查"重庆 拍火车机位"时
                    # 《【龙潭寺】龙潭寺拍车集及周边机位探索》（成都）被拒；
                    # 但它的标题本身已明确指出地点，属于有效线索，应留下。
                    title_scope = _prefer_scope_from_title(d["title"], ent, scope)
                    geo_ents = list(dict.fromkeys(scope_ents + [title_scope]))
                    good2, entities2, reasons2 = _judge(
                        d["title"], url, d["snippet"], text, title_scope, ent, geo_ents)
                    if not good2:
                        return {}
                    d["fetch_ok"] = True
                    d["page_text"] = text
                    d["entities"] = entities2
                    d["reasons"] = reasons2
                    d["scope"] = title_scope
                    d["scope_kind"] = "title" if title_scope != scope else kind
                    d["domain"] = _domain_from_content(text, domain)
                    d["source"] = source_of(d["domain"])
                else:
                    # 正文没抓到 → **不入库**。宁可少不可编：只有摘要在手时无法复核，
                    # 而摘要恰恰是最容易把"旅游/车次视频"误判成机位的地方。
                    return {}
                return d

            fetched = [r for r in await asyncio.gather(*(one(d) for d in docs)) if r]
            # 正文去重：同一篇攻略常有多条 URL（B站 read/opus/mobile），只在 URL 层面挡不住
            fetched = _dedup_by_content(fetched, seen_hashes, seen_titles)
            if not args.dry_run and fetched:
                upsert_docs(db, fetched)
            total_kept += len(fetched)
            total_fetched += sum(1 for d in fetched if d.get("fetch_ok"))
            if args.verbose:
                print(f"[{i}/{len(queries)}] {query}")
                for d in fetched:
                    tag = "正文" if d.get("fetch_ok") else "摘要"
                    print(f"    ✓ {tag} {d['title'][:52]}")
                    print(f"        {d['url'][:88]}")
                    print(f"        {' | '.join(d['reasons'])[:150]}")
            else:
                print(f"[{i}/{len(queries)}] {query} → 入库 {len(fetched)}（原始 {raw}）")
        else:
            if args.verbose:
                print(f"[{i}/{len(queries)}] {query} → 0（{note or '无相关结果'}）")
            else:
                print(f"[{i}/{len(queries)}] {query} → 0")
        if not args.dry_run:
            record_seed(db, query, scope, kind, ok, len(docs or []), raw, note)
        if getattr(finder, "quota_exhausted", False):
            print(f"\n[中止] 阿里云额度已用尽（本次已发 {getattr(finder, 'calls', 0)} 次请求）——"
                  f"停止在 {i}/{len(queries)} 条，避免剩余查询全部 403 空跑。")
            print("        已入库内容不受影响；换引擎或等额度恢复后续跑即可（seed 表支持续跑）。")
            break

    if finder.raw_dump is not None:
        out = Path(args.dump_json).expanduser()
        flat = []
        for _q, rs in finder.raw_dump.items():
            for r in rs:
                r = dict(r); r["_query"] = _q
                flat.append(r)
        out.write_text(json.dumps(flat, ensure_ascii=False), encoding="utf-8")
        print(f"[dump] 原始结果 {len(flat)} 条已落盘 → {out}"
              f"（可用 --ingest-json 离线复判，不必再花额度）")

    dt = time.time() - t0
    print(f"\n[done] {len(queries)} 条查询 / {dt:.0f}s：原始结果 {total_raw} → 通过闸门 {total_kept}"
          f"（其中正文抓取成功 {total_fetched}）")
    if not args.dry_run:
        stats(db)
    return 0


def stats(db: Path) -> None:
    if not db.exists():
        print("[stats] 库不存在")
        return
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        n = lambda q, *p: c.execute(q, p).fetchone()[0]  # noqa: E731
        docs = n("SELECT COUNT(*) FROM photo_spot_doc")
        fetched = n("SELECT COUNT(*) FROM photo_spot_doc WHERE fetch_ok=1")
        seeds = n("SELECT COUNT(*) FROM photo_spot_seed")
        ok_seeds = n("SELECT COUNT(*) FROM photo_spot_seed WHERE found>0")
        print(f"[stats] 文档 {docs}（正文到手 {fetched}）· 查询 {seeds}（有命中的 {ok_seeds}）")
        print("[stats] 按来源：")
        for d, k, f in c.execute(
                "SELECT domain, COUNT(*), SUM(fetch_ok) FROM photo_spot_doc"
                " GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 12"):
            print(f"         {d:<28} {k:>4} 篇（正文 {f or 0}）")
        print("[stats] 覆盖的地点（top）：")
        for s, k in c.execute(
                "SELECT scope, COUNT(*) FROM photo_spot_doc GROUP BY scope"
                " ORDER BY COUNT(*) DESC LIMIT 15"):
            print(f"         {s:<12} {k} 篇")


def main() -> int:
    ap = argparse.ArgumentParser(description="构建机位线索本地库")
    ap.add_argument("--all", action="store_true", help="跑全部种子查询")
    ap.add_argument("--cities", help="只跑的种子（逗号分隔，按 scope 过滤）")
    ap.add_argument("--limit", type=int, help="最多跑多少条查询（试跑用）")
    ap.add_argument("--per-query", type=int, default=6, help="每条查询取多少结果")
    ap.add_argument("--engine", choices=["auto", "aliyun", "qianfan", "baidu", "bing"],
                    default="auto",
                    help="取数通道：qianfan=官方百度AI搜索API（推荐，需 QIANFAN_API_KEY，不反爬）；"
                         "auto=web.search；baidu/bing=直连网页版（实测会反爬/退化）")
    ap.add_argument("--shapes", default="base,type,roundup,national",
                    help="生成哪些查询形态（按额度分配）：base,type,roundup 逗号分隔")
    ap.add_argument("--types", help="覆盖大地点类型词（逗号分隔）；"
                                    "快捷值：facility=铁路设施名词组，all=全部")
    ap.add_argument("--roundups", help="覆盖盘点词（逗号分隔），默认全部")
    ap.add_argument("--aliyun-engine", default="lite",
                    choices=["lite", "auto", "generic", "global_basic", "global_advanced"],
                    help="阿里云具体引擎（额度各自独立）：lite=国内版LiteBasic(50条,QPS10) / "
                         "auto=国内版Auto / generic=通用版 / global_basic / global_advanced")
    ap.add_argument("--num-results", type=int, default=50,
                    help="阿里云单次返回条数（LiteBasic 上限 50）")
    ap.add_argument("--sites", help="站点定向（逗号分隔，仅 qianfan 引擎有效）："
                                    "官方 search_filter.match.site，如 www.bilibili.com,tieba.baidu.com")
    ap.add_argument("--min-interval", type=float, default=SEARCH_MIN_INTERVAL_S,
                    help="两次引擎请求的最小间隔秒数（建库须低频；建议 ≥20）")
    ap.add_argument("--dump-json", help="把原始搜索结果（含正文）落盘到该文件；"
                                        "规则迭代时可离线复判，不必重跑查询")
    ap.add_argument("--rescan", action="store_true",
                    help="用当前闸门规则重扫存量并删除不合格条目（规则改进后必跑）")
    ap.add_argument("--dedup", action="store_true",
                    help="清理存量重复（同归一化标题保留正文最长的一条）")
    ap.add_argument("--ingest-json", help="直接吃**检索结果 JSON**（含正文，不再抓网页）；"
                                          "格式 [{\"title\",\"url\",\"content\",\"scope?\"}]")
    ap.add_argument("--urls", help="从文件读人工提供的链接建库（每行 `<url> [| 地点]`）；"
                                   "**不碰搜索引擎，推荐路径**")
    ap.add_argument("--db", help="目标库路径（默认 DICT_DB_PATH/backend/data/dict.db）")
    ap.add_argument("--dry-run", action="store_true", help="只判定不入库")
    ap.add_argument("--stats", action="store_true", help="只打印现状")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.stats:
        stats(Path(args.db).expanduser() if args.db else DEFAULT_DB)
        return 0
    if args.rescan:
        return run_rescan(args)
    if args.dedup:
        return run_dedup(args)
    if args.ingest_json:
        return asyncio.run(run_ingest_json(args))
    if args.urls:
        return asyncio.run(run_urls(args))
    if not (args.all or args.cities or args.limit):
        ap.print_help()
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
