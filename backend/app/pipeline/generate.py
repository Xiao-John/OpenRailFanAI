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
import os
import re

from app.config import get_settings
from app.context import format_history
from app.llm.client import chat_with_reasoning
from app.llm import client as llm_client
from app.pipeline.extract import Slots
from app.ticket_copy import AVAILABLE_TRAIN_COUNT

# 生成层历史深度：比意图/抽取层（4 条）更深，用于对话连贯
GEN_HISTORY_LIMIT = 6

# ---- 作答策略（按 problem_type 切换是否允许使用模型自身知识）----
_POLICY_REALTIME = (
    "【作答策略：实时数据型】\n"
    "本节问题依赖实时/事实性数据（时刻、余票、担当车组、开行状态、径路里程等）。\n"
    "必须**只依据下方“检索事实”作答**，不得用模型记忆补充或推测这类数据。\n"
    "**列车时刻边界**：rail.re/车组交路记录的时间只代表记录时间，绝不能改写成发车、到达或停站时刻；"
    "多个车次必须逐车依据各自的查询结果，绝不能用相反方向、同一车底或另一车次的时刻推算缺失车次。\n"
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


# Main 的常识与闲聊采用自然简答；LM 保留原策略。
_MAIN_KNOWLEDGE_POLICY = (
    "【作答策略：知识与闲聊】\n"
    "直接回答用户的问题，可结合可靠的铁路常识；检索事实优先，冲突或不确定处如实说明。\n"
    "常识定义不必逐句附免责声明；未检索确认的具体型号、编号、年份和参数，"
    "只在相关内容处简短标注一次，不确定就不要猜精确值。\n"
    "检索为空仍可回答稳定常识，但不要冒充查证结果；需要来源说明时，"
    "在末尾一句说明依据常识、未经本次检索确认。未知事实直接说无法确认。\n"
)

_MAIN_KNOWLEDGE_REQUIREMENTS = (
    "回答要求：\n"
    "- 第一句就回答问题，不写‘结论先行’‘本次检索情况’等过程标题或开场白。"
    "简单定义、是非题和普通追问默认2—4句、约80—200字；闲聊通常1—2句。"
    "这是默认篇幅而非硬截断：用户要求详细、原理、对比、举例或多个问题时按需要展开。\n"
    "- 只补充理解答案必需的条件、原因或一个例子；追问承接上文，不重新讲整套背景。"
    "不主动罗列所有车型、技术参数、历史与罕见例外，不重复结论，不在末尾邀请继续提问。\n"
    "- 不复述工具调用、命中条数、检索日志或长段自我审计。来源只保留支持答案的必要引用，"
    "不贴无关搜索结果。事实确有截断且影响答案的完整性时，仍须说明原有总数与展示数，"
    "不把部分结果称全部；完整返回的搜索条数无需专门报告。\n"
    "- 历史仅用于理解追问，不作为本次检索事实；每条事实按自己的来源与时效判断。"
    "资讯采用有日期的最新来源，冲突须说明；无日期不能确认现状，不能由周年等反推日期。\n"
    "- 不猜当日时刻、余票、担当或开行状态；只能依据查询日实际事实。"
    "结构性事实可引用其他日期图定资料，但一句说明日期和参考性质，不冒充实际运行。"
    "交路记录时间不是到发时刻，多车次不能相互推算；已确认的同车不同号可按事实说明。\n"
    "- 不编造来源或交叉印证；只在同日期同对象存在可比来源时才能称印证。"
    "必要的不确定性、事实冲突与失败原因用一句说清，不用长免责声明代替答案。\n"
    "- 思考只做必要核对，不在思考中完整起草或复述答案；正文只回答一次。\n\n"
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


# 精简版作答策略：只留"能不能用模型知识"这一条分歧点。
# 长版里那 400 字主要是在解释**边界情形**（站点纠错例外等），
# 而 0.8B/2B 的实测失败模式是"读不完就走样"，不是"不知道边界"。
_POLICY_COMPACT = {
    "realtime": (
        "【作答策略：实时数据型】\n"
        "只依据下方「检索事实」作答，不用模型记忆补充或推测。事实不足就如实说明缺什么，不要编。\n"
        "rail.re/车组交路记录的时间不是列车到发时刻；多个车次必须逐车核对，不能用另一个车次反推。\n"
    ),
    "knowledge": (
        "【作答策略：知识型】\n"
        "可结合你的铁路常识作答；与检索事实冲突时以事实为准。"
        "事实没覆盖的内容要标注「据模型知识，未检索确认」，不确定的用「据称/可能」。\n"
    ),
    "mixed": (
        "【作答策略：混合型】\n"
        "实时部分（时刻/余票/担当）只依据检索事实；知识部分可结合常识，但要标注"
        "「据模型知识，未检索确认」。\n"
    ),
}


def answer_policy(question_type: str | None, *, compact: bool = False) -> str:
    """按问题性质返回作答策略文本（未知/缺省按 realtime 从严处理）。"""
    table = _POLICY_COMPACT if compact else _POLICY
    key = str(question_type or "realtime")
    return table.get(key) or (table["realtime"] if compact else _POLICY_REALTIME)


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
            label = AVAILABLE_TRAIN_COUNT if os.environ.get("APP_VARIANT", "main").lower() != "lm" else "有票席别统计"
            lines.append(label + "：" + "；".join(f"{k}={v} 趟" for k, v in seats.items()))
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
        # 径路明细较长：保留工具自渲染文本（同样受注入预算约束，且截断要声明）
        return _cap_fact_text(str(d.get("text") or "").strip(), settings)

    text = str(d.get("text") or "").strip()
    return _cap_fact_text(text, settings)


def _cap_fact_text(text: str, settings) -> str:
    """按注入预算截断单条事实，**并声明被截断**。

    为什么必须带声明：prompt 里有一条硬规则是"截断必须声明"，而这里原来是裸切片
    `text[:4000]` —— 模型看到的是被腰斩的内容，却没有任何线索说明"后面还有"，
    于是很容易把"我只看到一半"讲成"资料就这么多"。这与项目的完整性契约直接冲突。
    """
    limit = int(getattr(settings, "fact_text_max_chars", 4000))
    if len(text) <= limit:
        return text
    # 留出标记本身的长度，避免"截断后仍然超长"这种自欺
    marker = f"\n…（本条事实超长，已按注入上限截断；原文还有约 {len(text) - limit} 字未注入）"
    return text[: max(0, limit - len(marker))] + marker


# ---- 精简生成提示词（本地小模型用）----
#
# 为什么必须另写一份：**实测同一条真实生成提示词（2073 字）下**
#     0.8B   输出 1200 token（撞上限不会停）· 重复率 97% · 同一片段重复 68 次 · 编造站名
#     2B/4B/云端  119–234 token · 重复率 0% · 内容正确
# 而把提示词换成 227 字的精简版后，**同一个 0.8B** 输出 61 token、重复率 0%。
#
# 也就是说：长提示词不是"信息更多"，对 0.8B 这种规模它是**过载** ——
# 它会去逐条应付读不完的规则，最后抓住其中一条反复复读。
# 这与决策层的 `LLM_STRUCTURED_COMPACT_PROMPT` 是同一个教训，只是发生在生成层。
#
# 保留的是**会被真实扣分的那几条**，删掉的是解释性文字与长例子。
# 另外它顺带是**延迟优化**：设备端 prefill 是唯一瓶颈，2073→约 500 字，
# prefill 直接砍到约 1/4。
_LONG_REQUIREMENTS = (
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
)

_COMPACT_REQUIREMENTS = (
    "通用要求：\n"
    "- **只依据上面的检索事实作答**；事实里没有的不要补（知识型问题可补充，但要标注"
    "「据模型知识，未检索确认」）。\n"
    "- 每条事实有自己的时效说明，**各按各的判断**，不要用一条的说明去否定另一条。\n"
    "- 日期**直接采用事实里给出的**，不要自己推算；禁止从「开通 N 周年」这类表述倒推年份。\n"
    "- 结构性事实（经停站、站序、里程、编组）即使不是今日数据也可以拿来回答，"
    "但要用一句话说明其日期与「图定/计划」性质。\n"
    "- 事实若标注「已截断」或给出「命中 N 条/展示 M 条」，回答里要原样声明，"
    "并避免「全部/只有」这类说法。\n"
    "- 回答末尾用一行说明哪些来自检索。\n\n"
)

_STRUCTURED_REQUIREMENTS = (
    "通用要求：\n"
    "- 日期直接采用本次检索事实；每条事实的来源、日期与时效说明分别核对。"
    "历史回答不作为本次检索事实。\n"
    "- 当日实际到发、正晚点、余票和担当只能依据查询日实际数据；不可得须说明，"
    "其他日期的记录不得冒充今日或据此断言停运。\n"
    "- 经停、站序、历时、里程、编组是结构性事实：非今日图定数据也可引用，"
    "须明确日期及图定/计划性质，不称实际运行、不推断晚点；仅有站序不得补出时刻。\n"
    "- 交路记录时间不是到发时间。多车次逐车核对，不从相反方向、同车底或其他车次反推。"
    "事实确认同一次车不同车次号时须说明别名，采用当日实际开行编号。\n"
    "- 来源需同一日期、对象的可比数值才可称交叉印证；资讯采用带日期的最新来源，"
    "冲突须说明，无日期不能确认现状；禁止由推理反推时间节点（如开通 N 周年）。\n"
    "- 已截断或命中 N 条/展示 M 条须声明原有总数与展示数，不用全部/只有/没有等全集措辞；"
    "询问省略部分须建议按条件重查。空、部分失败与参考数据须如实说明，不编造。\n"
    "- 卡片展示具体事实，正文仅写必要的简短衔接（尽量不超过100字），不复述表格或记录。"
    "不要思考中起草正文；最后可一行说明来源。\n\n"
)


def is_structured_realtime(retrieval: dict, question_type: str | None) -> bool:
    """Optimize only pure rail cards; composite requests still need full prose.

    A photo-spot question can include emu.routing alongside geographic/search
    facts. The presence of that one card does not make the entire answer auxiliary.
    """
    tools = {str(item.get("tool") or "")
             for item in [*(retrieval.get("data") or []),
                          *(retrieval.get("display_errors") or [])]
             if isinstance(item, dict)}
    return (os.environ.get("APP_VARIANT", "main").lower() != "lm"
            and (question_type or "realtime") == "realtime"
            and bool(tools) and tools.issubset({"train.schedule", "emu.routing"}))


def completion_options(retrieval: dict, question_type: str | None) -> dict:
    """Reduce invisible structured prose while retaining explicit BYOK budgets."""
    if not is_structured_realtime(retrieval, question_type):
        return {}
    options = {"no_think": True}
    if not llm_client._spec_override("max_tokens"):
        options["max_tokens"] = min(int(get_settings().llm_max_tokens), 256)
    return options

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
    structured_tools = {str(item.get("tool") or "") for item in data_entries}

    settings = get_settings()
    # 注入条数上限（0 = 不限制）。**本地小模型必须收敛**：候选一多它就会
    # "逐条列出"与"总结论"自相矛盾（实测 4B 列出"G1 余 12 张"却下结论"没票"）。
    # 被丢弃的条数在这里**显式声明**，否则模型会把"我只看到 8 条"讲成"资料就这 8 条"。
    max_entries = int(getattr(settings, "fact_max_entries", 0) or 0)
    dropped_entries = 0
    if max_entries > 0 and len(data_entries) > max_entries:
        dropped_entries = len(data_entries) - max_entries
        data_entries = data_entries[:max_entries]
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
        if dropped_entries:
            total = dropped_entries + len(data_entries)
            blocks.append(
                f"…（本次共命中 {total} 条检索事实，受注入上限约束**仅列出前 "
                f"{len(data_entries)} 条**，另有 {dropped_entries} 条未列出。"
                f"回答涉及数量时必须原样说明"
                f"“共 {total} 条、仅列出 {len(data_entries)} 条”，"
                f"**禁止**使用“没有/未包含/全部/只有”这类全集表述；"
                f"用户若问的是未列出的部分，应说明需缩小范围后重查）"
            )
        data_anchor = "\n".join(blocks) or "(数据块为空)"
    else:
        data_anchor = "(无可引用的检索数据)"

    ctx = format_history(history, limit=GEN_HISTORY_LIMIT)
    ctx_block = f"[对话历史]\n{ctx}\n\n" if ctx else ""

    # 本地小模型走精简版（默认关，云端行为零变化），理由见 _COMPACT_REQUIREMENTS 上方注释
    compact_gen = bool(getattr(settings, "llm_generation_compact_prompt", False))
    requirements = (_STRUCTURED_REQUIREMENTS if is_structured_realtime(retrieval, question_type)
                    else _COMPACT_REQUIREMENTS if compact_gen else _LONG_REQUIREMENTS)
    policy = answer_policy(question_type, compact=compact_gen)
    if (os.environ.get("APP_VARIANT", "main").lower() != "lm"
            and question_type == "knowledge"):
        policy = _MAIN_KNOWLEDGE_POLICY
        requirements = _MAIN_KNOWLEDGE_REQUIREMENTS
    structured_boundary = ""
    if structured_tools.intersection({"train.schedule", "emu.routing"}):
        structured_boundary = (
            "【结构化事实展示边界】本次列车时刻/车组交路事实会由界面依据工具结构化结果单独展示。"
            "回答只写必要的说明或衔接，不要输出 Markdown 表格、逐站时刻表或重复列举结构化记录；"
            "具体事实字段以工具结果为准，不得改写、补全或从回答文本推断。\n"
        )

    return (
        "以下是一句铁路相关请求，我已解析意图与关键信息，并调用了数据源工具。\n"
        f"{policy}\n"
        f"{structured_boundary}"
        # 必须显式用 `+`：`requirements` 现在是变量，不能像原来那样靠相邻字符串字面量
        # 做隐式拼接 —— 从字面量换成变量时漏掉这一步就是一个语法错误。
        + requirements
        + f"{ctx_block}"
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
    from app.pipeline.ticket_answer import direct_answer, direct_complete
    receipt = direct_answer(retrieval)
    if direct_complete(retrieval):
        return receipt, retrieval.get("sources") or [], ""
    prompt = build_prompt(user_message, slots, retrieval, history, question_type)
    # 历史已在 prompt 的 [对话历史] 区块中；不再重复传入 messages（见模块 docstring）
    answer, thinking = await chat_with_reasoning(prompt, **completion_options(retrieval, question_type))
    if suppress_duplicate_structured_answer(answer, retrieval):
        answer = ""
    return answer, retrieval.get("sources") or [], thinking


def suppress_duplicate_structured_answer(answer: str, retrieval: dict) -> bool:
    """Reject model prose that repeats or conflicts with structured tool facts."""
    entries = retrieval.get("data") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool") or "")
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        if tool == "train.schedule":
            stops = data.get("stops") or data.get("routes") or []
            stations = {str(s.get("station") or s.get("station_name") or s.get("name") or "") for s in stops if isinstance(s, dict)} - {""}
            times = {str(s.get(k) or "") for s in stops if isinstance(s, dict) for k in ("arrive_time", "start_time", "arrive", "depart")} - {""}
            dates = {str(data.get("train_date") or "")}
            unsupported_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", answer)) - dates
            expected_md = {f"{d[5:7]}月{d[8:10]}日" for d in dates if len(d) == 10 and d[4] == "-"}
            mentioned_md = set(re.findall(r"\d{1,2}月\d{1,2}日", answer))
            unsupported_dates |= mentioned_md - expected_md
            if data.get("train_date") and str(data["train_date"]) != date.today().isoformat() and re.search(r"今天|今日|当日实际", answer):
                unsupported_dates.add("today-vs-result-date")
            mentioned_times = set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", answer))
            unsupported_times = mentioned_times - times
            if (sum(1 for value in stations if value in answer) >= 2
                    or sum(1 for value in times if value in answer) >= 3
                    or unsupported_dates or unsupported_times):
                return True
        elif tool == "emu.routing":
            records = data.get("records") or []
            codes = {str(r.get("train_code") or "") for r in records if isinstance(r, dict)} - {""}
            times = {str(r.get("time") or "") for r in records if isinstance(r, dict)} - {""}
            dates = {str(data.get("focus_date") or "")} | {str(r.get("date") or "") for r in records if isinstance(r, dict)}
            dates.discard("")
            unsupported_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", answer)) - dates
            expected_md = {f"{d[5:7]}月{d[8:10]}日" for d in dates if len(d) == 10 and d[4] == "-"}
            mentioned_md = set(re.findall(r"\d{1,2}月\d{1,2}日", answer))
            unsupported_dates |= mentioned_md - expected_md
            focus_date = str(data.get("focus_date") or "")
            if focus_date and focus_date != date.today().isoformat() and re.search(r"今天|今日|当日实际", answer):
                unsupported_dates.add("today-vs-result-date")
            mentioned_times = set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", answer))
            unsupported_times = mentioned_times - times
            if (sum(1 for value in codes if value in answer) >= 2
                    or sum(1 for value in times if value in answer) >= 2
                    or unsupported_dates or unsupported_times):
                return True
    return False
