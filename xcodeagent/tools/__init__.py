"""工具子系统：注册中心 + 执行引擎 + 八个工具。"""

from __future__ import annotations

from pathlib import Path

from xcodeagent.tools.base import BaseTool, ToolResult
from xcodeagent.tools.registry import ToolRegistry
from xcodeagent.tools.executor import ToolExecutor
from xcodeagent.tools.read_file import ReadFile
from xcodeagent.tools.write_file import WriteFile
from xcodeagent.tools.edit_file import EditFile
from xcodeagent.tools.bash_ import Bash
from xcodeagent.tools.glob_ import Glob
from xcodeagent.tools.grep_ import Grep
from xcodeagent.tools.web_search import WebSearch
from xcodeagent.tools.web_fetch import WebFetch


def create_tool_executor(
    project_root: str | Path,
    default_timeout: float = 30.0,
    bash_timeout: float = 120.0,
    mcp_tools: list[BaseTool] | None = None,
) -> ToolExecutor:
    """创建预设所有内置工具和可选 MCP 工具的 ToolExecutor。

    Args:
        project_root: 项目根目录（所有路径操作的边界）。
        default_timeout: 默认超时秒数（除 Bash 外的工具）。
        bash_timeout: Bash 工具的默认超时秒数。
        mcp_tools: 可选，从 MCP Server 发现的工具列表。

    Returns:
        配置好的 ToolExecutor 实例。
    """
    registry = ToolRegistry()

    root = Path(project_root).resolve()
    registry.register(ReadFile(root))
    registry.register(WriteFile(root))
    registry.register(EditFile(root))
    registry.register(Bash(root))
    registry.register(Glob(root))
    registry.register(Grep(root))
    registry.register(WebSearch(root))
    registry.register(WebFetch(root))

    # 注册 MCP 工具
    if mcp_tools:
        for tool in mcp_tools:
            try:
                registry.register(tool)
            except ValueError as e:
                import sys
                print(f"[WARNING] MCP 工具冲突: {e}", file=sys.stderr)

    return ToolExecutor(registry, default_timeout=default_timeout)


__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolRegistry",
    "ToolExecutor",
    "ReadFile",
    "WriteFile",
    "EditFile",
    "Bash",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "create_tool_executor",
]
