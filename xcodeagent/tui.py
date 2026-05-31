"""终端交互界面（TUI）：基于 Rich 的流式对话 UI，支持工具调用展示。"""

from __future__ import annotations

import asyncio
import sys

import pyfiglet
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from xcodeagent.chat import ChatSession
from xcodeagent.config import AppConfig
from xcodeagent.tools.base import ToolResult
from xcodeagent.tools.executor import ToolExecutor
from xcodeagent.tools.registry import ToolRegistry
from xcodeagent.provider.base import ProviderError, ToolCall


def _render_logo() -> str:
    """用 pyfiglet slant 字体渲染 XCodeAgent 文字。"""
    return pyfiglet.figlet_format("XCodeAgent", font="slant")


def _print_logo() -> None:
    """用 ANSI 光标叠加打印带右下角阴影的 3D 立体 Logo。"""
    logo = _render_logo()
    lines = logo.split("\n")

    while lines and not lines[-1].strip():
        lines.pop()

    n = len(lines)

    # 阴影层
    sys.stdout.write("\n")
    for line in lines:
        sys.stdout.write(f"\033[2m\033[90m {line}\033[0m\n")

    sys.stdout.write(f"\033[{n + 1}A")
    sys.stdout.flush()

    # 前景层
    sys.stdout.write("\n")
    for line in lines:
        sys.stdout.write(f"\033[1m\033[96m{line}\033[0m\n")
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── 状态指示器 ────────────────────────────────────────────

class StatusIndicator:
    """在终端独立行内显示状态文字。"""

    def __init__(self):
        self._active = False

    async def thinking(self) -> None:
        """循环动画：thinking. → thinking.. → thinking..."""
        self._active = True
        frames = [
            "\033[1A\r\033[K\033[90mthinking.\033[0m",
            "\033[1A\r\033[K\033[90mthinking..\033[0m",
            "\033[1A\r\033[K\033[90mthinking...\033[0m",
        ]
        i = 0
        while self._active:
            sys.stdout.write(f"\0337{frames[i % 3]}\0338")
            sys.stdout.flush()
            i += 1
            await asyncio.sleep(0.4)

    def stop(self) -> None:
        self._active = False

    def show_output(self) -> None:
        sys.stdout.write(f"\033[u\033[K\033[93moutput...\033[0m\n")
        sys.stdout.flush()

    def show_completed(self) -> None:
        sys.stdout.write(f"\0337\033[u\033[K\033[92mcompleted!\033[0m\0338")
        sys.stdout.flush()


# ── TUI ───────────────────────────────────────────────────

class TUI:
    """基于 Rich 的交互式终端对话界面。"""

    def __init__(self, chat_session: ChatSession, config: AppConfig, tool_executor: ToolExecutor):
        self._chat = chat_session
        self._config = config
        self._tool_executor = tool_executor
        self._console = Console()
        self._running = True

    async def run(self) -> None:
        """启动 TUI 主循环。"""
        self._print_welcome()

        while self._running:
            try:
                user_input = await self._get_input()
            except EOFError:
                self._console.print()
                break
            except KeyboardInterrupt:
                self._console.print("\n[yellow]收到中断信号，退出中...[/]")
                break

            if not user_input.strip():
                continue

            if user_input.startswith("/"):
                self._handle_command(user_input)
                continue

            await self._handle_chat(user_input)

        self._console.print("[dim]Goodbye![/]")

    # ── 输入区域 ──────────────────────────────────────

    async def _get_input(self) -> str:
        self._console.print(Rule(style="dim cyan"))
        self._console.print("[bold cyan]▸[/] ", end="")
        sys.stdout.flush()
        return await asyncio.to_thread(input, "")

    # ── 欢迎界面 ──────────────────────────────────────

    def _print_welcome(self) -> None:
        _print_logo()

        info = Text()
        info.append("v0.2.0", style="dim")
        info.append("  |  ", style="dim")
        info.append(self._config.model, style="cyan italic")
        info.append("  |  ", style="dim")
        info.append(self._config.protocol, style="blue italic")

        self._console.print(info)
        self._console.print()
        self._console.print(
            "[dim]直接输入消息开始对话  |  /help 查看命令  |  /exit 退出[/]\n"
        )

    # ── 聊天（含工具调用）──────────────────────────────

    async def _handle_chat(self, user_input: str) -> None:
        """发送用户消息并流式显示 AI 回复（支持工具调用）。"""

        # 清掉输入区域，显示用户消息
        sys.stdout.write("\033[2A\033[J")
        sys.stdout.flush()
        self._console.print(f"[bold bright_black]▸[/] [bright_black]{user_input}[/]")

        # 状态行
        self._console.print()
        sys.stdout.write("\033[1A\033[s\033[1B")
        sys.stdout.flush()
        indicator = StatusIndicator()

        # thinking 动画
        thinking_task = asyncio.create_task(indicator.thinking())

        tool_registry = self._tool_executor.registry
        stream = self._chat.send_with_tools(user_input, self._tool_executor, tool_registry)
        first_item = None

        try:
            first_item = await stream.__anext__()
        except StopAsyncIteration:
            first_item = None
        except (KeyboardInterrupt, ProviderError):
            indicator.stop()
            thinking_task.cancel()
            try:
                await thinking_task
            except asyncio.CancelledError:
                pass
            self._console.print("\n[yellow]已中断[/]")
            self._console.print(Rule(style="dim grey30"))
            self._console.print()
            sys.stdout.flush()
            return

        # 切换到 output 状态
        indicator.stop()
        try:
            await thinking_task
        except asyncio.CancelledError:
            pass

        indicator.show_output()

        # ── 处理流式事件 ──
        self._console.print("[bold cyan]│[/] ", end="")

        tool_calls_seen = False

        try:
            # 先处理第一个 item
            await self._process_stream_item(first_item, tool_calls_seen)
            if isinstance(first_item, ToolCall):
                tool_calls_seen = True

            async for item in stream:
                await self._process_stream_item(item, tool_calls_seen)
                if isinstance(item, ToolCall):
                    tool_calls_seen = True

        except KeyboardInterrupt:
            self._console.print("\n[yellow]已中断当前回复[/]")
            self._console.print(Rule(style="dim grey30"))
            self._console.print()
            sys.stdout.flush()
            return
        except ProviderError as e:
            self._console.print(f"\n[red]API 错误: {e}[/]")
            self._console.print(Rule(style="dim grey30"))
            self._console.print()
            sys.stdout.flush()
            return

        self._console.print()
        indicator.show_completed()
        self._console.print(Rule(style="dim grey30"))
        self._console.print()
        sys.stdout.flush()

    async def _process_stream_item(self, item, tool_calls_seen: bool) -> None:
        """处理流中的一个 item：文本 token / ToolCall / ToolResult。"""
        if isinstance(item, str):
            self._console.print(item, end="")
        elif isinstance(item, ToolCall):
            # 换行后再显示工具调用状态，与流式文本分行
            if tool_calls_seen:
                self._console.print()
            input_preview = ", ".join(f"{k}={v}" for k, v in item.input.items())
            if len(input_preview) > 60:
                input_preview = input_preview[:57] + "..."
            self._console.print(
                f"\n[bold cyan]  ⚡ calling {item.name}[/] [dim]({input_preview})[/]"
            )
        elif isinstance(item, ToolResult):
            if item.success:
                # 取输出第一行作为摘要
                first_line = item.output.split("\n")[0] if item.output else "(empty)"
                if len(first_line) > 80:
                    first_line = first_line[:77] + "..."
                self._console.print(
                    f"[bold green]  ✅ {first_line}[/]"
                )
            else:
                self._console.print(
                    f"[bold red]  ❌ {item.error}[/]"
                )

    # ── 命令处理 ──────────────────────────────────────

    def _handle_command(self, user_input: str) -> None:
        cmd = user_input.lower()

        if cmd in ("/exit", "/quit"):
            self._running = False
        elif cmd == "/clear":
            self._chat.clear()
            self._console.print(Rule("对话已清空", style="dim"))
            self._console.print()
        elif cmd == "/help":
            self._console.print()
            self._console.print("可用命令：", style="bold")
            self._console.print("  /exit, /quit    退出程序")
            self._console.print("  /clear          清空对话历史")
            self._console.print("  /help           显示本帮助")
            self._console.print()
            self._console.print("工具调用：", style="bold")
            self._console.print("  在提问中描述任务，AI 会自动调用工具（读取文件、搜索、执行命令等）。")
            self._console.print()
        else:
            self._console.print(f"[red]未知命令: {user_input}[/]")
