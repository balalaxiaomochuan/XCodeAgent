"""终端交互界面（TUI）：基于 Rich 的流式对话 UI，消费 Agent 事件流。"""

from __future__ import annotations

import asyncio
import sys

import pyfiglet
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from xcodeagent.agent import Agent
from xcodeagent.chat import ChatSession
from xcodeagent.config import AppConfig
from xcodeagent.events import (
    AgentError,
    AgentEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    UserMessage,
)
from xcodeagent.provider.base import ProviderError


# ── Logo ──────────────────────────────────────────────────────


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

    sys.stdout.write("\n")
    for line in lines:
        sys.stdout.write(f"\033[2m\033[90m {line}\033[0m\n")

    sys.stdout.write(f"\033[{n + 1}A")
    sys.stdout.flush()

    sys.stdout.write("\n")
    for line in lines:
        sys.stdout.write(f"\033[1m\033[96m{line}\033[0m\n")
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── Esc 键监听器 ──────────────────────────────────────────────


async def _listen_for_esc(cancel_token: asyncio.Event) -> None:
    """后台监听 Esc 键，按下即设置 cancel_token。"""
    if sys.platform == "win32":
        import msvcrt
        while not cancel_token.is_set():
            await asyncio.sleep(0.1)
            try:
                if msvcrt.kbhit() and msvcrt.getch() == b'\x1b':
                    cancel_token.set()
                    break
            except (OSError, ValueError):
                break
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not cancel_token.is_set():
                try:
                    ch = await asyncio.to_thread(sys.stdin.read, 1)
                    if ch == '\x1b':
                        cancel_token.set()
                        break
                except (OSError, EOFError):
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


# ── TUI ───────────────────────────────────────────────────────


class TUI:
    """基于 Rich 的交互式终端对话界面，消费 Agent 事件流。"""

    def __init__(
        self,
        agent: Agent,
        chat_session: ChatSession,
        config: AppConfig,
    ):
        self._agent = agent
        self._chat = chat_session
        self._config = config
        self._console = Console()
        self._running = True
        self._first_text = True   # 是否本轮第一个 TextDelta
        self._in_thinking = False  # 是否正在渲染 thinking 内容

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

    # ── 输入区域 ──────────────────────────────────────────

    async def _get_input(self) -> str:
        mode_label = "[yellow]PLAN-ONLY[/] " if self._agent._config.plan_only else ""
        self._console.print(Rule(style="dim cyan"))
        self._console.print(f"{mode_label}[bold cyan]▸[/] ", end="")
        sys.stdout.flush()
        return await asyncio.to_thread(input, "")

    # ── 欢迎界面 ──────────────────────────────────────────

    def _print_welcome(self) -> None:
        _print_logo()

        info = Text()
        info.append("v0.3.0", style="dim")
        info.append("  |  ", style="dim")
        info.append(self._config.model, style="cyan italic")
        info.append("  |  ", style="dim")
        info.append(self._config.protocol, style="blue italic")
        info.append("  |  ", style="dim")
        info.append("ReAct Agent", style="green")

        self._console.print(info)
        self._console.print()
        self._console.print(
            "[dim]直接输入消息开始对话  |  Esc 取消  |  /help 查看命令  |  /exit 退出[/]\n"
        )

    # ── 聊天（Agent 事件流）────────────────────────────────

    async def _handle_chat(self, user_input: str) -> None:
        """消费 Agent 事件流并渲染。"""

        # 清掉输入区域
        sys.stdout.write("\033[2A\033[J")
        sys.stdout.flush()
        self._console.print(f"[bold bright_black]▸[/] [bright_black]{user_input}[/]")
        self._console.print()

        self._first_text = True
        self._turn_has_output = False  # 当前轮是否有过文本或工具输出

        cancel_token = asyncio.Event()
        esc_task = asyncio.create_task(_listen_for_esc(cancel_token))

        try:
            self._console.print("[dim]  thinking...[/]")
            async for event in self._agent.run(user_input, cancel_token):
                self._dispatch(event)
        except KeyboardInterrupt:
            cancel_token.set()
            self._console.print("\n[yellow]已中断当前回复[/]")
        except ProviderError as e:
            self._console.print(f"\n[red]API 错误: {e}[/]")
        except Exception as e:
            self._console.print(f"\n[red]Agent 错误: {e}[/]")
        finally:
            esc_task.cancel()
            try:
                await esc_task
            except asyncio.CancelledError:
                pass

        self._console.print(Rule(style="dim grey30"))
        self._console.print()

    # ── 事件分发 ──────────────────────────────────────────

    def _dispatch(self, event: AgentEvent) -> None:
        """匹配事件类型并分发到对应渲染方法。"""
        if isinstance(event, UserMessage):
            pass  # 已在 _handle_chat 中渲染
        elif isinstance(event, ThinkingDelta):
            if not self._config.show_thinking:
                return  # 配置关闭思考展示
            self._turn_has_output = True
            self._render_thinking(event.text)
        elif isinstance(event, TextDelta):
            self._end_thinking()  # 结束 thinking 行，切换到文本模式
            self._turn_has_output = True
            self._render_text(event.text)
        elif isinstance(event, ToolCallStart):
            self._end_thinking()
            self._turn_has_output = True
            self._render_tool_start(event.tool_name, event.tool_input)
        elif isinstance(event, ToolCallEnd):
            self._render_tool_end(event.tool_name, event.success, event.output, event.error)
            self._first_text = True
        elif isinstance(event, TurnEnd):
            self._end_thinking()
            if event.reason == "cancelled":
                self._console.print("\n[yellow][已取消][/]")
            elif event.reason == "max_rounds":
                self._console.print(f"\n[yellow][已达最大轮数 {event.round_number}][/]")
            elif event.reason == "continue":
                self._console.print("[dim]  thinking...[/]")
        elif isinstance(event, AgentError):
            self._console.print(f"\n[red]⚠ {event.message}[/]")

    # ── 事件渲染方法 ──────────────────────────────────────

    def _end_thinking(self) -> None:
        """结束 thinking 模式，换行以便后续内容另起一行。"""
        if self._in_thinking:
            self._console.print()  # 结束 thinking 行
            self._in_thinking = False

    def _render_thinking(self, text: str) -> None:
        """thinking 增量：灰色文字流式追加。"""
        if not self._in_thinking:
            self._console.print()  # 另起一行
            self._console.print("[dim]💭 ", end="")
            self._in_thinking = True
        self._console.print(text, end="", style="dim")

    def _render_text(self, text: str) -> None:
        """文本增量：正常颜色流式追加。"""
        if self._first_text:
            self._first_text = False
            self._console.print("[bold cyan]│[/] ", end="")
        self._console.print(text, end="")

    def _render_tool_start(self, name: str, tool_input: dict) -> None:
        """工具调用开始：工具名 + 参数摘要。"""
        input_preview = ", ".join(f"{k}={v}" for k, v in tool_input.items())
        if len(input_preview) > 60:
            input_preview = input_preview[:57] + "..."
        self._console.print(f"\n[bold cyan]  ⚡ {name}[/] [dim]({input_preview})[/]")

    def _render_tool_end(
        self, name: str, success: bool, output: str, error: str | None
    ) -> None:
        """工具调用结束：成功/失败状态 + 结果摘要。"""
        if success:
            first_line = output.split("\n")[0] if output else "(empty)"
            if len(first_line) > 80:
                first_line = first_line[:77] + "..."
            self._console.print(f"[bold green]  ✅ {first_line}[/]")
        else:
            err = error or "unknown"
            if len(err) > 80:
                err = err[:77] + "..."
            self._console.print(f"[bold red]  ❌ {err}[/]")

    # ── 命令处理 ──────────────────────────────────────────

    def _handle_command(self, user_input: str) -> None:
        cmd = user_input.lower().strip()

        if cmd in ("/exit", "/quit"):
            self._running = False
        elif cmd == "/clear":
            self._chat.clear()
            self._console.print(Rule("对话已清空", style="dim"))
            self._console.print()
        elif cmd == "/help":
            self._console.print()
            self._console.print("可用命令：", style="bold")
            self._console.print("  /exit, /quit      退出程序")
            self._console.print("  /clear            清空对话历史")
            self._console.print("  /plan-on          进入计划模式（只读，不执行写操作）")
            self._console.print("  /plan-off         退出计划模式")
            self._console.print("  /thinking-on      展示模型思考过程")
            self._console.print("  /thinking-off     隐藏模型思考过程")
            self._console.print("  /help             显示本帮助")
            self._console.print()
            self._console.print("快捷键：", style="bold")
            self._console.print("  Esc               取消当前 Agent 循环")
            self._console.print("  Ctrl+C            中断当前回复（兜底）")
            self._console.print("  Ctrl+D / EOF      退出程序")
            self._console.print()
        elif cmd == "/plan-on":
            self._agent._config.plan_only = True
            self._console.print("[yellow]已进入 Plan-only 模式：只允许读操作，写操作将被拦截。[/]")
            self._console.print("[dim]使用 /plan-off 恢复正常模式。[/]")
            self._console.print()
        elif cmd == "/plan-off":
            self._agent._config.plan_only = False
            self._console.print("[green]已退出 Plan-only 模式，所有工具恢复正常执行。[/]")
            self._console.print()
        elif cmd == "/thinking-on":
            self._config.show_thinking = True
            self._console.print("[green]已开启思考过程展示。[/]")
            self._console.print()
        elif cmd == "/thinking-off":
            self._config.show_thinking = False
            self._console.print("[yellow]已关闭思考过程展示。[/]")
            self._console.print()
        else:
            self._console.print(f"[red]未知命令: {user_input}[/]")
