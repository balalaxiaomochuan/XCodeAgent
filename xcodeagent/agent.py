"""ReAct Agent 主循环：工具结果自动回填，多轮自主推理。"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator

from xcodeagent.chat import ChatSession
from xcodeagent.events import (
    AgentError,
    AgentEvent,
    FinalReply,
    PermissionRequest,
    PermissionResponse,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    TurnStart,
    UserMessage,
)
from xcodeagent.permission import PermissionManager, PermissionConfig
from xcodeagent.provider.base import TextDelta as ProviderTextDelta
from xcodeagent.provider.base import ThinkingDelta as ProviderThinkingDelta
from xcodeagent.provider.base import ToolCall as ProviderToolCall
from xcodeagent.tools.base import ToolResult
from xcodeagent.tools.executor import ToolExecutor

if TYPE_CHECKING:
    from xcodeagent.context import ContextManager


# ── 配置 ──────────────────────────────────────────────────────


@dataclass
class AgentConfig:
    """Agent 行为配置。"""
    max_rounds: int = 50
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
        permission_manager: PermissionManager | None = None,
        context_manager: "ContextManager | None" = None,
    ):
        self._chat = chat_session
        self._executor = tool_executor
        self._config = config or AgentConfig()
        self._permission = permission_manager
        self._context_manager = context_manager

        # Agent → TUI 的权限请求通道
        self._perm_request_queue: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        # TUI → Agent 的权限响应通道
        self.permission_response_queue: asyncio.Queue[PermissionResponse] = (
            asyncio.Queue()
        )

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

        # 新一轮开始，重置权限状态
        if self._permission:
            self._permission.reset_round()

        # 追踪上一轮的工具 ID，用于上下文压缩第一层检查
        prev_tool_ids: list[str] = []

        for round_num in range(1, self._config.max_rounds + 1):
            # ── 检查外部取消 ──
            if cancel_token and cancel_token.is_set():
                yield TurnEnd(round_number=round_num, reason="cancelled")
                break

            # ── 上下文检查：第一层卸载 + 第二层 auto-compact ──
            if self._context_manager:
                await self._context_manager.pre_round_check(
                    self._chat, round_num, prev_tool_ids
                )
                prev_tool_ids = []

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

            # ── 执行工具（分批：读并发、写串行）──
            all_results: dict[str, ToolResult] = {}

            # 按 category 分组
            reads, writes = self._partition_tools(tool_calls)

            # 读工具并发执行（读工具默认跳过权限检查）
            if reads:
                results = await asyncio.gather(*[
                    self._executor._execute_safe(tc) for tc in reads
                ])
                for tc, result in zip(reads, results):
                    all_results[tc.id] = result

            # 写工具串行执行（每个经过权限检查）
            for tc in writes:
                result = await self._execute_write_tool(tc)
                all_results[tc.id] = result

            # 发出工具结果事件
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

            # 记录本轮工具 ID，供下一轮上下文检查
            prev_tool_ids = [tc.id for tc in tool_calls]

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

    # ── 写工具执行（含权限检查）──────────────────────────────

    async def _execute_write_tool(self, tool_call: ProviderToolCall) -> ToolResult:
        """执行单个写工具，先经过权限检查。

        权限检查流程:
            1. plan_only 模式 → 直接拦截
            2. 调用 PermissionManager.check()
            3. ALLOW → 直接执行
            4. BLOCK → 返回失败 ToolResult
            5. ASK → 构造 PermissionRequest 并通过队列等待 TUI 响应

        注意: 步骤 5 的 PermissionRequest 不是通过 yield 发送到事件流的，
        而是通过一个独立的 asyncio.Queue。TUI 需要通过该队列获取请求并渲染对话框。
        """
        # ── plan_only 模式快捷拦截 ──
        if self._config.plan_only:
            try:
                tool = self._executor.registry.get(tool_call.name)
            except KeyError:
                return ToolResult(success=False, error=f"未知工具: {tool_call.name}")

            if tool.category == "write":
                return ToolResult(
                    success=False,
                    error=(
                        f"[Plan-only 模式] 写操作 '{tool_call.name}' 已拦截。"
                        "如需执行，请运行 /plan-off 关闭计划模式。"
                    ),
                )

        # ── 无权限管理器 → 直接执行 ──
        if self._permission is None:
            return await self._executor._execute_safe(tool_call)

        # ── 权限检查 ──
        perm_result = self._permission.check(tool_call.name, tool_call.input)

        # ALLOW → 直接执行
        if perm_result.is_allow:
            return await self._executor._execute_safe(tool_call)

        # BLOCK → 返回拦截结果
        if perm_result.is_block:
            return ToolResult(success=False, error=perm_result.reason)

        # ASK → 等待用户确认
        request_id = str(uuid.uuid4())
        request = PermissionRequest(
            request_id=request_id,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            tool_input=tool_call.input,
            risk_level=perm_result.risk_level,
            source_layer=perm_result.source_layer,
            source_description=perm_result.reason,
            summary=perm_result.summary or self._make_summary(tool_call),
        )

        # 把 request 放入 TUI 可以读取的位置
        # 同时把 request 通过外部队列发送给 TUI
        self._last_permission_request = request
        # TUI 通过 permission_response_queue 传回响应
        # 但 TUI 需要先知道有 request...
        # 方案: Agent 通过独立的事件通知机制告诉 TUI
        # 实际做法: TUI 在 agent.run() 循环中同时监听
        # Agent._last_permission_request 的变化

        await self._perm_request_queue.put(request)

        # 等待 TUI 响应
        response = await self.permission_response_queue.get()

        if response.decision == "deny":
            return ToolResult(
                success=False,
                error="[用户拒绝] 操作已被用户拒绝。",
            )
        elif response.decision == "allow_all":
            self._permission.allow_all_this_round()

        # allow_once 或 allow_all → 执行
        return await self._executor._execute_safe(tool_call)

    @staticmethod
    def _make_summary(tool_call: ProviderToolCall) -> str:
        """生成工具调用的单行摘要。"""
        name = tool_call.name
        inp = tool_call.input
        if name == "bash":
            cmd = inp.get("command", "")
            return f"{name}: {cmd[:60]}"
        elif name in ("write_file", "edit_file", "read_file"):
            fp = inp.get("file_path", "")
            return f"{name}: {fp}"
        elif name in ("glob", "grep"):
            pat = inp.get("pattern", "")
            return f"{name}: {pat}"
        return f"{name}"

    # ── 工具分组 ─────────────────────────────────────────────

    def _partition_tools(
        self,
        tool_calls: list[ProviderToolCall],
    ) -> tuple[list[ProviderToolCall], list[ProviderToolCall]]:
        """按 category 分组工具调用：读工具并发、写工具串行。"""
        reads, writes = [], []
        for tc in tool_calls:
            try:
                tool = self._executor.registry.get(tc.name)
                category = tool.category
            except KeyError:
                category = "write"  # 未知工具保守当写处理
            if category == "read":
                reads.append(tc)
            else:
                writes.append(tc)
        return reads, writes

    # ── Plan-only 工具拦截（保留 V4 兼容）───────────────────

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

        return await self._executor._execute_safe(tool_call)
