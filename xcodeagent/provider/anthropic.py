"""Anthropic Claude API 流式 Provider — 支持纯对话和工具调用。"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

import anthropic

from xcodeagent.provider.base import (
    BaseLLMProvider,
    ChatDelta,
    ProviderError,
    TextDelta,
    ToolCall,
)


class AnthropicProvider(BaseLLMProvider):
    """通过 Anthropic Messages API 调用 Claude 模型。"""

    def __init__(self, api_key: str, base_url: str):
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
        )

    def supports_extended_thinking(self) -> bool:
        return True

    # ── 纯文本流式（保留原有接口）────────────────────────────

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

    # ── 工具调用流式 ────────────────────────────────────────

    async def chat_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        **kwargs,
    ) -> AsyncIterator[ChatDelta]:
        system_prompt, api_messages = self._convert_messages(messages)

        stream_kwargs: dict = {
            "model": model,
            "max_tokens": 8192,
            "messages": api_messages,
            "tools": tools,
        }
        if system_prompt:
            stream_kwargs["system"] = system_prompt

        # 每个 content block index → 累积状态
        blocks: dict[int, dict] = {}  # index → {type, name, id, json_fragments}

        try:
            async with self._client.beta.messages.stream(**stream_kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        blocks[event.index] = {
                            "type": block.type,
                            "name": getattr(block, "name", None),
                            "id": getattr(block, "id", None),
                            "json_fragments": [],
                        }

                    elif event.type == "content_block_delta":
                        delta = event.delta
                        block = blocks.get(event.index, {})

                        if delta.type == "text_delta":
                            yield TextDelta(text=delta.text)
                        elif delta.type == "input_json_delta":
                            block["json_fragments"].append(delta.partial_json)

                    elif event.type == "content_block_stop":
                        block = blocks.get(event.index, {})
                        if block.get("type") == "tool_use":
                            json_str = "".join(block["json_fragments"])
                            try:
                                tool_input = json.loads(json_str) if json_str else {}
                            except json.JSONDecodeError:
                                tool_input = {}
                            yield ToolCall(
                                id=block["id"],
                                name=block["name"],
                                input=tool_input,
                            )

        except anthropic.AuthenticationError as e:
            raise ProviderError(f"Anthropic 认证失败: {e}")
        except anthropic.RateLimitError as e:
            raise ProviderError(f"Anthropic 速率限制: {e}")
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Anthropic API 错误 ({e.status_code}): {e}")
        except (asyncio.TimeoutError, ConnectionError) as e:
            raise ProviderError(f"网络错误: {e}")

    # ── 消息格式转换 ────────────────────────────────────────

    @staticmethod
    def _convert_messages(
        messages: list[dict],
    ) -> tuple[str, list[dict]]:
        """将内部消息格式转换为 Anthropic API 格式。

        提取 system 角色为顶层 system 参数。
        content 可以是字符串或 content block 列表。
        """
        system_parts: list[str] = []
        api_messages: list[dict] = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                api_messages.append({"role": role, "content": content})
            elif role == "tool":
                # OpenAI 格式的 tool result → Anthropic 格式
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": content if isinstance(content, str) else str(content),
                        }
                    ],
                })
            elif isinstance(content, list):
                # content 已是 content block 列表（如 assistant 的 text + tool_use）
                api_messages.append({"role": role, "content": content})
            else:
                api_messages.append({"role": role, "content": content})

        system_prompt = "\n\n".join(system_parts) if system_parts else ""
        return system_prompt, api_messages

    # ── 辅助 ────────────────────────────────────────────────

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
