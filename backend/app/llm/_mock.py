"""确定性规则回复（mock 演示 / 模型不可用兜底）。

同一套解析与渲染服务两个场景：

1. **`LLM_MOCK=true`（演示 / 无 Key CI）**：替掉生成层，让三层整链仍能跑通；
2. **模型不可用时的降级**（`render_fallback`）：检索已经完成、事实就在 prompt 里，
   与其只丢一句「LLM 不可用」，不如用固定模板把事实组织成可读回复。
   快路径命中时（实测占单轮问法的 74%）**一次 LLM 调用都不需要**就能拿到全部数据，
   模型挂掉时这部分数据本该照常交付。

# 设计约束（每一条都是踩过的坑）

**① 判定输入必须是「用户输入」段落，不是整段 prompt。**
   prompt 模板自带 few-shot 示例（"G1今天由哪组动车组担当？"、"CR400AF用的哪个品牌的
   动力系统？"）。早期实现对整段 prompt 做关键词匹配 → 任何输入（包括"你好"）都会被
   示例里的"担当"命中 → 恒判 emu_routing + knowledge，mock/CI 下 13 个工具的路由逻辑
   从未被执行。现在统一先切出 `[本次用户输入]` 段落。

**② 槽位抽取复用 `fastpath.rule_slots`，不另写一套。**
   原实现自带弱正则：`[A-Z]{1,2}\\d{2,6}` 要求**至少两位数字**，"G1" 抽不到；
   区间（北京南→上海虹桥）与站名完全不认。而 mock 模式下合并调用正是走的这条抽取路径，
   后果是 **LLM_MOCK=true 时凡交给 LLM 的问题都带着空槽位去检索**（实测
   "G1今天几点到上海虹桥？" → slots={}），测试对检索参数零鉴别力。

**③ 来源只从 `[数据来源]` 段落取。**
   原实现把"以 - 开头"的行全当来源，而检索事实块每一条的首行也是 `- [tool] …`，
   于是输出里的"数据来源"塞满了 prompt 通用要求的原文（实测输出里混进了
   "每条检索事实都带有自己的来源与时效说明…" 这类模板文字）。

**④ 不编造，也不假装是模型。**
   本模块只做搬运与排版，绝不生成事实。知识型问题没有事实可搬时如实说"无法作答"，
   而不是用规则拼一段看起来像答案的话——那比"查不到"更糟。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 回复风格：mock 自报家门；fallback 是线上降级，措辞面向用户
STYLE_MOCK = "mock"
STYLE_FALLBACK = "fallback"

# intent.py / extract.py 的稳定标记：用户输入段落
_USER_SECTION_RE = re.compile(r"\[本次用户输入\]\s*\n(?P<user>.*?)(?:\n\s*\n|\Z)", re.S)

# ---- 生成层 prompt 的稳定锚点（见 pipeline/generate.build_prompt）----
_RE_QUESTION = re.compile(r"^用户原话：(?P<v>.*)$", re.M)
_RE_SLOTS = re.compile(r"^关键槽位 => (?P<v>.*)$", re.M)
_RE_TOOLS = re.compile(r"^工具调用：(?P<v>.*)$", re.M)
_RE_QTYPE = re.compile(r"^问题性质 => (?P<v>.*)$", re.M)
_SRC_MARK = "\n[数据来源]\n"
_FACT_MARK = "\n[检索事实]\n"
_HIST_MARK = "[对话历史]\n"

# 事实块里的元信息行（由 generate.build_prompt 渲染）
_P_INTEGRITY = "  数据完整性："
_P_SOURCES = "  来源："
_P_NOTE = "  时效/说明："

# 工具名 → 中文小标题。工具是权威信号（它真的被调用了），比从问句猜意图可靠。
_TOOL_ZH: dict[str, str] = {
    "ticket.query": "余票查询",
    "train.schedule": "列车时刻 / 经停",
    "station.screen": "车站大屏",
    "station.lookup": "车站信息",
    "emu.routing": "车组担当 / 交路",
    "railre": "rail.re 数据",
    "rail.line": "径路",
    "rail.line_stations": "沿线车站",
    "rail.mileage": "里程",
    "cnrail.map": "线路图",
    "web.search": "网络检索",
    "web.fetch": "网页正文",
    "jprailfan": "日文铁路资料",
    "freight.95306": "95306 货运",
    "kmrail.freight": "铁路里程资料",
    "sytlj.ticket": "票务资料",
    "t12306.search_tickets": "12306 车票检索",
}
_TOOL_FALLBACK_ZH = "数据源结果"

# 行列分隔符：generate._render_fact_text 用全角"｜"渲染表格类事实
# （刻意避开 ASCII "|"，否则事实块会被前端的 Markdown 表格解析器误吃）。
_CELL_SEP = "｜"


# ============================================================ prompt 解析
@dataclass
class Fact:
    """一条检索事实（对应 build_prompt 里的 `- [tool] …` 块）。"""

    tool: str
    body: str = ""
    integrity: str = ""
    sources: tuple[str, ...] = ()
    note: str = ""


@dataclass
class PromptView:
    """从生成层 prompt 里解析出的结构化视图（纯字符串处理，不联网、不调模型）。"""

    question: str = ""
    slots: dict[str, str] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    question_type: str = "realtime"
    facts: list[Fact] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    history_turns: int = 0


def user_text(prompt: str) -> str:
    """从 prompt 中切出用户输入段落；找不到标记时回退为整段（兼容直接调用）。"""
    m = _USER_SECTION_RE.search(prompt or "")
    return (m.group("user") if m else (prompt or "")).strip()


def _split_facts(raw: str) -> list[Fact]:
    """把 `[检索事实]` 段落切成 Fact 列表。

    块格式（见 generate.build_prompt）：
        - [tool] 首行
          续行（两个空格缩进）
          数据完整性：…
          来源：a, b
          时效/说明：…
    续行与元信息行**都**以两个空格开头，只能靠前缀区分——所以元信息前缀必须是常量。
    """
    facts: list[Fact] = []
    cur: Fact | None = None
    body: list[str] = []
    for line in (raw or "").splitlines():
        if line.startswith("- ["):
            if cur is not None:
                cur.body = "\n".join(body).strip("\n")
                facts.append(cur)
            end = line.find("]")
            tool = line[3:end] if end > 3 else ""
            body = [line[end + 1:].lstrip()] if end > 3 else [line[2:]]
            cur = Fact(tool=tool or "?")
            continue
        if cur is None:
            continue
        if line.startswith(_P_INTEGRITY):
            cur.integrity = line[len(_P_INTEGRITY):].strip()
        elif line.startswith(_P_SOURCES):
            raw_src = line[len(_P_SOURCES):].strip()
            cur.sources = tuple(s.strip() for s in raw_src.split(",") if s.strip())
        elif line.startswith(_P_NOTE):
            cur.note = line[len(_P_NOTE):].strip()
        else:
            body.append(line.rstrip())
    if cur is not None:
        cur.body = "\n".join(body).strip("\n")
        facts.append(cur)
    return facts


def parse_prompt(prompt: str) -> PromptView:
    """把生成层 prompt 解析成 PromptView（找不到的字段留空，不抛异常）。"""
    text = prompt or ""
    view = PromptView()

    m = _RE_QUESTION.search(text)
    if m:
        view.question = m.group("v").strip()
    m = _RE_QTYPE.search(text)
    if m:
        view.question_type = (m.group("v").strip() or "realtime")
    m = _RE_TOOLS.search(text)
    if m:
        # trace 是 Python list 的 repr（`['ticket.query(from=北京南,to=上海虹桥)']`），
        # 而参数里本身就含逗号 —— 只能按引号切，不能按逗号 split。
        view.tools = [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group("v"))]
    m = _RE_SLOTS.search(text)
    if m:
        for pair in m.group("v").split(","):
            k, sep, v = pair.partition("=")
            if sep and k.strip() and v.strip():
                view.slots[k.strip()] = v.strip()

    # 用 rfind/find 定位而不是正则 `.*?`：事实正文里可能出现空行（抓来的网页正文），
    # 正则的非贪婪匹配会在第一个空行处提前收尾。
    i = text.find(_SRC_MARK)
    head = text if i < 0 else text[:i]
    if i >= 0:
        for line in text[i + len(_SRC_MARK):].splitlines():
            s = line.strip()
            if s.startswith("- ") and "无实时来源" not in s:
                view.sources.append(s[2:].strip())
    j = head.rfind(_FACT_MARK)
    if j >= 0:
        view.facts = _split_facts(head[j + len(_FACT_MARK):])

    k = text.find(_HIST_MARK)
    if k >= 0:
        # 历史区块在「用户原话」之前；只数条数用于如实交代"已参考前文"
        end = text.find("\n\n", k)
        block = text[k:end if end > 0 else None]
        view.history_turns = sum(1 for ln in block.splitlines() if ln.strip().startswith("[用户]"))
    return view


# ============================================================ 表格化
def _table_at(lines: list[str], i: int) -> tuple[list[str], int] | None:
    """识别从第 i 行开始的表格块，返回 (Markdown 行, 下一行下标)；不是表格则 None。

    只认两种**列数唯一确定**的形态，其余一律保持纯文本（误判会把内容拆得乱七八糟）：
      形式 A `……（列1｜列2）：` + 缩进的 `｜` 数据行 —— `generate._render_fact_text` 的余票明细；
      形式 B `列1\\t列2\\t列3` 制表符表头 + 同列数的数据行 —— `train_schedule` 的经停表。

    刻意不认 ASCII `|`：`station.screen` 用它做字段分隔，且标题写的列数与数据行并不对应
    （标题 6 列、数据 4 列），照搬会渲染出错位的表。全角"｜"是专门的表格分隔符，安全。
    """
    line = lines[i]

    # 形式 A
    head = re.match(r"^.*?（(?P<cols>[^（）]*｜[^（）]*)）：\s*$", line)
    if head:
        cols = [c.strip() for c in head.group("cols").split(_CELL_SEP)]
        rows: list[list[str]] = []
        k = i + 1
        while k < len(lines) and lines[k].startswith(" ") and _CELL_SEP in lines[k]:
            rows.append([c.strip() for c in lines[k].strip().split(_CELL_SEP)])
            k += 1
        if rows and all(len(r) == len(cols) for r in rows):
            return _md_table(cols, rows), k
        return None

    # 形式 B
    if "\t" in line:
        cols = [c.strip() for c in line.split("\t")]
        if len(cols) >= 2 and all(cols):
            rows = []
            k = i + 1
            while k < len(lines) and "\t" in lines[k]:
                rows.append([c.strip() for c in lines[k].split("\t")])
                k += 1
            if rows and all(len(r) == len(cols) for r in rows):
                return _md_table(cols, rows), k
    return None


def _md_table(cols: list[str], rows: list[list[str]]) -> list[str]:
    def cell(v: str) -> str:
        # 单元格内不能出现裸 "|"，否则会把表格拆掉
        return v.replace("|", _CELL_SEP)

    out = ["| " + " | ".join(cell(c) for c in cols) + " |",
           "| " + " | ".join("---" for _ in cols) + " |"]
    out.extend("| " + " | ".join(cell(c) for c in r) + " |" for r in rows)
    return out


def _tuplify(text: str) -> str:
    """把事实正文里的表格块转成 Markdown 表格（列数不符时保持原样）。"""
    lines = (text or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        got = _table_at(lines, i)
        if got is not None:
            rendered, nxt = got
            out.extend(rendered)
            i = nxt
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


# ============================================================ 渲染
def _tool_title(tool: str) -> str:
    return _TOOL_ZH.get(tool, f"{_TOOL_FALLBACK_ZH}（{tool}）")


def _render_fact(fact: Fact) -> str:
    parts = [f"### {_tool_title(fact.tool)}", _tuplify(fact.body)]
    if fact.integrity:
        # 完整性契约：命中/展示/是否截断必须原样带出（这条是硬约束，不能省）
        parts.append(f"**数据完整性**：{fact.integrity}")
    if fact.note:
        parts.append(f"**时效/说明**：{fact.note}")
    return "\n".join(p for p in parts if p)


def _header(view: PromptView, style: str) -> str:
    if style == STYLE_FALLBACK:
        head = (
            "⚠️ **语言模型当前不可用**，以下内容由系统按固定规则整理检索结果生成，"
            "**未经模型归纳、组织与校对**。"
        )
    else:
        head = (
            "【确定性规则回复】本次回答**未使用大语言模型**，"
            "内容由系统按固定规则整理检索结果生成。"
        )
    lines = [head, ""]
    if view.question:
        lines.append(f"**问题**：{view.question}")
    if view.slots:
        lines.append("**关键信息**：" + "、".join(f"{k}={v}" for k, v in view.slots.items()))
    if view.tools:
        lines.append("**已调用**：" + "、".join(view.tools))
    if view.history_turns:
        lines.append(f"**上下文**：已参考前文 {view.history_turns} 条历史消息")
    return "\n".join(lines)


def _footer(style: str) -> str:
    if style == STYLE_FALLBACK:
        return (
            "（本条回复由确定性规则生成，非大语言模型输出。"
            "模型恢复后重试，可获得完整的归纳与表述。）"
        )
    return "（本条回复由确定性规则生成，非大语言模型输出。）"


def _no_facts(view: PromptView, style: str) -> str:
    """没有任何事实时的如实交代——不同性质的问题，说法不同。"""
    if view.question_type == "knowledge":
        return (
            "本问题属**知识型**，需要语言模型结合铁路知识作答；"
            "当前为确定性规则模式，**无法给出知识型回答**（规则作答不编造知识，以免误导）。"
        )
    return (
        "本次**没有检索到可引用的数据**。"
        "可能是数据源未启用、查询参数不足，或该条件下确实无结果——"
        "建议补充车次号 / 站名 / 日期等关键信息后重试。"
    )


def render(prompt: str, style: str = STYLE_MOCK) -> str:
    """把生成层 prompt 渲染成规则回复（无事实时也返回说明性文字，不返回空串）。"""
    view = parse_prompt(prompt)
    blocks: list[str] = [_header(view, style)]

    if view.facts:
        blocks.append("")
        blocks.append("\n\n".join(_render_fact(f) for f in view.facts))
    else:
        blocks.append("")
        blocks.append(_no_facts(view, style))

    if view.sources:
        blocks.append("")
        blocks.append("### 数据来源")
        blocks.extend(f"- {s}" for s in view.sources)

    blocks.append("")
    blocks.append(_footer(style))
    return "\n".join(blocks)


def render_fallback(prompt: str) -> str:
    """模型不可用时的降级回复；**没有事实可搬时返回空串**。

    为什么空手就返回空串：规则渲染的价值在于"把已经拿到的数据交付出去"。
    一无所获时，编排层那句可操作的错误提示（怎么配 Key、怎么换供应商）才是用户要的，
    再叠一段"没有检索到数据"只会淹没它。
    """
    view = parse_prompt(prompt)
    if not view.facts:
        return ""
    return render(prompt, STYLE_FALLBACK)


# ============================================================ 意图 / 槽位（结构化调用）
# 注意：顺序即优先级，先匹配到的赢；仅在「用户输入」上匹配
_INTENT_KEYWORDS = (
    ("拍", "photo_spot"), ("机位", "photo_spot"), ("拍摄", "photo_spot"),
    ("担当", "emu_routing"), ("交路", "emu_routing"), ("车组", "emu_routing"), ("车底", "emu_routing"),
    ("径路", "rail_line"), ("线路", "rail_line"), ("里程", "rail_line"), ("走哪条线", "rail_line"),
    ("余票", "ticket"), ("有票", "ticket"), ("车票", "ticket"), ("买票", "ticket"), ("票价", "ticket"),
    ("时刻", "schedule"), ("几点", "schedule"), ("发车", "schedule"), ("开行", "schedule"),
    ("车站", "station"), ("站点", "station"),
    ("新闻", "news"), ("资讯", "news"),
)

SLOT_KEYS = ("location", "target", "time", "direction", "extra")


def _detect_intent(text: str) -> str:
    for kw, intent in _INTENT_KEYWORDS:
        if kw in text:
            return intent
    return "general"


# 知识型问题的关键词（与技术参数/历史/编号相关）
_KNOWLEDGE_KEYWORDS = (
    "动力", "牵引", "功率", "厂商", "品牌", "制造商", "供应商", "技术",
    "参数", "历史", "沿革", "样车", "试验", "编号", "命名", "谱系",
    "用的什么", "哪家", "什么牌子",
)


def _detect_question_type(text: str) -> str:
    """粗判问题性质：knowledge / realtime。"""
    for kw in _KNOWLEDGE_KEYWORDS:
        if kw in text:
            return "knowledge"
    return "realtime"


# 兜底抽取：仅当 fastpath 不可用（站点库加载失败等）时使用。
# 故意写得很保守——只抽绝对确定的形态，抽不到就给 null（错抽比漏抽危险得多）。
_RE_FB_TRAIN = re.compile(r"(?<![0-9A-Za-z])(0?[GDCTZKYSLBN]\d{1,5})(?!\d)")
_RE_FB_TARGET = re.compile(r"(CR[0-9A-Za-z\-]{2,12}|CRH[0-9A-Za-z\-]{1,10})", re.I)
_RE_FB_LOC = re.compile(r"[在到](?P<loc>[\u4e00-\u9fa5]{2,8}?)(?:站|市|区)?[，,。；;\s]")
_RE_FB_TIME = re.compile(
    r"(今天|今日|明天|明日|后天|大后天|昨天|昨日|今晚|今早|明早|明晚)"
    r"(早上|上午|中午|下午|傍晚|晚上|凌晨)?"
)


def _fallback_slots(text: str) -> dict:
    t = text or ""
    m = _RE_FB_TRAIN.search(t) or _RE_FB_TARGET.search(t)
    loc = _RE_FB_LOC.search(t)
    tm = _RE_FB_TIME.search(t)
    return {
        "location": loc.group("loc") if loc else None,
        "target": m.group(0).upper() if m else None,
        "time": ((tm.group(1) or "") + (tm.group(2) or "")) if tm else None,
        "direction": next((d for d in ("上行", "下行", "方向") if d in t), None),
        "extra": None,
    }


async def _extract_slots(text: str) -> dict:
    """槽位抽取：优先复用快路径的判据（见模块 docstring 约束②）。"""
    try:
        from app.pipeline.fastpath import rule_slots

        s = await rule_slots(text)
        out = {k: s.get(k) for k in SLOT_KEYS}
    except Exception:  # noqa: BLE001 —— 站点库不可用等情况，退化为保守正则
        out = _fallback_slots(text)
    return {k: (out.get(k) or None) for k in SLOT_KEYS}


async def mock_structured(prompt: str, schema: dict) -> dict:
    """按 schema 的 properties 决定返回哪些字段（只基于**用户输入**判定）。

    关键：**按 schema 里实际出现的字段来填**，而不是"有 intent 就只返回 intent"。
    planner 的合并调用用的是一份同时含 intent / question_type / 5 个槽位的 schema，
    旧实现一见 `intent` 就 return，槽位被整块丢掉——mock 模式下走 LLM 的那条路
    因此**永远带着空槽位检索**。这里改成按 properties 取交集。
    """
    text = user_text(prompt)
    props = set((schema.get("properties") or {}).keys())
    out: dict = {}
    if "intent" in props:
        out["intent"] = _detect_intent(text)
    if "question_type" in props:
        out["question_type"] = _detect_question_type(text)
    if props & set(SLOT_KEYS):
        out.update(await _extract_slots(text))
    return {k: v for k, v in out.items() if k in props}


# ============================================================ 生成（文本）
async def mock_chat(prompt: str, history: list[dict] | None = None) -> str:
    """生成层的确定性回复（演示 / CI 用）。"""
    return render(prompt, STYLE_MOCK)


async def mock_stream_chunks(prompt: str, history: list[dict] | None = None) -> list[str]:
    """Mock 流式分块（供 stream_completion 使用）。"""
    answer = render(prompt, STYLE_MOCK)
    # 按行切分，模拟流式增量；空行单独成块以保留段落结构
    parts = answer.split("\n")
    chunks: list[str] = []
    for i, p in enumerate(parts):
        chunk = p + ("\n" if i < len(parts) - 1 else "")
        if chunk:
            chunks.append(chunk)
    return chunks or [answer]
