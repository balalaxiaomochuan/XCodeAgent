"""EditFile 工具：单次调用对同一文件执行多段精确文本替换，整体回滚。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xcodeagent.tools.base import BaseTool, ToolResult


class EditFile(BaseTool):
    """多段精确文本替换，任一失败则整体回滚。

    old_string 必须严格唯一匹配（恰好 1 次）。
    0 次匹配 → 报错提示检查内容。
    >1 次匹配 → 报错提示添加更多上下文使其唯一。

    适合：修改代码片段、重构、修 bug。
    不适合：整体重写文件 → 用 WriteFile；新建文件 → 用 WriteFile。
    """

    name = "edit_file"
    category = "write"
    description = (
        "对同一文件执行多段精确文本替换，任一 old_string 匹配失败则整体回滚，文件原样不变。"
        "何时使用：修改代码片段、重构变量名、修复 bug、调整函数签名等局部修改。"
        "何时不用：整体重写文件 → 用 WriteFile；新建文件 → 用 WriteFile；不确定当前内容时先 ReadFile。"
        "参数约束：file_path 绝对路径；edits 数组按顺序执行，每个 old_string 必须严格唯一匹配（恰好 1 次）。"
        "匹配规则：0 次匹配 → 报错 '未找到匹配的字符串'；>1 次 → 报错 '该字符串出现了 N 次，请提供更多上下文使其唯一'。"
        "new_string 为空字符串时表示删除匹配内容。"
        "返回格式：成功时逐条列出每个片段的匹配和替换行数。"
        "协作建议：标准流程：ReadFile 确认内容 → EditFile 多段提交 → Bash 验证；"
        "old_string 不够唯一时增加前后 2-3 行上下文使其唯一。"
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
                "edits": {
                    "type": "array",
                    "description": "编辑片段列表，按数组顺序依次执行",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {
                                "type": "string",
                                "description": "要替换的原始文本（必须在文件中唯一匹配）",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "替换后的新文本，空字符串表示删除",
                            },
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
            },
            "required": ["file_path", "edits"],
        }

    async def execute(self, file_path: str, edits: list[dict]) -> ToolResult:
        try:
            target = self._validate_path(file_path)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        if not target.exists():
            return ToolResult(success=False, error=f"文件不存在: {target}")
        if not target.is_file():
            return ToolResult(success=False, error=f"路径不是文件: {target}")

        try:
            original = await asyncio.to_thread(target.read_text, encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(success=False, error="无法以文本方式读取，可能是二进制文件")
        except OSError as e:
            return ToolResult(success=False, error=str(e))

        current = original
        summaries: list[str] = []

        for i, edit in enumerate(edits):
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")

            count = current.count(old_string)
            if count == 0:
                return ToolResult(
                    success=False,
                    error=(
                        f"编辑 {i + 1}/{len(edits)} 失败: "
                        f"未找到匹配的字符串。请用 ReadFile 确认文件当前内容。\n"
                        f"  old_string 前 80 字符: '{old_string[:80]}'"
                    ),
                )
            if count > 1:
                return ToolResult(
                    success=False,
                    error=(
                        f"编辑 {i + 1}/{len(edits)} 失败: "
                        f"该字符串出现了 {count} 次，请提供更多上下文使其唯一。"
                        f"（在 old_string 前后多加几行代码来限定唯一位置）"
                    ),
                )

            old_lines = old_string.count("\n") + (0 if old_string.endswith("\n") else 1)
            new_lines = new_string.count("\n") + (0 if new_string.endswith("\n") else 1)

            current = current.replace(old_string, new_string, 1)

            if new_string == "":
                summaries.append(
                    f"  Edit {i + 1}/{len(edits)}: matched and replaced {old_lines} line(s) → 0 lines (deleted)"
                )
            else:
                summaries.append(
                    f"  Edit {i + 1}/{len(edits)}: matched and replaced {old_lines} line(s) → {new_lines} line(s)"
                )

        try:
            await asyncio.to_thread(target.write_text, current, encoding="utf-8")
        except OSError as e:
            return ToolResult(success=False, error=str(e))

        output = "Edit applied successfully:\n" + "\n".join(summaries)
        return ToolResult(success=True, output=output)
