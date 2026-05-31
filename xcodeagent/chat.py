"""对话会话管理：维护消息历史，封装 LLM 调用。"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from xcodeagent.provider.base import BaseLLMProvider


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

    async def send(self, user_input: str) -> AsyncIterator[str]:
        """发送用户消息，返回流式 AI 回复。

        Args:
            user_input: 用户输入文本。

        Yields:
            AI 回复的文本块（逐 token）。
        """
        self._messages.append({"role": "user", "content": user_input})

        # 构建 provider 所需的额外参数
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
