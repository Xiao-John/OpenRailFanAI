"""回答生成层。

把用户原句 + 抽取槽位 + 检索结果（真实工具数据）交给 LLM，生成自然语言回答，
并在结尾附数据来源。

数据注入：将 retrieval["data"] 中每个工具抓到的文本摘要渲染成结构化行，
连同 source 链接一起给模型引用，避免模型编造数据。
检索为空/失败时如实说明，而非伪造实时数据。

多轮上下文（M11.1 成本治理）：历史**只以「[对话历史] 区块」一种形式**进入生成调用。
早期实现同时把 `history` 塞进 messages 数组（`chat_with_reasoning(prompt, history=...)`），
与 prompt 里的压缩区块重复注入（实测单次生成 prompt 中约 1760 字符是重复历史）。
现在统一走区块（区块自带"历史结论不得当作本次检索事实"的约束）；
意图/抽取层同理，只读区块。
"""
from __future__ import annotations

from datetime import date

from app.config import get_settings
from app.context import format_history
from app.llm.client import chat_with_reasoning
from app.pipeline.extract import Slots

# 生成层历史深度：比意图/抽取层（4 条）更深，用于对话连贯
GEN_HISTORY_LIMIT = 6

# ---- 作答策略（按 problem_type 切换是否允许使用模型自身知识）----
_POLICY_REALTIME = (
    "【作答策略：实时数据型】\n"
    "本节问题依赖实时/事实性数据（时刻、余票、担当车组、开行状态、径路里程等）。\n"
    "必须**只依据下方“检索事实”作答**，不得用模型记忆补充或推测这类数据。\n"
    "检索不足时，如实说明缺哪些数据并给出方向性建议，不要编造。\n"
    "**唯一例外（站点纠错）**：若检索明确显示“未找到该站”并给出了候选站名，"
    "你可以基于模型知识指出可能的同音/形近正确站名（例如“太安”→“泰安”），"
    "但必须标注“（据模型知识，未检索确认）”并建议用户确认后再查。\n"
)

_POLICY_KNOWLEDGE = (
    "【作答策略：知识型】\n"
    "本节问题属知识型（车型技术参数、动力系统、厂商品牌、历史沿革、样车编号、命名规则等），"
    "答案不随实时数据变化。**允许**综合以下两类内容作答：\n"
    "  (a) 下方“检索事实”（网络搜索/数据源结果）——优先级最高；\n"
    "  (b) 你自己的中国铁路知识（可作为补充与背景）。\n"
    "约束（务必遵守）：\n"
    "  1. 检索事实与你的记忆冲突时，**以检索事实为准**，并说明存在不同说法；\n"
    "  2. 来自模型知识、且检索事实未覆盖的内容，需标注“（据模型知识，未检索确认）”，"
    "并对不确定处用“据称/可能/大致”等措辞，不要给出生硬的确定结论；\n"
    "  2.5 **精确数值宁少勿错**：编组辆数、投产/提速年份、牵引功率、车组编号这类具体数字，"
    "若记忆不确切就**不要给精确值**（可给区间或直接说“不确定”）；"
    "已知易错项：AF-A/BF-A 长编组节数、铁路大提速年份、厂商全称。\n"
    "  3. **具体编号、精确数值、型号名**（如样车车组号、试验编号、功率数字）"
    "若检索事实中没有，必须明确标注为“未经检索确认”，不得当作权威数据；\n"
    "  4. 检索事实为空时，可以基于模型知识作答，但开头要说明"
    "“本次未检索到相关资料，以下为模型知识，建议核实”。\n"
)

_POLICY_MIXED = (
    "【作答策略：混合型】\n"
    "本问题同时包含实时诉求与知识诉求，请**分开处理**：\n"
    "  - 实时部分（时刻/余票/担当/开行状态）：只依据“检索事实”，不足则如实说明；\n"
    "   - 知识部分（参数/厂商/历史/编号）：可结合模型知识，但须标注"
    "“（据模型知识，未检索确认）”，并说明与检索事实是否一致。\n"
)

_POLICY = {
    "realtime": _POLICY_REALTIME,
    "knowledge": _POLICY_KNOWLEDGE,
    "mixed": _POLICY_MIXED,
}


def answer_policy(question_type: str | None) -> str:
    """按问题性质返回作答策略文本（未知/缺省按 realtime 从严处理）。"""
    return _POLICY.get(str(question_type or "realtime"), _POLICY_REALTIME)


# ---- 事实渲染：结构化投影优先，取消"900 字符硬截"（2026-09-14）----
# 事故背景（R1 报告 C02/C04）：`text[:900]` 会把 53 趟车次的明细从中间切断，
# 模型只看到上午车次 → 如实回答"未包含晚间车次" → 用户的问题实际没答。
# 现在表格类工具走专用渲染：字段投影 + 行数上限 + 显式省略标注 + 分布统计。

def _seat_cell(seats: dict, limit: int = 4) -> str:
    if not seats:
        return "席别信息缺失"
    parts = [f"{k}={v}" for k, v in seats.items() if v and v not in ("无", "--")]
    return " ".join(parts[:limit]) or "无余票"


def _render_fact_text(d: dict, settings) -> str:
    """把一条检索事实渲染成注入 prompt 的文本（表格类工具走结构化投影）。"""
    tool = str(d.get("tool") or "")
    payload = d.get("data") if isinstance(d.get("data"), dict) else None

    if tool == "ticket.query" and payload and payload.get("trains") is not None:
        rows = list(payload.get("trains") or [])
        max_rows = int(getattr(settings, "fact_table_max_rows", 40))
        head = (
            f"{payload.get('from_station')}→{payload.get('to_station')}"
            f"（{payload.get('train_date')}）：区间共 {payload.get('count_all')} 趟"
            f"，符合条件 {payload.get('count')} 趟，本次下发 {len(rows)} 趟"
        )
        if payload.get("applied_filters"):
            head += "；已应用过滤：" + "、".join(
                f"{k}{v}" for k, v in payload["applied_filters"].items()
            )
        lines = [head]
        periods = payload.get("period_counts") or {}
        if periods:
            lines.append("发车时段分布：" + "；".join(f"{k} {v} 趟" for k, v in periods.items() if v))
        seats = payload.get("seat_counts") or {}
        if seats:
            lines.append("有票席别统计：" + "；".join(f"{k}={v} 趟" for k, v in seats.items()))
        lines.append("车次明细（车次｜发-到｜席别）：")
        for t in rows[:max_rows]:
            lines.append(
                f"  {t.get('train_no')}｜{t.get('start_time')}-{t.get('arrive_time')}"
                f"｜{_seat_cell(t.get('seats') or {})}"
            )
        if len(rows) > max_rows:
            lines.append(f"  …（已省略 {len(rows) - max_rows} 行；可按条件过滤后重查）")
        return "\n".join(lines)

    if tool == "rail.line" and payload and payload.get("route"):
        # 径路明细较长：保留工具自渲染文本，但用更高的字符预算
        return str(d.get("text") or "").strip()[: int(getattr(settings, "fact_text_max_chars", 4000))]

    text = str(d.get("text") or "").strip()
    return text[: int(getattr(settings, "fact_text_max_chars", 4000))]


def build_prompt(
    user_message: str,
    slots: Slots,
    retrieval: dict,
    history: list[dict] | None = None,
    question_type: str | None = None,
) -> str:
    """构造生成层 prompt（流式/非流式共用）。

    每条检索事实都带自己的来源与时效说明（note），避免把某个工具的
    陈旧数据警告错误地套用到另一个工具的实时数据上。

    question_type 决定作答策略：
      realtime  → 严格闭卷（只用检索事实）
      knowledge → 允许结合模型知识（需标注来源与不确定度）
      mixed     → 实时/知识分别处理
    """
    slot_lines = ", ".join(f"{k}={v}" for k, v in slots.non_empty().items()) or "(未抽取到关键槽位)"
    sources: list[str] = retrieval.get("sources") or []
    trace: list[str] = retrieval.get("tool_trace") or []
    data_entries: list[dict] = retrieval.get("data") or []

    settings = get_settings()
    if data_entries:
        blocks: list[str] = []
        for d in data_entries:
            text = _render_fact_text(d, settings)
            if not text:
                continue
            entry_sources = d.get("sources") or []
            entry_note = str(d.get("note") or "").strip()
            integrity = str(d.get("integrity") or "").strip()
            line = f"- [{d['tool']}] {text}"
            if integrity:
                line += f"\n  数据完整性：{integrity}"
            if entry_sources:
                line += "\n  来源：" + ", ".join(entry_sources)
            if entry_note:
                line += f"\n  时效/说明：{entry_note}"
            blocks.append(line)
        data_anchor = "\n".join(blocks) or "(数据块为空)"
    else:
        data_anchor = "(无可引用的检索数据)"

    ctx = format_history(history, limit=GEN_HISTORY_LIMIT)
    ctx_block = f"[对话历史]\n{ctx}\n\n" if ctx else ""

    return (
        "以下是一句铁路相关请求，我已解析意图与关键信息，并调用了数据源工具。\n"
        f"{answer_policy(question_type)}\n"
        "通用要求（所有类型都适用）：\n"
        "- 每条检索事实都带有自己的“来源”与“时效/说明”，请**逐条按各自的说明判断时效性**，"
        "不要用某条数据的时效说明去否定另一条数据；\n"
        "- 涉及日期时，**直接采用检索事实中给出的日期（YYYY-MM-DD），不要自行推算**；"
        f"今天是 {date.today().isoformat()}（“今天/明天/后天”已由系统换算）；\n"
        "- 多轮对话时注意承接上文（用户可能在追问“那明天呢”“这车呢”），"
        "但**不要把历史回答中的结论当成本次检索事实**；\n"
        "- 资讯类问题（开通/停运/调图/公告等）**时效优先**：检索结果若自带日期"
        "（形如 [2024-12-26]），一律以**日期最新**的条目为准；不带日期的条目不得作为时效依据；"
        "新旧条目冲突时必须说明存在冲突并倾向最新，不得仅凭个别陈旧条目下“尚未开通/已停运”这类结论；\n"
        "- **禁止由推理反推时间节点**：不得从“开通 N 周年”“预计年底”这类表述倒推出具体年份/日期，"
        "也不得把推理结果当事实陈述（实测 H02 由“开通 15 周年”倒推出 2011 并当结论）；"
        "没有带日期的来源时，只能说明“检索结果未给出明确日期，无法确认”；\n"
        "- **思考要短**（用户看不到思考，只看到回答）：思考只做要点判断与事实核对，"
        "**不要在思考里完整起草最终回答**，也不要复述检索事实原文或罗列所有候选方案；"
        "正式回答只输出一次，不要重复思考中的措辞。\n"
        "- **跨日期使用规则（按问题性质分两档，不要一刀切）**：\n"
        "  ① **当日运行事实**——今天几点发车/几点到/是否晚点/有无余票/由哪组担当/几站台，"
        "只能用**本次询问日期、且标注为实际运行**的事实；拿不到就直说“该日数据不可得”"
        "并说明原因（已发车、12306 不再列出）。这条红线不变。\n"
        "  ② **结构性事实**——经停哪些站、站序、全程历时/里程、席别构成、车型编组，"
        "以及“这个车次号在交路上怎么变”，都属于**图定属性**，调图前长期稳定："
        "**只要检索事实里有，哪怕是【非今日数据】或别的日期的图定时刻表，就必须拿来回答**，"
        "并用一句话交代数据的日期与“图定/计划”性质"
        "（例：“以下为 2026-09-18 的图定时刻，仅供参考；当日实际运行时刻不可得”）。\n"
        "  ⚠️ ②类问题**禁止**因为“不是今天／该日已发车”就回答“查不到／无法回答／无数据”——"
        "这是最常犯的错误：用户问的就是这趟车的经停与用时，图定表完全答得上。\n"
        "  ⚠️ 引用②类数据时**禁止**把它说成当日实际发车/到达时间，也禁止据此推断晚点。\n"
        "- **同车不同号**：同一次车在交路上换分段时会挂**不同车次号**（上行/下行、套跑，"
        "如 G2365/G2368、D2238/D2235、Z184/Z181）。检索事实若已给出“同一次车不同车次号”"
        "或给出内部编号相同的两个车次号，必须直接说明二者是**同一次车**，"
        "并按当日实际开行的那个编号给数据，**不得**回“该车次不存在/查不到”。\n"
        "- **禁止自造印证**：不得声称“与另一来源完全一致/可交叉印证/相互验证”，"
        "除非检索事实中确实同时给出了**同一日期、同一对象**的两个可比数值；"
        "不同来源或不同日期的数值不得当作印证。\n"
        "- **截断必须声明**：若某条事实标注“已截断”或给出“命中 N 条/展示 M 条”，"
        "回答里必须原样声明总数与已展示条数，并禁止使用“没有/未包含/全部/只有”这类全集表述；"
        "用户若问的是被截掉的部分，应说明“需按条件缩小范围后重查”。\n"
        "- 回答末尾可用一行说明哪些内容来自检索、哪些来自模型知识。\n\n"
        f"{ctx_block}"
        f"用户原话：{user_message}\n"
        f"关键槽位 => {slot_lines}\n"
        f"工具调用：{trace or '(无)'}\n"
        f"问题性质 => {question_type or 'realtime'}\n\n"
        f"[检索事实]\n{data_anchor}\n\n"
        "[数据来源]\n" + ("\n".join(f"- {s}" for s in sources) if sources else "- (无实时来源)")
    )


async def generate(
    user_message: str,
    slots: Slots,
    retrieval: dict,
    history: list[dict] | None = None,
    question_type: str | None = None,
) -> tuple[str, list[str], str]:
    """生成最终回答，返回 (answer, sources, thinking)。"""
    prompt = build_prompt(user_message, slots, retrieval, history, question_type)
    # 历史已在 prompt 的 [对话历史] 区块中；不再重复传入 messages（见模块 docstring）
    answer, thinking = await chat_with_reasoning(prompt)
    return answer, retrieval.get("sources") or [], thinking