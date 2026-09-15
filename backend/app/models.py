"""共享的数据模型：三层流水线的结果结构。

独立于此模块以避免 chat 路由与 orchestrator 之间的循环导入。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.config import get_settings


# ---- 多轮上下文 ----
class ChatMessage(BaseModel):
    """一条历史消息（用于多轮上下文）。"""

    role: Literal["user", "assistant"] = Field(..., description="消息角色")
    content: str = Field("", description="消息文本内容")


# ---- 入参 ----
class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        description="用户输入的一句自然语言，如：吉林市XX区，要拍CR400AF，今天下午",
    )
    session_id: Optional[str] = Field(None, description="会话标识（前端生成，用于日志关联）")
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="此前已完成的多轮对话（不含本次 message），用于上下文理解",
    )

    @field_validator("message")
    @classmethod
    def _check_message(cls, v: str) -> str:
        """空消息与超长消息必须被拒绝（超长输入会直接放大 token 成本与延迟）。

        上限取 `MAX_MESSAGE_CHARS`（默认 2000）：正常提问远低于此值，
        而审计实测过"20 万字符照跑"的情况。
        """
        text = (v or "").strip()
        if not text:
            raise ValueError("message 不能为空")
        limit = get_settings().max_message_chars
        if len(text) > limit:
            raise ValueError(f"message 过长（{len(text)} 字，上限 {limit} 字）")
        return text


# ---- 出参 ----
class SlotValue(BaseModel):
    """抽取出的关键信息槽位。"""

    name: str
    value: Optional[str] = None


class PipelineResult(BaseModel):
    intent: str = Field(..., description="识别出的意图")
    question_type: str = Field(
        "realtime",
        description="问题性质：realtime（须依据检索事实）/ knowledge（可用模型知识）/ mixed",
    )
    slots: list[SlotValue] = Field(default_factory=list)    # 关键信息槽位
    answer: str = ""
    thinking: str = Field("", description="模型思考过程（think 内容，可前端展示）")
    sources: list[str] = Field(default_factory=list)        # 引用的数据来源 URL
    tool_trace: list[str] = Field(default_factory=list)     # 调用的工具与关键操作（调试用）
    process_logs: list[str] = Field(default_factory=list)   # 流程日志：各阶段耗时/工具/计费（可折叠）
    planner: str = Field(
        "llm-legacy",
        description="决策来源：deterministic（确定性快路径）/ llm-merged（合并调用）/ llm-legacy（两次调用）",
    )
    usage: dict = Field(default_factory=lambda: {
        "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
    })                                                    # 本请求累计 token（输入/输出/总）
    latency_ms: float = Field(default=0.0, description="本次流水线整体耗时（毫秒）")