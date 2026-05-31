"""WriteFile 工具：创建新文件或完整覆盖已有文件。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xcodeagent.tools.base import BaseTool, ToolResult


class WriteFile(BaseTool):
    """创建新文件或完整覆盖已有文件。不追加，不部分修改。

    适合：创建新文件、整体替换文件内容。
    不适合：局部修改已有文件 → 用 EditFile；追加内容 → 用 Bash。
    """

    name = "write_file"
    category = "write"
    description = (
        "创建新文件或完整覆盖已有文件。写入的是文件完整内容，不是追加。"
        "何时使用：创建新文件（如 __init__.py、新模块）；需要整体替换文件内容时。"
        "何时不用：局部修改现有文件 → 用 EditFile；仅追加内容 → 用 Bash '>>'。"
        "参数约束：file_path 必须是绝对路径，在项目根目录内；content 是完整文件内容。"
        "返回格式：成功时返回 'Successfully wrote N bytes to <path>'。"
        "协作建议：新建模块前先 ReadFile 参考同级文件风格；写完后 Bash 验证语法。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件的绝对路径，必须在项目根目录内",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的文件完整内容（不是追加，是覆盖）",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, file_path: str, content: str) -> ToolResult:
        try:
            target = self._validate_path(file_path)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        if target.exists() and not target.is_file():
            return ToolResult(success=False, error=f"路径已存在但不是文件: {target}")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, content, encoding="utf-8")
        except OSError as e:
            return ToolResult(success=False, error=str(e))

        size = len(content.encode("utf-8"))
        return ToolResult(success=True, output=f"Successfully wrote {size} bytes to {target}")
