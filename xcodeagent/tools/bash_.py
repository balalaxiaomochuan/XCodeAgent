"""Bash 工具：在项目根目录执行 shell 命令。"""

from __future__ import annotations

import asyncio
import os

from xcodeagent.tools.base import BaseTool, ToolResult


class Bash(BaseTool):
    """在项目根目录执行 shell 命令，返回 stdout/stderr 和退出码。

    适合：运行测试、安装依赖、git 操作、构建命令。
    不适合：读文件 → 用 ReadFile；写文件 → 用 WriteFile；搜索 → 用 Glob/Grep。
    """

    name = "bash"
    description = (
        "在项目根目录执行 shell 命令，返回 stdout、stderr 和退出码。"
        "何时使用：运行测试 (pytest)、安装依赖 (pip install)、git 操作 (git status/diff/log)、构建命令。"
        "何时不用：读文件 → 用 ReadFile；写文件 → 用 WriteFile；搜索文件/内容 → 用 Glob/Grep。这些专用工具更高效且结果结构化。"
        "参数约束：command 是完整的 shell 命令字符串；timeout 单位毫秒，默认 120000（2 分钟）。"
        "返回格式：stdout + stderr + exit_code，exit_code=0 才算成功。"
        "输出超过 10000 字符会截断。"
        "协作建议：EditFile/WriteFile 后 Bash 验证语法/跑测试；命令超时后自动终止子进程。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令字符串",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时毫秒（默认 120000，即 2 分钟）",
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, timeout: int = 120000) -> ToolResult:
        timeout_sec = timeout / 1000.0

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._project_root),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            return ToolResult(
                success=False,
                error=f"命令执行超时 ({timeout_sec:.0f}s)，已终止进程",
            )
        except OSError as e:
            return ToolResult(success=False, error=str(e))

        stdout = self._truncate(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = self._truncate(stderr_bytes.decode("utf-8", errors="replace"))

        output_parts = []
        if stdout.strip():
            output_parts.append(f"stdout:\n{stdout}")
        if stderr.strip():
            output_parts.append(f"stderr:\n{stderr}")
        output_parts.append(f"exit_code: {proc.returncode}")
        output = "\n".join(output_parts)

        return ToolResult(
            success=proc.returncode == 0,
            output=output,
            error=None if proc.returncode == 0 else f"命令退出码: {proc.returncode}",
        )

    @staticmethod
    def _truncate(text: str, max_chars: int = 10000) -> str:
        if len(text) > max_chars:
            return text[:max_chars] + f"\n... (截断，共 {len(text)} 字符)"
        return text
