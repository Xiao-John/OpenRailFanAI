"""工具基类与统一返回结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    ok: bool                    # 调用是否成功
    data: Any = None            # 结构化的数据
    text: str = ""              # 原始/摘要文本
    sources: list[str] = field(default_factory=list)  # 数据来源 URL
    error: str = ""             # 失败时的错误信息
    note: str = ""              # 附注（例如"接口需代理/未启用"）

    # ---- 数据完整性契约（2026-09-14 新增，用于消除"截断冒充缺失"）----
    # 集合类工具（余票/交路/径路）必须如实告知"命中多少、展示了多少、是否截断、用了什么过滤"，
    # 否则生成层会把"我只看到前 10 条"表述成"数据里没有"（实测事故 C02/C04）。
    total: Optional[int] = None       # 命中总数（未经截断）
    shown: Optional[int] = None       # 实际下发条数
    truncated: bool = False           # 是否存在截断（shown < total）
    filters: dict = field(default_factory=dict)   # 已应用的过滤条件（供模型如实转述）
    fetched_at: str = ""              # 数据采样时刻（UTC ISO8601）

    def integrity_line(self) -> str:
        """渲染成一行"数据完整性"说明（无集合语义时返回空串）。"""
        if self.total is None:
            return ""
        shown = self.shown if self.shown is not None else self.total
        parts = [f"命中 {self.total} 条", f"已展示 {shown} 条"]
        if self.filters:
            parts.append("过滤条件：" + "、".join(f"{k}={v}" for k, v in self.filters.items()))
        if self.fetched_at:
            parts.append(f"采样时刻 {self.fetched_at}")
        text = "；".join(parts)
        if self.truncated:
            text += "。**已截断**：以上不是全部结果，禁止使用“没有/未包含/全部/一共只有”这类全集表述"
        return text


class Tool:
    """数据源工具基类。子类实现 invoke 即可在检索层/Agent 使用。"""

    name: str = ""
    description: str = ""
    enabled: bool = True

    async def invoke(self, params: dict) -> ToolResult:
        raise NotImplementedError
