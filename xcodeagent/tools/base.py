"""工具接口层：BaseTool 抽象基类 + ToolResult。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolResult:
    """工具执行结果。"""

    success: bool
    output: str = ""
    error: str | None = None
    execution_time: float | None = None


class BaseTool(ABC):
    """所有工具的抽象基类。

    子类必须定义：
        name: str          — 工具唯一标识（如 "read_file"）
        description: str   — 工具功能描述（用于 LLM 上下文）
        parameters: dict   — 参数的 JSON Schema（作为 input_schema 传给模型）
    """

    name: str = ""
    description: str = ""

    def __init__(self, project_root: Path):
        self._project_root = project_root.resolve()

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """返回输入参数的 JSON Schema（不含外层的 type/name）。

        返回的是 input_schema 的值：
            {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        """
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具，返回标准化结果。"""
        ...

    @property
    def project_root(self) -> Path:
        """项目根目录（所有路径操作的边界）。"""
        return self._project_root

    def _validate_path(self, file_path: str | Path) -> Path:
        """校验并规范化文件路径，确保在项目根目录内。

        Returns:
            规范化后的绝对 Path。

        Raises:
            ValueError: 路径不在项目根目录内。
        """
        p = Path(file_path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self._project_root / p).resolve()

        try:
            resolved.relative_to(self._project_root)
        except ValueError:
            raise ValueError(
                f"路径不在项目根目录内: {file_path}（根目录: {self._project_root}）"
            )
        return resolved
