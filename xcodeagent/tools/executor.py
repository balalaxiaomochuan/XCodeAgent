"""工具执行引擎：超时控制 + 统一错误处理 + 分批并发执行。"""

from __future__ import annotations

import asyncio
import time

from xcodeagent.tools.base import ToolResult
from xcodeagent.tools.registry import ToolRegistry
from xcodeagent.provider.base import ToolCall


class ToolExecutor:
    """统一工具调用入口，封装超时和异常处理。"""

    def __init__(self, registry: ToolRegistry, default_timeout: float = 30.0):
        self._registry = registry
        self._default_timeout = default_timeout

    @property
    def registry(self) -> ToolRegistry:
        """工具注册中心（用于获取 tool definitions）。"""
        return self._registry

    async def call(
        self,
        tool_name: str,
        params: dict,
        timeout: float | None = None,
    ) -> ToolResult:
        """执行指定工具。

        直接 await tool.execute()，由 asyncio.wait_for 控制超时。
        异常绝不传播到调用方，统一包装为 ToolResult(success=False)。

        Args:
            tool_name: 工具名称。
            params: 工具参数字典。
            timeout: 超时秒数，None 则用默认值。

        Returns:
            ToolResult，绝不抛异常。
        """
        try:
            tool = self._registry.get(tool_name)
        except KeyError as e:
            return ToolResult(success=False, error=str(e))

        effective_timeout = timeout if timeout is not None else self._default_timeout

        start = time.perf_counter()
        try:
            coro = tool.execute(**params)
            result = await asyncio.wait_for(coro, timeout=effective_timeout)
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - start
            return ToolResult(
                success=False,
                error=f"工具 '{tool_name}' 执行超时 ({effective_timeout:.0f}s)",
                execution_time=elapsed,
            )
        except Exception as e:
            elapsed = time.perf_counter() - start
            return ToolResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                execution_time=elapsed,
            )

        result.execution_time = time.perf_counter() - start
        return result

    # ── 分批执行（ReAct Agent 用）───────────────────────────

    async def execute_batch(
        self,
        tool_calls: list[ToolCall],
    ) -> dict[str, ToolResult]:
        """按 category 分批执行工具：读工具并发、写工具串行。

        部分失败不影响其他工具，所有结果（成功或失败）都返回。

        Args:
            tool_calls: 模型返回的工具调用列表。

        Returns:
            {tool_use_id: ToolResult} 字典，确保每个 tool_use_id 都有结果。
        """
        # 按 category 分组（保留原始顺序）
        reads: list[ToolCall] = []
        writes: list[ToolCall] = []
        for tc in tool_calls:
            try:
                tool = self._registry.get(tc.name)
            except KeyError:
                reads.append(tc)  # 未知工具当读处理，不会抛异常
                continue
            if tool.category == "write":
                writes.append(tc)
            else:
                reads.append(tc)

        all_results: dict[str, ToolResult] = {}

        # 读工具并发执行
        if reads:
            results = await asyncio.gather(*[
                self._execute_safe(tc) for tc in reads
            ])
            for tc, result in zip(reads, results):
                all_results[tc.id] = result

        # 写工具串行执行
        for tc in writes:
            all_results[tc.id] = await self._execute_safe(tc)

        return all_results

    async def _execute_safe(self, tool_call: ToolCall) -> ToolResult:
        """执行单个工具调用，异常包装为 ToolResult（绝不抛异常）。"""
        try:
            return await self.call(tool_call.name, tool_call.input)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
            )
