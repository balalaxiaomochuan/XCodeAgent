"""ReadFile 工具：读取文件内容，返回带行号前缀的文本。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xcodeagent.tools.base import BaseTool, ToolResult


class ReadFile(BaseTool):
    """读取文件内容，返回带行号的文本。

    适合：查看文件内容、理解代码结构、编辑前确认。
    不适合：大文件（>500 行）先 Grep 定位行号再定点读取；二进制文件。
    """

    name = "read_file"
    description = (
        "读取指定路径的文件内容，返回带行号前缀的完整文本。"
        "何时使用：需要查看文件内容、理解代码结构、编辑前确认现有代码。"
        "何时不用：大文件（超 500 行）先用 Grep 定位行号再定点读取；二进制文件不要读。"
        "参数约束：file_path 必须是绝对路径，在项目根目录内；limit 最大 2000 行。"
        "返回格式：每行以 6 位右对齐行号 + ' | ' 前缀，如 '     1  | import os'。"
        "协作建议：大文件场景用 Grep 定位行号 → ReadFile(offset=行号)；编辑前必读确保 old_string 精确匹配。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件绝对路径，必须在项目根目录内",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始行号，从 1 开始（默认 1）",
                },
                "limit": {
                    "type": "integer",
                    "description": "最大读取行数（默认 500，上限 2000）",
                },
            },
            "required": ["file_path"],
        }

    async def execute(
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 500,
    ) -> ToolResult:
        try:
            target = self._validate_path(file_path)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        if limit > 2000:
            limit = 2000
        if offset < 1:
            offset = 1

        try:
            lines = await asyncio.to_thread(self._read_lines, target, offset, limit)
        except UnicodeDecodeError:
            return ToolResult(success=False, error="无法以文本方式读取，可能是二进制文件")
        except OSError as e:
            return ToolResult(success=False, error=str(e))

        return ToolResult(success=True, output=lines)

    @staticmethod
    def _read_lines(target: Path, offset: int, limit: int) -> str:
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {target}")
        if not target.is_file():
            raise IsADirectoryError(f"路径不是文件: {target}")

        with open(target, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        total = len(all_lines)
        start = offset - 1
        end = start + limit
        selected = all_lines[start:end]

        output_lines = []
        for i, line in enumerate(selected, start=offset):
            output_lines.append(f"{i:>6}  | {line.rstrip()}")

        result = "\n".join(output_lines)
        if end < total:
            truncated = total - end
            result += f"\n... (截断，剩余 {truncated} 行，使用 offset={end + 1} 继续读取)"

        return result
