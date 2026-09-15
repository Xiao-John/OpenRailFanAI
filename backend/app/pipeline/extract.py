"""关键信息抽取层（槽位填充）。

用 LLM 结构化输出，从用户输入中抽取 location/target/time/
direction/extra 等槽位。示例：\"我在吉林市XX区，要拍 CR400AF，
今天下午\" → location=吉林市XX区、target=CR400AF、time=今天下午。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.context import format_history
from app.llm.client import chat_structured
from app.pipeline.schemas import SLOTS_JSON_SCHEMA


@dataclass
class Slots:
    location: str | None = None    # 地点
    target: str | None = None      # 车次/车型等目标对象
    time: str | None = None        # 时间表述
    direction: str | None = None   # 方向/区间（可选）
    extra: str | None = None       # 其余重要细节
    raw: dict = field(default_factory=dict)  # 保留原始解析结果

    def non_empty(self) -> dict[str, str]:
        """返回非空槽位，便于展示或检索。"""
        out: dict[str, str] = {}
        for name in ("location", "target", "time", "direction", "extra"):
            v = getattr(self, name)
            if v:
                out[name] = v
        return out


async def fill(message: str, *, intent: str | None = None, history: list[dict] | None = None) -> Slots:
    """从输入抽取关键槽位。

    intent 作为上下文提示，帮助模型决定哪些槽位更重要。
    history 用于消解指代：若本次输入省略了主体（如"那明天呢"），
    可从前文补全 location/target。
    """
    ctx = format_history(history)
    prompt = (
        "从下面的铁路相关请求中抽取关键信息。可参考 intent 以确定侧重点：\n"
        + (f"意图：{intent}\n" if intent else "")
        + (f"\n[前文对话]\n{ctx}\n" if ctx else "")
        + f"\n[本次用户输入]\n{message}\n\n"
        "填写规则：\n"
        "- 优先使用【本次用户输入】中的信息；\n"
        "- 若本次输入省略了主体（如仅说“那明天呢”），可从前文对话中继承"
        "（如地点、车次/车型）；\n"
        "- 继承来的值要填成完整、可直接检索的形式；确实没有的字段填 null。"
    )
    data = await chat_structured(prompt, schema=SLOTS_JSON_SCHEMA)
    data = data if isinstance(data, dict) else {}
    return Slots(
        location=_s(data.get("location")),
        target=_s(data.get("target")),
        time=_s(data.get("time")),
        direction=_s(data.get("direction")),
        extra=_s(data.get("extra")),
        raw=data,
    )


def _s(v) -> str | None:
    """仅接受有内容的字符串；bool/数字/列表等非字符串一律返回 None。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return None
