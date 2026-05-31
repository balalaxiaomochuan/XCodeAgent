"""Anthropic Claude API 流式 Provider。"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

import anthropic

from xcodeagent.provider.base import BaseLLMProvider, ProviderError


class AnthropicProvider(BaseLLMProvider):
    """通过 Anthropic Messages API 调用 Claude 模型。"""

    def __init__(self, api_key: str, base_url: str):
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
        )

    def supports_extended_thinking(self) -> bool:
        return True

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> AsyncIterator[str]:
        system_prompt, api_messages = self._convert_messages(messages)

        thinking_config = self._build_thinking_config(**kwargs)

        stream_kwargs: dict = {
            "model": model,
            "max_tokens": 8192,
            "messages": api_messages,
        }
        if system_prompt:
            stream_kwargs["system"] = system_prompt
        if thinking_config:
            stream_kwargs["thinking"] = thinking_config

        try:
            async with self._client.beta.messages.stream(**stream_kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield event.delta.text
        except anthropic.AuthenticationError as e:
            raise ProviderError(f"Anthropic 认证失败: {e}")
        except anthropic.RateLimitError as e:
            raise ProviderError(f"Anthropic 速率限制: {e}")
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Anthropic API 错误 ({e.status_code}): {e}")
        except (asyncio.TimeoutError, ConnectionError) as e:
            raise ProviderError(f"网络错误: {e}")

    @staticmethod
    def _convert_messages(
        messages: list[dict],
    ) -> tuple[str, list[dict]]:
        """将 OpenAI 格式消息转换为 Anthropic API 格式。

        提取 system 角色的消息作为顶层 system 参数，
        其余消息直接映射 role 和 content。
        """
        system_parts: list[str] = []
        api_messages: list[dict] = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                system_parts.append(content)
            else:
                api_messages.append({"role": role, "content": content})

        system_prompt = "\n\n".join(system_parts) if system_parts else ""
        return system_prompt, api_messages

    @staticmethod
    def _build_thinking_config(**kwargs) -> Optional[dict]:
        """根据 kwargs 构建 Anthropic thinking 参数。"""
        thinking: Optional[dict] = kwargs.get("thinking")
        if thinking is None:
            return None
        return {
            "type": "enabled",
            "budget_tokens": thinking.get("budget_tokens", 4000),
        }
