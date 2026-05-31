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


# ── 转轮动画帧 ────────────────────────────────────────────────

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class WorkingSpinner:
    """统一的工作状态指示器：小转轮 + 'working...'。

    在后台协程中循环刷新终端上的同一行，不干扰正文输出。
    通过 \\0337/\\0338 保护正向光标位置，确保 Rich 内部的
    光标追踪不受干扰。
    """

    def __init__(self):
        self._active = False

    async def run(self) -> None:
        """启动转轮动画（阻塞直到 stop() 被调用）。"""
        self._active = True
        i = 0
        while self._active:
            frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
            # \\0337 保存当前正向光标 → \\033[u 回 spinner 行 → 写入 → \\0338 恢复
            sys.stdout.write(
                f"\0337\033[u\033[K  \033[90m{frame} working...\033[0m\0338"
            )
            sys.stdout.flush()
            i += 1
            await asyncio.sleep(0.1)

    def stop(self) -> None:
        """停止转轮并清除状态行。"""
        self._active = False
        sys.stdout.write("\0337\033[u\033[K\0338")
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
        self._spinner: WorkingSpinner | None = None
        self._spinner_task: asyncio.Task | None = None
        self._first_text = True  # 是否本轮第一个 TextDelta

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

        # 保存光标位置作为 spinner 行，然后下移一行留给正文
        sys.stdout.write("\033[s")  # 当前行 = spinner 行
        self._console.print()      # 下移，正文区域
        sys.stdout.flush()

        self._first_text = True

        # 启动转轮
        self._spinner = WorkingSpinner()
        self._spinner_task = asyncio.create_task(self._spinner.run())

        cancel_token = asyncio.Event()
        esc_task = asyncio.create_task(_listen_for_esc(cancel_token))

        try:
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
            self._stop_spinner()
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
            self._render_thinking(event.text)
        elif isinstance(event, TextDelta):
            self._render_text(event.text)
        elif isinstance(event, ToolCallStart):
            self._stop_spinner()
            self._render_tool_start(event.tool_name, event.tool_input)
        elif isinstance(event, ToolCallEnd):
            self._render_tool_end(event.tool_name, event.success, event.output, event.error)
            # 工具结果回来后重新启动 spinner（等待下一轮 LLM 响应）
            self._start_spinner()
        elif isinstance(event, TurnEnd):
            self._stop_spinner()
            if event.reason == "cancelled":
                self._console.print("\n[yellow][已取消][/]")
            elif event.reason == "max_rounds":
                self._console.print(f"\n[yellow][已达最大轮数 {event.round_number}][/]")
        elif isinstance(event, AgentError):
            self._console.print(f"\n[red]⚠ {event.message}[/]")

    # ── 事件渲染方法 ──────────────────────────────────────

    def _render_thinking(self, text: str) -> None:
        """thinking 增量：灰色文字流式追加。"""
        if self._first_text:
            self._first_text = False
            self._console.print("[bold cyan]│[/] ", end="")
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
        self._first_text = True

    # ── Spinner 控制 ──────────────────────────────────────

    def _start_spinner(self) -> None:
        """（重新）启动转轮动画。"""
        if self._spinner_task and not self._spinner_task.done():
            return
        self._spinner = WorkingSpinner()
        self._spinner_task = asyncio.create_task(self._spinner.run())

    def _stop_spinner(self) -> None:
        """停止转轮并清除状态行。"""
        if self._spinner:
            self._spinner.stop()
            self._spinner = None
        if self._spinner_task and not self._spinner_task.done():
            self._spinner_task.cancel()
            self._spinner_task = None

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
        else:
            self._console.print(f"[red]未知命令: {user_input}[/]")
