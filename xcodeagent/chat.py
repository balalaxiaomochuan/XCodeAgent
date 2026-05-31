"""对话会话管理：维护消息历史，封装 LLM 调用和工具调用。"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from xcodeagent.tools.base import ToolResult
from xcodeagent.tools.executor import ToolExecutor
from xcodeagent.tools.registry import ToolRegistry
from xcodeagent.provider.base import BaseLLMProvider, TextDelta, ToolCall


class ChatSession:
    """管理多轮对话的消息历史和 LLM 交互。"""

    def __init__(
        self,
        provider: BaseLLMProvider,
        model: str,
        system_prompt: Optional[str] = None,
        extended_thinking: Optional["ExtendedThinkingConfig"] = None,
    ):
        self._provider = provider
        self._model = model
        self._extended_thinking = extended_thinking
        self._messages: list[dict] = []

        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

    @property
    def messages(self) -> list[dict]:
        """返回当前消息历史（只读引用）。"""
        return self._messages

    def clear(self) -> None:
        """清空对话历史，保留 system prompt（如果有的话）。"""
        self._messages = [m for m in self._messages if m["role"] == "system"]

    # ── 纯文本对话（保留原有接口）────────────────────────────

    async def send(self, user_input: str) -> AsyncIterator[str]:
        """发送用户消息，返回流式 AI 回复（纯文本）。

        Args:
            user_input: 用户输入文本。

        Yields:
            AI 回复的文本 chunk（逐 token）。
        """
        self._messages.append({"role": "user", "content": user_input})

        kwargs: dict = {}
        if (
            self._extended_thinking is not None
            and self._extended_thinking.enabled
            and self._provider.supports_extended_thinking()
        ):
            kwargs["thinking"] = {
                "budget_tokens": self._extended_thinking.budget_tokens,
            }

        full_response = ""
        async for token in self._provider.chat_stream(
            messages=self._messages,
            model=self._model,
            **kwargs,
        ):
            full_response += token
            yield token

        self._messages.append({"role": "assistant", "content": full_response})

    # ── 工具调用对话 ────────────────────────────────────────

    async def send_with_tools(
        self,
        user_input: str,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
    ) -> AsyncIterator[str | ToolCall | ToolResult]:
        """发送用户消息，支持工具调用（单步，不循环）。

        Args:
            user_input: 用户输入文本。
            tool_executor: 工具执行引擎。
            tool_registry: 工具注册中心（用于获取 tool definitions）。

        Yields:
            - str: 文本 chunk（流式展示）
            - ToolCall: 模型返回的工具调用（在文本之后）
            - ToolResult: 工具执行结果
        """
        self._messages.append({"role": "user", "content": user_input})

        # 收集流式回复中的 text 和 tool_use block
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        try:
            async for delta in self._provider.chat_with_tools(
                messages=self._messages,
                model=self._model,
                tools=tool_registry.get_definitions(),
            ):
                if isinstance(delta, TextDelta):
                    text_parts.append(delta.text)
                    yield delta.text
                elif isinstance(delta, ToolCall):
                    tool_calls.append(delta)
        except Exception:
            # 异常时清掉已追加的 user 消息状态
            self._messages.pop()
            raise

        # 构建 assistant 消息（content blocks 格式）
        assistant_content: list[dict] = []
        full_text = "".join(text_parts)
        if full_text.strip():
            assistant_content.append({"type": "text", "text": full_text})
        for tc in tool_calls:
            assistant_content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        self._messages.append({"role": "assistant", "content": assistant_content})

        # 执行工具调用
        tool_result_blocks: list[dict] = []
        for tc in tool_calls:
            yield tc  # TUI 可以展示 "正在调用 xxx..."

            result = await tool_executor.call(tc.name, tc.input)
            yield result  # TUI 可以展示执行结果

            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result.output if result.success else f"Error: {result.error}",
            })

        # 追加 tool_result 消息到历史
        if tool_result_blocks:
            self._messages.append({
                "role": "user",
                "content": tool_result_blocks,
            })
