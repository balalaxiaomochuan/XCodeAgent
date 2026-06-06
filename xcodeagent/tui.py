"""终端交互界面（TUI）：基于 Rich 的流式对话 UI，消费 Agent 事件流。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pyfiglet
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from xcodeagent.agent import Agent
from xcodeagent.chat import ChatSession
from xcodeagent.config import AppConfig
from xcodeagent.events import (
    AgentError,
    AgentEvent,
    PermissionRequest,
    PermissionResponse,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    UserMessage,
)
from xcodeagent.command_analyzer import analyze_tool_call
from xcodeagent.input_ui import InputUI
from xcodeagent.permission import PermissionMode
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
        project_root: Path | None = None,
    ):
        self._agent = agent
        self._chat = chat_session
        self._config = config
        self._console = Console()
        self._running = True
        self._first_text = True
        self._in_thinking = False
        self._input_ui = InputUI(project_root=project_root or Path.cwd())

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
        label = self._mode_label()
        self._console.print(Rule(style="dim cyan"))
        self._console.print(f"{label}[bold cyan]▸[/] ", end="")
        sys.stdout.flush()
        return await self._input_ui.get_input()

    def _mode_label(self) -> str:
        """构建权限模式标签。"""
        parts = []
        if self._agent._config.plan_only:
            parts.append("[yellow]PLAN-ONLY[/] ")
        if self._agent._permission:
            mode = self._agent._permission.mode
            labels = {
                PermissionMode.DEFAULT: "[dim][D][/] ",
                PermissionMode.ACCEPT_EDITS: "[dim][A][/] ",
                PermissionMode.PLAN: "[yellow][P][/] ",
            }
            label = labels.get(mode, "")
            if self._agent._permission.is_allow_all_active:
                label = label.replace("[/]", "*[/]")
            parts.append(label)
        return "".join(parts)

    # ── 欢迎界面 ──────────────────────────────────────────

    def _print_welcome(self) -> None:
        _print_logo()

        info = Text()
        info.append("v0.4.0", style="dim")
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
        self._console.print(
            f"[bold bright_black]▸[/] [bright_black]{self._escape(user_input)}[/]"
        )
        self._console.print()

        self._first_text = True
        self._turn_has_output = False

        cancel_token = asyncio.Event()
        esc_task = asyncio.create_task(_listen_for_esc(cancel_token))

        # 创建 Agent 事件消费任务
        agent_events: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        async def collect_events():
            try:
                async for event in self._agent.run(user_input, cancel_token):
                    await agent_events.put(event)
            except Exception:
                pass
            finally:
                await agent_events.put(None)  # 结束信号

        collect_task = asyncio.create_task(collect_events())

        try:
            self._console.print("[dim]  thinking...[/]")

            while True:
                # 先检查权限请求（不阻塞）
                req = self._try_get_perm_request()
                if req is not None:
                    response = await self._show_permission_dialog(req)
                    await self._agent.permission_response_queue.put(response)
                    continue

                # 等待 Agent 事件（带超时，以便周期性检查权限请求）
                try:
                    event = await asyncio.wait_for(agent_events.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    # 检查结束条件
                    if agent_events.empty() and collect_task.done():
                        break
                    continue

                if event is None:
                    break
                self._dispatch(event)

                # 事件流结束后检查是否还有未消费事件
                if agent_events.empty() and collect_task.done():
                    # 再检查一次权限队列
                    req = self._try_get_perm_request()
                    if req is None:
                        break

        except KeyboardInterrupt:
            cancel_token.set()
            self._console.print("\n[yellow]已中断当前回复[/]")
        except ProviderError as e:
            self._console.print(f"\n[red]API 错误: {self._escape(str(e))}[/]")
        except Exception as e:
            self._console.print(f"\n[red]Agent 错误: {self._escape(str(e))}[/]")
        finally:
            esc_task.cancel()
            collect_task.cancel()
            try:
                await asyncio.gather(esc_task, collect_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass

        self._console.print(Rule(style="dim grey30"))
        self._console.print()

    def _try_get_perm_request(self) -> PermissionRequest | None:
        """非阻塞方式获取 Agent 的权限请求。"""
        if hasattr(self._agent, '_perm_request_queue'):
            try:
                return self._agent._perm_request_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        return None

    # ── 权限确认对话框 ──────────────────────────────────────

    async def _show_permission_dialog(
        self, request: PermissionRequest
    ) -> PermissionResponse:
        """渲染权限确认对话框并用方向键选择决策。"""

        risk_colors = {
            "low": "green",
            "medium": "yellow",
            "high": "red",
            "critical": "bold red",
        }
        risk_color = risk_colors.get(request.risk_level, "yellow")

        analysis = analyze_tool_call(request.tool_name, request.tool_input)

        esc = self._escape
        param_lines = []
        for k, v in request.tool_input.items():
            s = str(v)
            if len(s) > 100:
                s = s[:97] + "..."
            param_lines.append(f"  {esc(k)}: {esc(s)}")

        body_parts = [
            f"[bold]工具:[/] {esc(request.tool_name)}",
            f"[bold]用途:[/] [bold cyan]{esc(analysis.summary)}[/]",
            f"[bold]影响:[/] {esc(analysis.impact)}",
        ]
        if analysis.risk_hint:
            body_parts.append(f"[bold]⚠ 注意:[/] [yellow]{esc(analysis.risk_hint)}[/]")
        body_parts += [
            "",
            f"[bold]参数:[/]",
            *param_lines,
            "",
            f"[bold]风险级别:[/] [{risk_color}]{esc(request.risk_level.upper())}[/]",
            f"[bold]触发来源:[/] {esc(request.source_description)}",
        ]

        panel = Panel(
            "\n".join(body_parts),
            title="[bold]⚠ 确认工具调用[/]",
            border_style=risk_color,
            padding=(1, 2),
            safe_box=True,
        )
        self._console.print()
        self._console.print(panel)
        self._console.print()

        # ── 可方向键选择的选项菜单 ──
        options = [
            ("allow_once",  "允许本次",    "green"),
            ("deny",        "拒绝",        "red"),
            ("allow_all",   "本轮全部允许", "yellow"),
        ]
        selected = 0  # 默认选中"允许本次"

        # Y/N/A 快捷键映射
        shortcut_map = {"Y": 0, "N": 1, "A": 2}

        while True:
            # 渲染选项行
            parts = []
            for i, (_, label, color) in enumerate(options):
                if i == selected:
                    parts.append(f"[bold white on {color}] ◀ {label} ▶ [/]")
                else:
                    parts.append(f"[{color}][{label}][/]")
            self._console.print("  " + "  ".join(parts))
            self._console.print()
            self._console.print(
                f"[dim]  ← → 选择  |  Enter 确认  |  Y/N/A 快捷键[/]"
            )

            # 读取按键
            key = await self._read_key()

            if key == "left":
                selected = (selected - 1) % len(options)
            elif key == "right":
                selected = (selected + 1) % len(options)
            elif key == "enter":
                break
            elif key.upper() in shortcut_map:
                selected = shortcut_map[key.upper()]
                break

            # 清除选项行（上移 3 行: 选项行 + 空行 + 提示行）
            sys.stdout.write("\033[3A\033[J")
            sys.stdout.flush()

        decision = options[selected][0]

        result_text = {
            "allow_once": "[green]✓ 已允许本次执行[/]",
            "allow_all": "[yellow]✓ 已允许本轮全部执行[/]",
            "deny": "[red]✗ 已拒绝[/]",
        }
        self._console.print(result_text[decision])
        self._console.print()

        return PermissionResponse(
            request_id=request.request_id,
            tool_call_id=request.tool_call_id,
            decision=decision,
        )

    async def _read_key(self) -> str:
        """读取单个按键，支持方向键（返回 'up'/'down'/'left'/'right'/'enter'）。"""
        if sys.platform == "win32":
            import msvcrt
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch == b'\xe0' or ch == b'\x00':
                        # 方向键等扩展键
                        ch2 = msvcrt.getch()
                        return {
                            b'H': 'up', b'P': 'down',
                            b'K': 'left', b'M': 'right',
                        }.get(ch2, '?')
                    elif ch == b'\r':
                        return 'enter'
                    else:
                        try:
                            return ch.decode("utf-8")
                        except UnicodeDecodeError:
                            return '?'
                await asyncio.sleep(0.03)
        else:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while True:
                    ch = await asyncio.to_thread(sys.stdin.read, 1)
                    if ch == '\x1b':
                        # 可能是 Escape 或方向键序列
                        ch2 = await asyncio.to_thread(sys.stdin.read, 1)
                        if ch2 == '[':
                            ch3 = await asyncio.to_thread(sys.stdin.read, 1)
                            return {'A': 'up', 'B': 'down',
                                    'C': 'right', 'D': 'left'}.get(ch3, 'esc')
                        return 'esc'
                    elif ch in ('\r', '\n'):
                        return 'enter'
                    else:
                        return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    # ── 事件分发 ──────────────────────────────────────────

    def _dispatch(self, event: AgentEvent) -> None:
        """匹配事件类型并分发到对应渲染方法。"""
        if isinstance(event, UserMessage):
            pass
        elif isinstance(event, ThinkingDelta):
            if not self._config.show_thinking:
                return
            self._turn_has_output = True
            self._render_thinking(event.text)
        elif isinstance(event, TextDelta):
            self._end_thinking()
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
            self._console.print(f"\n[red]⚠ {self._escape(event.message)}[/]")

    # ── 事件渲染方法 ──────────────────────────────────────

    @staticmethod
    def _escape(s: str) -> str:
        """转义字符串中的 Rich markup 特殊字符，避免 LLM 输出被误解析。"""
        return s.replace("[", "\\[")

    def _end_thinking(self) -> None:
        """结束 thinking 模式，换行以便后续内容另起一行。"""
        if self._in_thinking:
            self._console.print()
            self._in_thinking = False

    def _render_thinking(self, text: str) -> None:
        """thinking 增量：灰色文字流式追加。"""
        if not self._in_thinking:
            self._console.print()
            self._console.print("[dim]💭 ", end="")
            self._in_thinking = True
        self._console.print(self._escape(text), end="", style="dim")

    def _render_text(self, text: str) -> None:
        """文本增量：正常颜色流式追加（禁用 Rich markup 防止 LLM 输出被误解析）。"""
        if self._first_text:
            self._first_text = False
            self._console.print("[bold cyan]|[/] ", end="")
        self._console.print(self._escape(text), end="")

    def _render_tool_start(self, name: str, tool_input: dict) -> None:
        """工具调用开始：工具名 + 参数摘要。"""
        input_preview = ", ".join(f"{k}={v}" for k, v in tool_input.items())
        if len(input_preview) > 60:
            input_preview = input_preview[:57] + "..."
        self._console.print(
            f"\n[bold cyan]  ⚡ {name}[/] [dim]({self._escape(input_preview)})[/]"
        )

    def _render_tool_end(
        self, name: str, success: bool, output: str, error: str | None
    ) -> None:
        """工具调用结束：成功/失败状态 + 结果摘要。"""
        if success:
            first_line = output.split("\n")[0] if output else "(empty)"
            if len(first_line) > 80:
                first_line = first_line[:77] + "..."
            self._console.print(
                f"[bold green]  ✅ {self._escape(first_line)}[/]"
            )
        else:
            err = error or "unknown"
            if len(err) > 80:
                err = err[:77] + "..."
            self._console.print(
                f"[bold red]  ❌ {self._escape(err)}[/]"
            )

    # ── 命令处理 ──────────────────────────────────────────

    def _handle_command(self, user_input: str) -> None:
        cmd = user_input.lower().strip()
        parts = user_input.strip().split(maxsplit=1)

        if cmd in ("/exit", "/quit"):
            self._running = False
        elif cmd == "/clear":
            self._chat.clear()
            self._console.print(Rule("对话已清空", style="dim"))
            self._console.print()
        elif cmd == "/help":
            self._print_help()
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
        elif parts[0] == "/mode":
            self._handle_mode_command(parts)
        elif cmd == "/revoke":
            if self._agent._permission:
                self._agent._permission.revoke_allow_all()
                self._console.print('[yellow]已撤销本轮"全部允许"许可。[/]')
            else:
                self._console.print("[dim]权限系统未启用。[/]")
            self._console.print()
        elif cmd == "/perm":
            self._print_perm_status()
        else:
            self._console.print(f"[red]未知命令: {self._escape(user_input)}[/]")

    def _handle_mode_command(self, parts: list[str]) -> None:
        """处理 /mode 命令。"""
        if len(parts) == 1:
            # /mode — 显示当前模式
            if self._agent._permission:
                mode = self._agent._permission.mode.value
                self._console.print(f"[dim]当前权限模式: [bold]{mode}[/][/]")
            else:
                self._console.print("[dim]权限系统未启用。[/]")
            self._console.print()
            return

        mode_str = parts[1].strip().lower()
        if self._agent._permission is None:
            self._console.print("[red]权限系统未启用，无法切换模式。[/]")
            self._console.print()
            return

        try:
            new_mode = PermissionMode.from_string(mode_str)
            self._agent._permission.mode = new_mode
            self._agent._permission.revoke_allow_all()

            labels = {
                PermissionMode.DEFAULT: "default — 读工具自动放行，写/Bash 需确认",
                PermissionMode.ACCEPT_EDITS: "acceptEdits — 文件写自动放行，Bash 需确认",
                PermissionMode.PLAN: "plan — 只允许读操作",
            }
            self._console.print(f"[green]已切换权限模式: {labels[new_mode]}[/]")
        except ValueError as e:
            self._console.print(f"[red]{self._escape(str(e))}[/]")
        self._console.print()

    def _print_perm_status(self) -> None:
        """显示当前权限配置摘要。"""
        self._console.print()
        if self._agent._permission is None:
            self._console.print("[dim]权限系统未启用[/]")
        else:
            pm = self._agent._permission
            self._console.print("权限配置:", style="bold")
            self._console.print(f"  模式: {pm.mode.value}")
            self._console.print(f"  本轮全部允许: {'是' if pm.is_allow_all_active else '否'}")
            self._console.print(f"  plan_only: {'是' if self._agent._config.plan_only else '否'}")
        self._console.print()

    def _print_help(self) -> None:
        self._console.print()
        self._console.print("可用命令：", style="bold")
        self._console.print("  /exit, /quit      退出程序")
        self._console.print("  /clear            清空对话历史")
        self._console.print("  /plan-on          进入计划模式（只读，不执行写操作）")
        self._console.print("  /plan-off         退出计划模式")
        self._console.print("  /thinking-on      展示模型思考过程")
        self._console.print("  /thinking-off     隐藏模型思考过程")
        self._console.print("  /mode             显示当前权限模式")
        self._console.print("  /mode <mode>      切换权限模式（default/acceptEdits/plan）")
        self._console.print("  /revoke           撤销本轮全部允许")
        self._console.print("  /perm             显示权限配置摘要")
        self._console.print("  /help             显示本帮助")
        self._console.print()
        self._console.print("快捷键：", style="bold")
        self._console.print("  Esc               取消当前 Agent 循环")
        self._console.print("  Ctrl+C            中断当前回复（兜底）")
        self._console.print("  Ctrl+D / EOF      退出程序")
        self._console.print()
