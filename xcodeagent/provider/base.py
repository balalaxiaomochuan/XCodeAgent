"""Provider 抽象基类 — 定义统一的 LLM 调用接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Union


class ProviderError(Exception):
    """LLM API 调用相关错误（认证失败、网络超时、API 错误等）。"""
    pass


# ── 流式回复的两种 delta 类型 ──────────────────────────────────


@dataclass
class TextDelta:
    """模型输出的文本 token。"""
    text: str


@dataclass
class ThinkingDelta:
    """模型 extended thinking 过程的增量文本（仅 Anthropic）。"""
    text: str


@dataclass
class ToolCall:
    """模型返回的工具调用（完整解析，非增量）。"""
    id: str
    name: str
    input: dict


ChatDelta = Union[TextDelta, ThinkingDelta, ToolCall]
"""chat_with_tools() 流式返回的 delta 类型。"""


# ── 抽象基类 ──────────────────────────────────────────────────


class BaseLLMProvider(ABC):
    """所有 LLM 后端的统一抽象接口。

    消息格式约定：内部统一使用 OpenAI 格式
    [{"role": "user"|"assistant"|"system", "content": "..."}]
    """

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> AsyncIterator[str]:
        """流式调用 LLM，逐 token yield 纯文本。"""
        ...

    @abstractmethod
    async def chat_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        **kwargs,
    ) -> AsyncIterator[ChatDelta]:
        """流式调用 LLM 并支持工具调用。

        返回 TextDelta（文本 token，用于 TUI 实时渲染）和
        ToolCall（完整解析的工具调用，id/name/input 已填好）。

        Args:
            messages: 对话消息列表（OpenAI 格式）。
            model: 模型 ID。
            tools: 工具定义列表（Anthropic tool use 格式）。
            **kwargs: 后端特定参数。

        Yields:
            TextDelta 或 ToolCall。
        """
        ...

    @abstractmethod
    def supports_extended_thinking(self) -> bool:
        """当前后端是否支持 extended thinking。"""
        ...
