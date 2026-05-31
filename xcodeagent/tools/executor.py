"""工具执行引擎：超时控制 + 统一错误处理。"""

from __future__ import annotations

import asyncio
import time

from xcodeagent.tools.base import ToolResult
from xcodeagent.tools.registry import ToolRegistry


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
