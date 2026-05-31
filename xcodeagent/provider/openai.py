"""OpenAI Chat Completions API 流式 Provider — 支持纯对话和函数调用。"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import openai

from xcodeagent.provider.base import (
    BaseLLMProvider,
    ChatDelta,
    ProviderError,
    TextDelta,
    ToolCall,
)


class OpenAIProvider(BaseLLMProvider):
    """通过 OpenAI Chat Completions API 调用 GPT 等模型。"""

    def __init__(self, api_key: str, base_url: str):
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    def supports_extended_thinking(self) -> bool:
        return False

    # ── 纯文本流式（保留原有接口）────────────────────────────

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

    # ── 工具调用流式 ────────────────────────────────────────

    async def chat_with_tools(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        **kwargs,
    ) -> AsyncIterator[ChatDelta]:
        # Anthropic tool use 格式 → OpenAI function calling 格式
        openai_tools = self._convert_tools(tools)
        openai_messages = self._convert_messages(messages)

        # 记录累积的 tool_call（按 index）
        pending_tool_calls: dict[int, dict] = {}

        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=openai_messages,
                tools=openai_tools,
                stream=True,
                stream_options={"include_usage": False},
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta

                # 文本 token
                if delta.content:
                    yield TextDelta(text=delta.content)

                # 函数调用
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in pending_tool_calls:
                            pending_tool_calls[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        entry = pending_tool_calls[idx]
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function and tc.function.name:
                            entry["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            entry["arguments"] += tc.function.arguments

        except openai.AuthenticationError as e:
            raise ProviderError(f"OpenAI 认证失败: {e}")
        except openai.RateLimitError as e:
            raise ProviderError(f"OpenAI 速率限制: {e}")
        except openai.APIStatusError as e:
            raise ProviderError(f"OpenAI API 错误 ({e.status_code}): {e}")
        except (asyncio.TimeoutError, ConnectionError) as e:
            raise ProviderError(f"网络错误: {e}")

        # 流结束后，解析累积的 tool_calls 并 yield 完整的 ToolCall
        for idx in sorted(pending_tool_calls.keys()):
            entry = pending_tool_calls[idx]
            if entry["name"] and entry["arguments"]:
                try:
                    tool_input = json.loads(entry["arguments"])
                except json.JSONDecodeError:
                    tool_input = {}
                yield ToolCall(
                    id=entry["id"],
                    name=entry["name"],
                    input=tool_input,
                )

    # ── 格式转换 ────────────────────────────────────────────

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        """Anthropic tool use 格式 → OpenAI function calling 格式。

        Anthropic: {name, description, input_schema}
        OpenAI:    {type: "function", function: {name, description, parameters}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            }
            for t in tools
        ]

    @staticmethod
    def _convert_messages(messages: list[dict]) -> list[dict]:
        """将内部消息格式转为 OpenAI 格式。

        内部存储使用 Anthropic content block 格式，需转为 OpenAI 格式：
        - assistant 的 content blocks → OpenAI content + tool_calls
        - tool result（role: user + tool_result block）→ role: tool
        """
        result: list[dict] = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "system":
                result.append({"role": "system", "content": content})

            elif role == "assistant" and isinstance(content, list):
                # Anthropic content blocks → OpenAI assistant + tool_calls
                text_parts: list[str] = []
                tool_calls: list[dict] = []

                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })

                entry: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                result.append(entry)

            elif role == "user" and isinstance(content, list):
                # Anthropic tool_result block → OpenAI tool role
                tool_results: list[dict] = []
                text_parts: list[str] = []

                for block in content:
                    if block.get("type") == "tool_result":
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": block.get("content", ""),
                        })
                    elif block.get("type") == "text":
                        text_parts.append(block.get("text", ""))

                if text_parts:
                    result.append({"role": "user", "content": "\n".join(text_parts)})
                result.extend(tool_results)

            elif role == "tool":
                # 已经是 OpenAI tool 格式，直接透传
                result.append(msg)

            else:
                result.append({"role": role, "content": content})

        return result
