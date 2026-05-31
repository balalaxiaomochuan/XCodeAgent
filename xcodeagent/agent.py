"""ReAct Agent 主循环：工具结果自动回填，多轮自主推理。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator

from xcodeagent.chat import ChatSession
from xcodeagent.events import (
    AgentError,
    AgentEvent,
    FinalReply,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    TurnStart,
    UserMessage,
)
from xcodeagent.provider.base import TextDelta as ProviderTextDelta
from xcodeagent.provider.base import ThinkingDelta as ProviderThinkingDelta
from xcodeagent.provider.base import ToolCall as ProviderToolCall
from xcodeagent.tools.base import ToolResult
from xcodeagent.tools.executor import ToolExecutor


# ── 配置 ──────────────────────────────────────────────────────


@dataclass
class AgentConfig:
    """Agent 行为配置。"""
    max_rounds: int = 20
    plan_only: bool = False
    tool_timeout: float = 120.0


# ── Agent ─────────────────────────────────────────────────────


class Agent:
    """ReAct Agent：思考 → 调工具 → 拿到结果 → 再思考 → 循环。

    通过 run() 的 AsyncIterator[AgentEvent] 对外暴露完整执行过程，
    上层（TUI / CLI）按需消费。
    """

    def __init__(
        self,
        chat_session: ChatSession,
        tool_executor: ToolExecutor,
        config: AgentConfig | None = None,
    ):
        self._chat = chat_session
        self._executor = tool_executor
        self._config = config or AgentConfig()

    # ── 主入口 ─────────────────────────────────────────────

    async def run(
        self,
        user_input: str,
        cancel_token: asyncio.Event | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """执行 ReAct 循环。

        Args:
            user_input: 用户输入的文本。
            cancel_token: 外部取消信号，设置后 Agent 在下一轮开始前退出。

        Yields:
            AgentEvent 子类实例，按时间顺序。
        """
        yield UserMessage(content=user_input)
        self._chat.messages.append({"role": "user", "content": user_input})

        for round_num in range(1, self._config.max_rounds + 1):
            # ── 检查外部取消 ──
            if cancel_token and cancel_token.is_set():
                yield TurnEnd(round_number=round_num, reason="cancelled")
                break

            yield TurnStart(round_number=round_num)

            # ── 调用 LLM ──
            tool_calls: list[ProviderToolCall] = []
            text_parts: list[str] = []

            tool_definitions = self._executor.registry.get_definitions()

            async for delta in self._chat.provider.chat_with_tools(
                messages=self._chat.messages,
                model=self._chat.model,
                tools=tool_definitions,
            ):
                if isinstance(delta, ProviderTextDelta):
                    text_parts.append(delta.text)
                    yield TextDelta(text=delta.text)
                elif isinstance(delta, ProviderThinkingDelta):
                    yield ThinkingDelta(text=delta.text)
                elif isinstance(delta, ProviderToolCall):
                    tool_calls.append(delta)
                    yield ToolCallStart(
                        tool_use_id=delta.id,
                        tool_name=delta.name,
                        tool_input=delta.input,
                    )

            # ── 构建 assistant 消息 ──
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
            self._chat.messages.append({"role": "assistant", "content": assistant_content})

            # ── 无工具调用 → 结束 ──
            if not tool_calls:
                yield FinalReply(text=full_text)
                yield TurnEnd(round_number=round_num, reason="no_tools")
                break

            # ── 执行工具（分批：读并发、写串行）─��
            all_results = await self._executor.execute_batch(tool_calls)

            for tc in tool_calls:
                result = all_results[tc.id]
                yield ToolCallEnd(
                    tool_use_id=tc.id,
                    tool_name=tc.name,
                    success=result.success,
                    output=result.output,
                    error=result.error,
                )

            # ── 回填工具结果 ──
            tool_result_blocks: list[dict] = []
            for tc in tool_calls:
                result = all_results[tc.id]
                content = result.output if result.success else f"Error: {result.error}"
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": content,
                })

            if tool_result_blocks:
                self._chat.messages.append({
                    "role": "user",
                    "content": tool_result_blocks,
                })

            yield TurnEnd(round_number=round_num, reason="continue")

        else:
            # 达到最大轮数
            yield TurnEnd(round_number=self._config.max_rounds, reason="max_rounds")
            yield AgentError(message="达到最大循环轮数，Agent 已停止")

    # ── Plan-only 工具拦截 ──────────────────────────────────

    async def _execute_tool(self, tool_call: ProviderToolCall) -> ToolResult:
        """执行单个工具，plan-only 模式下拦截写操作。"""
        try:
            tool = self._executor.registry.get(tool_call.name)
        except KeyError:
            return ToolResult(success=False, error=f"未知工具: {tool_call.name}")

        if self._config.plan_only and tool.category == "write":
            return ToolResult(
                success=False,
                error=(
                    f"[Plan-only 模式] 写操作 '{tool_call.name}' 已拦截。"
                    "如需执行，请运行 /plan-off 关闭计划模式。"
                ),
            )

        return await self._executor.call(tool_call.name, tool_call.input)
