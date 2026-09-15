"""多轮上下文处理工具。

职责：
1. 裁剪历史（限制轮数与字符数），防止超出上下文窗口
2. 把历史渲染成可嵌入 prompt 的紧凑文本

设计约定（M11.1 明确口径）
- 历史**只以「prompt 内的紧凑区块」一种形式**进入 LLM 调用：
  意图/抽取层 `format_history(limit=4)`（消解指代够用，省 token），
  生成层 `format_history(limit=6)`（对话连贯优先，见 `generate.GEN_HISTORY_LIMIT`）。
- 早期生成层还把原始历史塞进 messages 数组，与区块重复注入
  （实测单次生成 prompt 里约 1760 字符是重复历史）→ 已取消，见
  历史审计结论（已归档）。
- 生成层深度比意图层更深是**刻意的不对称**：意图分类只需最近 1-2 轮消解指代，
  而生成回答需要更完整的上下文。
"""
from __future__ import annotations

MAX_TURNS = 6          # 最多保留最近 6 条消息（约 3 轮）
MAX_CHARS_PER_MSG = 800
MAX_TOTAL_CHARS = 3000


def trim_history(history: list[dict] | None) -> list[dict]:
    """裁剪历史：取最近 MAX_TURNS 条，单条与总量都做字符上限。"""
    if not history:
        return []
    cleaned: list[dict] = []
    for h in history:
        role = (h or {}).get("role")
        content = str((h or {}).get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if len(content) > MAX_CHARS_PER_MSG:
            content = content[:MAX_CHARS_PER_MSG] + "…"
        cleaned.append({"role": role, "content": content})

    cleaned = cleaned[-MAX_TURNS:]

    # 从最旧开始丢，直到总量达标
    while cleaned and sum(len(h["content"]) for h in cleaned) > MAX_TOTAL_CHARS:
        cleaned.pop(0)
    return cleaned


def format_history(history: list[dict] | None, *, limit: int = 4) -> str:
    """把历史渲染成紧凑文本（供嵌入 prompt 消解指代）。

    只保留最近 limit 条；assistant 回复只取开头片段（避免噪声）。
    """
    trimmed = trim_history(history)[-limit:]
    if not trimmed:
        return ""
    lines: list[str] = []
    for h in trimmed:
        role = "用户" if h["role"] == "user" else "助手"
        content = h["content"]
        if h["role"] == "assistant" and len(content) > 160:
            content = content[:160] + "…"
        lines.append(f"{role}：{content}")
    return "\n".join(lines)
