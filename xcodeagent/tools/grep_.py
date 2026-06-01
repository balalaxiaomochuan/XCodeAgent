
"""Grep 工具：用正则表达式搜索文件内容，返回所有匹配行及其位置。"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from xcodeagent.tools.base import BaseTool, ToolResult

_EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs"}
_BINARY_EXTENSIONS = {".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".zip", ".tar", ".gz", ".png", ".jpg", ".mp3", ".mp4", ".pdf", ".ico"}


class Grep(BaseTool):
    """用正则搜索文件内容，返回匹配行及位置。

    适合：查找函数定义、追踪变量引用、搜索错误信息、找到需修改的代码位置。
    不适合：按文件名找文件 → 用 Glob；读完整文件 → 用 ReadFile。
    """

    name = "grep"
    description = (
        "用正则表达式搜索文件内容，返回所有匹配行及其文件位置。"
        "何时使用：查找函数/类定义、追踪变量引用、搜索错误信息/日志关键词、找到需要修改的代码位置。"
        "何时不用：按文件名找文件 → 用 Glob；读取完整文件内容 → 用 ReadFile。"
        "参数约束：pattern 是正则表达式，特殊字符需转义（如 function\\s+\\w+）；path 限制搜索目录；glob 过滤文件名。"
        "返回格式：'{文件路径}:{行号}: {匹配行内容}'，最多返回 250 条。"
        "自动排除 .git、__pycache__ 等目录和二进制文件。"
        "协作建议：定位函数定义 Grep 'def func_name' → ReadFile；编辑前 Grep 确认影响范围 → EditFile。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "正则表达式，特殊字符需转义（如 'function\\s+\\w+'）",
                },
                "path": {
                    "type": "string",
                    "description": "搜索路径（默认项目根目录）",
                },
                "glob": {
                    "type": "string",
                    "description": "文件名过滤 glob，如 '*.py'、'*.{ts,tsx}'",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, path: str = "", glob: str = "") -> ToolResult:
        root = self._project_root
        if path:
            try:
                root = self._validate_path(path)
            except ValueError as e:
                return ToolResult(success=False, error=str(e))

        if not root.exists():
            return ToolResult(success=False, error=f"目录不存在: {root}")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(success=False, error=f"正则表达式无效: {e}")

        try:
            matches = await asyncio.to_thread(self._grep, root, regex, glob)
        except OSError as e:
            return ToolResult(success=False, error=str(e))

        if not matches:
            return ToolResult(
                success=True,
                output=f"No matches for '{pattern}'",
            )

        output = f"Found {len(matches)} match(es) for '{pattern}'"
        if glob:
            output += f" in {glob}"
        output += ":\n"
        output += "\n".join(matches)
        return ToolResult(success=True, output=output)

    @staticmethod
    def _grep(root: Path, regex: re.Pattern, glob_filter: str) -> list[str]:
        import fnmatch

        results: list[str] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDE_DIRS)

            for fname in filenames:
                full = Path(dirpath) / fname

                # glob 过滤
                if glob_filter and not fnmatch.fnmatch(fname, glob_filter):
                    continue

                # 跳过二进制文件
                if full.suffix.lower() in _BINARY_EXTENSIONS:
                    continue

                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        for line_no, line in enumerate(f, 1):
                            if len(results) >= 250:
                                results.append("... (截断，最多显示 250 条匹配)")
                                return results
                            if regex.search(line):
                                rel = full.relative_to(root)
                                results.append(f"  {rel}:{line_no}: {line.rstrip()}")
                except (OSError, UnicodeDecodeError):
                    continue

        return results
