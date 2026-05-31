"""OpenAI Chat Completions API 流式 Provider。"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import openai

from xcodeagent.provider.base import BaseLLMProvider, ProviderError


class OpenAIProvider(BaseLLMProvider):
    """通过 OpenAI Chat Completions API 调用 GPT 等模型。"""

    def __init__(self, api_key: str, base_url: str):
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    def supports_extended_thinking(self) -> bool:
        return False

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> AsyncIterator[str]:
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": False},
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except openai.AuthenticationError as e:
            raise ProviderError(f"OpenAI 认证失败: {e}")
        except openai.RateLimitError as e:
            raise ProviderError(f"OpenAI 速率限制: {e}")
        except openai.APIStatusError as e:
            raise ProviderError(f"OpenAI API 错误 ({e.status_code}): {e}")
        except (asyncio.TimeoutError, ConnectionError) as e:
            raise ProviderError(f"网络错误: {e}")
