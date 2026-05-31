"""工具子系统：注册中心 + 执行引擎 + 六个工具。"""

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


def create_tool_executor(
    project_root: str | Path,
    default_timeout: float = 30.0,
    bash_timeout: float = 120.0,
) -> ToolExecutor:
    """创建预设所有 6 个工具的 ToolExecutor。

    Args:
        project_root: 项目根目录（所有路径操作的边界）。
        default_timeout: 默认超时秒数（除 Bash 外的工具）。
        bash_timeout: Bash 工具的默认超时秒数。

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
    "create_tool_executor",
]
