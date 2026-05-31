"""终端交互界面（TUI）：基于 Rich 的流式对话 UI。"""

from __future__ import annotations

import asyncio
import sys

import pyfiglet
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from xcodeagent.chat import ChatSession
from xcodeagent.config import AppConfig
from xcodeagent.provider.base import ProviderError


def _render_logo() -> str:
    """用 pyfiglet slant 字体渲染 XCodeAgent 文字。"""
    return pyfiglet.figlet_format("XCodeAgent", font="slant")


def _print_logo() -> None:
    """用 ANSI 光标叠加打印带右下角阴影的 3D 立体 Logo。

    先打偏移暗色阴影 → 光标回退 → 覆盖亮色前景。
    前景的空格位会透出下方阴影，形成立体感。
    """
    logo = _render_logo()
    lines = logo.split("\n")

    # 去掉末尾空行
    while lines and not lines[-1].strip():
        lines.pop()

    n = len(lines)

    # ── 阴影层：暗灰色，向右下偏移 1 行 1 列 ──
    sys.stdout.write("\n")  # 垂直偏移
    for line in lines:
        sys.stdout.write(f"\033[2m\033[90m {line}\033[0m\n")

    # 回退光标
    sys.stdout.write(f"\033[{n + 1}A")
    sys.stdout.flush()

    # ── 前景层：亮青色粗体 ──
    sys.stdout.write("\n")
    for line in lines:
        sys.stdout.write(f"\033[1m\033[96m{line}\033[0m\n")
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── 状态指示器 ────────────────────────────────────────────

class StatusIndicator:
    """在终端行内显示异步状态文字（thinking... / output... / completed!）。"""

    def __init__(self):
        self._active = False

    async def thinking(self) -> None:
        """循环动画：thinking. → thinking.. → thinking..."""
        self._active = True
        frames = [
            "\033[90mthinking.\033[0m  ",
            "\033[90mthinking..\033[0m ",
            "\033[90mthinking...\033[0m",
        ]
        i = 0
        while self._active:
            sys.stdout.write(f"\r\033[K{frames[i % 3]}")
            sys.stdout.flush()
            i += 1
            await asyncio.sleep(0.4)
        # 清除状态行
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def stop(self) -> None:
        self._active = False

    def show_output(self) -> None:
        sys.stdout.write("\r\033[K\033[93moutput...\033[0m ")
        sys.stdout.flush()

    def show_completed(self) -> None:
        sys.stdout.write("\r\033[K\033[92mcompleted!\033[0m")
        sys.stdout.flush()


# ── TUI ───────────────────────────────────────────────────

class TUI:
    """基于 Rich 的交互式终端对话界面。"""

    def __init__(self, chat_session: ChatSession, config: AppConfig):
        self._chat = chat_session
        self._config = config
        self._console = Console()
        self._running = True

    async def run(self) -> None:
        """启动 TUI 主循环。"""
        self._print_welcome()

        while self._running:
            try:
                user_input = await asyncio.to_thread(input, "")
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

    # ── 欢迎界面 ──────────────────────────────────────

    def _print_welcome(self) -> None:
        """打印 3D logo 和基础信息。"""
        _print_logo()

        info = Text()
        info.append("v0.1.0", style="dim")
        info.append("  |  ", style="dim")
        info.append(self._config.model, style="cyan italic")
        info.append("  |  ", style="dim")
        info.append(self._config.protocol, style="blue italic")

        self._console.print(info)
        self._console.print()
        self._console.print(
            "[dim]直接输入消息开始对话  |  /help 查看命令  |  /exit 退出[/]\n"
        )

    # ── 聊天 ──────────────────────────────────────────

    async def _handle_chat(self, user_input: str) -> None:
        """发送用户消息并流式显示 AI 回复。"""

        # ── 替换输入行为带背景的 Panel（消除重复显示）──
        user_panel = Panel(
            Text(user_input),
            box=box.ROUNDED,
            border_style="bright_black",
            style="on grey15",
            padding=(0, 1),
        )
        # 光标上移一行，用 Panel 覆盖掉纯文本输入行
        sys.stdout.write("\033[1A\033[2K")
        sys.stdout.flush()
        self._console.print(user_panel)

        # ── 状态指示器 ──
        indicator = StatusIndicator()
        thinking_task = asyncio.create_task(indicator.thinking())

        stream = self._chat.send(user_input)
        first_token = None

        try:
            # 等待第一个 token
            first_token = await stream.__anext__()
        except StopAsyncIteration:
            first_token = None
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
            return

        # 收到第一个 token，切换为 output 状态
        indicator.stop()
        try:
            await thinking_task
        except asyncio.CancelledError:
            pass

        self._console.print()
        indicator.show_output()
        self._console.print("[bold cyan]│[/] ", end="")

        try:
            if first_token:
                self._console.print(first_token, end="")
            async for token in stream:
                self._console.print(token, end="")
            self._console.print()
        except KeyboardInterrupt:
            self._console.print("\n[yellow]已中断当前回复[/]")
            self._console.print(Rule(style="dim grey30"))
            self._console.print()
            return
        except ProviderError as e:
            self._console.print(f"\n[red]API 错误: {e}[/]")
            self._console.print(Rule(style="dim grey30"))
            self._console.print()
            return

        # 完成
        indicator.show_completed()
        self._console.print()
        self._console.print(Rule(style="dim grey30"))
        self._console.print()

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
        else:
            self._console.print(f"[red]未知命令: {user_input}[/]")
