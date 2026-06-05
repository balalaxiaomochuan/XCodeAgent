"""Agent 事件类型体系：ReAct 循环与 TUI 之间的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass


# ── 事件基类 ──────────────────────────────────────────────────


@dataclass
class AgentEvent:
    """所有 Agent 事件的基类，支持 match/case 模式匹配。"""
    pass


# ── 用户消息 ──────────────────────────────────────────────────


@dataclass
class UserMessage(AgentEvent):
    """用户输入的消息。"""
    content: str


# ── Thinking 事件（Extended Thinking 场景）────────────────────


@dataclass
class ThinkingStart(AgentEvent):
    """模型开始 thinking 阶段。"""
    pass


@dataclass
class ThinkingDelta(AgentEvent):
    """thinking 过程增量文本。"""
    text: str


@dataclass
class ThinkingEnd(AgentEvent):
    """thinking 阶段结束。"""
    pass


# ── 文本增量 ──────────────────────────────────────────────────


@dataclass
class TextDelta(AgentEvent):
    """模型输出的文本增量 token。"""
    text: str


# ── 工具调用事件 ──────────────────────────────────────────────


@dataclass
class ToolCallStart(AgentEvent):
    """工具调用开始（LLM 决定调用某个工具）。"""
    tool_use_id: str
    tool_name: str
    tool_input: dict


@dataclass
class ToolCallEnd(AgentEvent):
    """工具调用执行完毕。"""
    tool_use_id: str
    tool_name: str
    success: bool
    output: str = ""
    error: str | None = None


# ── 轮次事件 ──────────────────────────────────────────────────


@dataclass
class TurnStart(AgentEvent):
    """新一轮 ReAct 循环开始。"""
    round_number: int


@dataclass
class TurnEnd(AgentEvent):
    """一轮 ReAct 循环结束。"""
    round_number: int
    reason: str  # "end_turn" | "no_tools" | "max_rounds" | "cancelled"


# ── 最终结果 ──────────────────────────────────────────────────


@dataclass
class FinalReply(AgentEvent):
    """Agent 的最终文本回复。"""
    text: str


@dataclass
class AgentError(AgentEvent):
    """Agent 级别错误（非工具级别）。"""
    message: str
    exception: Exception | None = None


# ── 权限事件（V5 五层权限安全系统）───────────────────────────────


@dataclass
class PermissionRequest(AgentEvent):
    """权限系统需要用户确认工具调用时发出。Agent 循环在此事件后挂起。

    TUI 收到此事件后弹出确认对话框，用户做出选择后回传 PermissionResponse。
    """
    request_id: str
    tool_call_id: str       # 原始 tool_use_id
    tool_name: str          # 工具名称
    tool_input: dict        # 工具参数
    risk_level: str         # "low" / "medium" / "high" / "critical"
    source_layer: int       # 触发确认的层级 (3 或 4)
    source_description: str # 人类可读的触发原因
    summary: str            # 单行摘要，TUI 确认对话框标题用


@dataclass
class PermissionResponse(AgentEvent):
    """用户对 PermissionRequest 的响应，由 TUI 发回给 Agent。

    在 Agent 循环内部使用 asyncio.Queue 传递，不作为 Agent.run() 的
    正常事件流的一部分。
    """
    request_id: str
    tool_call_id: str
    decision: str  # "allow_once" | "allow_all" | "deny"
