"""Glob 工具：按通配符模式搜索文件，返回匹配的文件路径列表。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from xcodeagent.tools.base import BaseTool, ToolResult

_EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs"}


class Glob(BaseTool):
    """按通配符模式搜索文件，返回匹配列表。

    适合：了解项目结构、找某类文件、定位配置文件。
    不适合：已知确切文件路径 → 直接用 ReadFile；搜索文件内容 → 用 Grep。
    """

    name = "glob"
    description = (
        "按通配符模式搜索文件，返回匹配的文件路径列表（按修改时间倒序）。"
        "何时使用：了解项目结构、找某个目录下所有 Python 文件、定位配置文件。"
        "何时不用：已知确切文件路径 → 直接用 ReadFile；搜索文件内容 → 用 Grep。"
        "参数约束：pattern 支持 ** 递归匹配；path 指定搜索根目录（默认项目根目录）。"
        "返回格式：文件路径列表，带修改日期，如 'src/main.py (2024-06-15 14:30)'。"
        "最多返回 500 条；自动排除 .git、__pycache__、node_modules、.venv 等目录。"
        "协作建议：探索项目结构先 Glob；批量修改前 Glob 定位目标文件集 → ReadFile → EditFile。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "通配符模式，支持 ** 递归匹配（如 '**/*.py'、'src/**/*.ts'）",
                },
                "path": {
                    "type": "string",
                    "description": "搜索根目录（默认项目根目录），必须是项目根目录内的路径",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, path: str = "") -> ToolResult:
        root = self._project_root
        if path:
            try:
                root = self._validate_path(path)
            except ValueError as e:
                return ToolResult(success=False, error=str(e))

        if not root.exists():
            return ToolResult(success=False, error=f"目录不存在: {root}")

        try:
            matches = await asyncio.to_thread(self._glob, root, pattern)
        except OSError as e:
            return ToolResult(success=False, error=str(e))

        if not matches:
            return ToolResult(
                success=True,
                output=f"No files matching '{pattern}' found in {root}",
            )

        output = f"Found {len(matches)} file(s) matching '{pattern}':\n"
        output += "\n".join(matches)
        return ToolResult(success=True, output=output)

    @staticmethod
    def _glob(root: Path, pattern: str) -> list[str]:
        results: list[tuple[float, str]] = []

        for full in root.rglob(pattern):
            if not full.is_file():
                continue
            # 排除目录
            if any(part in _EXCLUDE_DIRS for part in full.relative_to(root).parts):
                continue
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                mtime = 0.0
            rel = full.relative_to(root)
            results.append((mtime, str(rel)))

        # 按修改时间倒序
        results.sort(key=lambda x: x[0], reverse=True)

        formatted = []
        for i, (mtime, rel_path) in enumerate(results):
            if i >= 500:
                formatted.append(f"... (截断，共 {len(results)} 条，仅显示前 500)")
                break
            from datetime import datetime
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            formatted.append(f"  {rel_path} ({date_str})")

        return formatted
