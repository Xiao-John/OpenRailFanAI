"""确定性 LLM mock（演示 / 无 Key CI 用）。

当 config.llm_mock=True 时，client.chat / client.chat_structured 改走本模块，
返回基于关键词/正则的固定结果，使三层整链在无真实 LLM 时也能确定性跑通。

⚠️ 判定输入必须是「用户输入」而不是完整 prompt（M11.1 修复）
    prompt 模板自身含有 few-shot 示例（如 'G1今天由哪组动车组担当？'、
    'CR400AF用的哪个品牌的动力系统？'），早期实现对整段 prompt 做关键词匹配，
    导致任何输入（包括"你好"）都会被示例里的"担当"命中 → 恒判 emu_routing + knowledge。
    后果：mock/CI 下 13 个工具的路由逻辑**从未被执行**，测试对真实链路零鉴别力。
    现在统一先切出 `[本次用户输入]` 段落（intent.py / extract.py 共用该标记）。
"""
from __future__ import annotations

import re

# intent.py / extract.py 的稳定标记：用户输入段落
_USER_SECTION_RE = re.compile(r"\[本次用户输入\]\s*\n(?P<user>.*?)(?:\n\s*\n|\Z)", re.S)


def user_text(prompt: str) -> str:
    """从 prompt 中切出用户输入段落；找不到标记时回退为整段（兼容直接调用）。"""
    m = _USER_SECTION_RE.search(prompt or "")
    return (m.group("user") if m else (prompt or "")).strip()


# ---------- 意图分类（匹配 INTENT_JSON_SCHEMA）----------
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


def _detect_intent(text: str) -> str:
    for kw, intent in _INTENT_KEYWORDS:
        if kw in text:
            return intent
    return "general"


# ---------- 槽位抽取（匹配 SLOTS_JSON_SCHEMA）----------
_RE_LOC = re.compile(r"[在在](?P<loc>[^，,。；;\s]{2,14})")          # 在吉林市XX区
_RE_TGT = re.compile(r"[A-Z]{1,2}\d{2,6}[A-Z]*")                     # CR400AF / G1865 / D122
_RE_TIME = re.compile(r"(今天下午|今天上午|今天|明天|后天|上午|下午|早上|晚上|今早|昨晚)")
_DIRS = ("上行", "下行", "方向")


def _extract_slots(text: str) -> dict:
    loc = _RE_LOC.search(text)
    tgt = _RE_TGT.search(text)
    time = _RE_TIME.search(text)
    direction = next((d for d in _DIRS if d in text), None)
    extra = ""
    return {
        "location": loc.group("loc") if loc else None,
        "target": tgt.group(0) if tgt else None,
        "time": time.group(1) if time else None,
        "direction": direction,
        "extra": extra or None,
    }


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


async def mock_structured(prompt: str, schema: dict) -> dict:
    """按 schema 的 properties 决定返回意图或槽位（只基于**用户输入**判定）。"""
    text = user_text(prompt)
    props = set((schema.get("properties") or {}).keys())
    if "intent" in props:
        out = {"intent": _detect_intent(text)}
        if "question_type" in props:
            out["question_type"] = _detect_question_type(text)
        return out
    if "location" in props:
        return _extract_slots(text)
    return {}


async def mock_chat(prompt: str, history: list[dict] | None = None) -> str:
    """对生成层的 prompt 做确定性摘要回复。

    history 存在时在回复中体现"已考虑上下文"，便于验证多轮链路。
    """
    import re as _re

    rows = []
    # 抽取 prompt 中「检索事实」与「数据来源」段落
    fact = ""
    m = _re.search(r"\n\[检索事实\]\n(?P<f>.*?)\n\[数据来源\]", prompt, _re.S)
    if m:
        fact = m.group("f").strip()
    src_lines = [l.strip().lstrip("-").strip() for l in prompt.splitlines() if l.strip().startswith("-")]

    ctx = ""
    if history:
        ctx = f"\n- 已参考前文 {len(history)} 条历史消息"

    answer = (
        "【Mock 回复】已解析你的请求。\n"
        f"- 检索到的事实如下：{fact or '（检索层未返回事实）'}\n"
        f"- 可参考数据来源：{'、'.join(src_lines) if src_lines else '（无）'}"
        f"{ctx}"
    )
    return answer


async def mock_stream_chunks(prompt: str, history: list[dict] | None = None) -> list[str]:
    """Mock 流式分块（供 stream_completion 使用）。"""
    answer = await mock_chat(prompt, history=history)
    # 按句切分，模拟流式增量
    parts = [p for p in answer.split("\n") if p]
    chunks: list[str] = []
    for i, p in enumerate(parts):
        chunks.append(p + ("\n" if i < len(parts) - 1 else ""))
    return chunks or [answer]
