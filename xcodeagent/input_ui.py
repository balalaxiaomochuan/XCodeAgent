"""增强输入模块：基于 prompt_toolkit 的智能终端输入。

提供:
    - / 指令自动补全（斜杠命令 + 描述）
    - @ 文件引用补全（项目目录文件模糊匹配）
    - 历史记录（上下方向键浏览，FileHistory 持久化）
    - 补全菜单方向键交互（↑↓移动，Tab选中，Esc关闭）
    - 降级策略（prompt_toolkit 未安装时回退原生 input）
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 斜杠命令定义 ──────────────────────────────────────────────────

COMMANDS: dict[str, str] = {
    "/exit":              "退出程序",
    "/quit":              "退出程序",
    "/clear":             "清空对话历史",
    "/plan-on":           "进入计划模式（只读，拦截写操作）",
    "/plan-off":          "退出计划模式，恢复所有工具",
    "/thinking-on":       "展示模型思考过程",
    "/thinking-off":      "隐藏模型思考过程",
    "/mode":              "显示当前权限模式",
    "/mode default":      "切换权限模式: default（写/Bash需确认）",
    "/mode acceptEdits":  "切换权限模式: acceptEdits（文件写自动放行）",
    "/mode plan":         "切换权限模式: plan（仅读操作）",
    "/revoke":            "撤销本轮全部允许",
    "/perm":              "显示权限配置摘要",
    "/help":              "显示帮助信息",
}

# ── 忽略目录 ──────────────────────────────────────────────────────

_IGNORE_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vscode", ".vs", "dist", "build", ".eggs",
    "*.egg-info",
})

# ── ContextAwareCompleter ──────────────────────────────────────────


try:
    from prompt_toolkit.completion import Completer, Completion

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

    class Completer:  # type: ignore
        pass

    class Completion:  # type: ignore
        def __init__(self, text, **kwargs):
            self.text = text


if _HAS_PROMPT_TOOLKIT:

    class ContextAwareCompleter(Completer):
        """上下文感知的自动补全器。

        规则:
            - 文本以 '/' 开头且无空格 → 补全斜杠命令
            - 文本包含 '@' → 在 @ 后面的文字上补全文件路径
            - 其他 → 不补全
        """

        def __init__(self, project_root: Path):
            self._project_root = project_root
            self._file_cache: tuple[float, list[tuple[str, str, bool]]] | None = None

        # ── 入口 ─────────────────────────────────────────────

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor

            # / 命令补全
            if text.startswith("/"):
                yield from self._complete_command(text)
                return

            # @ 文件补全
            last_at = text.rfind("@")
            if last_at >= 0:
                partial = text[last_at + 1:]
                yield from self._complete_file(partial)
                return

        # ── 命令补全 ─────────────────────────────────────────

        def _complete_command(self, text: str):
            """匹配以 text 开头的斜杠命令。"""
            text_lower = text.lower()
            # 如果包含空格（如 /mode default），不再补全
            if " " in text.strip():
                return

            for cmd, desc in COMMANDS.items():
                if cmd.lower().startswith(text_lower):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=desc,
                    )

        # ── 文件补全 ─────────────────────────────────────────

        def _complete_file(self, partial: str):
            """在项目根目录下搜索匹配 partial 的文件。

            如果 partial 包含 /或\\, 在对应子目录搜索。
            结果: 目录优先, 字母排序, 最多 50 个。
            """
            base = self._project_root

            # 解析搜索目录和前缀
            search_dir = base
            prefix = partial

            # Windows 和 Unix 路径分隔符都支持
            if "/" in partial or "\\" in partial:
                last_sep = max(partial.rfind("/"), partial.rfind("\\"))
                dir_part = partial[:last_sep + 1]  # 保留末尾分隔符
                prefix = partial[last_sep + 1:]
                search_dir = base / dir_part

            if not search_dir.exists() or not search_dir.is_dir():
                return

            pattern = f"{prefix}*" if prefix else "*"

            results: list[tuple[str, str, bool]] = []  # (display, text, is_dir)
            try:
                for p in search_dir.glob(pattern):
                    # 跳过忽略目录中的文件
                    if any(part in _IGNORE_DIRS for part in p.parts):
                        continue
                    # 跳过隐藏文件（. 开头）
                    if p.name.startswith("."):
                        continue

                    try:
                        rel_path = str(p.relative_to(base))
                    except ValueError:
                        continue
                    # 统一用 / 分隔符
                    rel_path = rel_path.replace("\\", "/")
                    is_dir = p.is_dir()
                    display = rel_path + "/" if is_dir else rel_path
                    results.append((display, rel_path, is_dir))
            except (OSError, PermissionError):
                return

            # 目录优先，字母排序
            results.sort(key=lambda x: (not x[2], x[1].lower()))

            for display, rel_path, _is_dir in results[:50]:
                yield Completion(
                    rel_path,
                    start_position=-len(prefix),
                    display=display,
                    display_meta="dir" if _is_dir else "file",
                )

else:
    # prompt_toolkit 不可用时的占位补全器
    class ContextAwareCompleter:  # type: ignore
        def __init__(self, project_root: Path):
            pass


# ── InputUI ───────────────────────────────────────────────────────


class InputUI:
    """基于 prompt_toolkit 的增强输入模块。

    使用方式:
        ui = InputUI(project_root=Path("/project"))
        text = await ui.get_input()

    如果 prompt_toolkit 未安装，自动降级为原生 input()。
    """

    def __init__(
        self,
        project_root: Path,
        history_file: Path | None = None,
    ):
        self._project_root = project_root
        self._session = None
        self._warning_printed = False

        if not _HAS_PROMPT_TOOLKIT:
            return

        try:
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.styles import Style
            from prompt_toolkit import PromptSession

            # 历史文件
            hist_path = history_file or self._default_history_path()
            try:
                hist_path.parent.mkdir(parents=True, exist_ok=True)
                history = FileHistory(str(hist_path))
            except (OSError, PermissionError) as e:
                logger.warning("无法创建历史文件 %s: %s", hist_path, e)
                from prompt_toolkit.history import InMemoryHistory
                history = InMemoryHistory()

            # 补全器
            completer = ContextAwareCompleter(project_root)

            # 补全菜单配色 - 与 Rich 暗色终端协调
            style = Style.from_dict({
                "completion-menu": "bg:#2d2d2d #e0e0e0",
                "completion-menu.completion": "bg:#2d2d2d #e0e0e0",
                "completion-menu.completion.current": "bg:#444444 #ffffff",
                "bottom-toolbar": "bg:#1a1a1a #888888",
            })

            self._session = PromptSession(
                history=history,
                style=style,
                complete_while_typing=True,
                completer=completer,
            )
        except Exception as e:
            logger.warning("prompt_toolkit 初始化失败: %s，使用基础输入模式", e)

    # ── 公开接口 ─────────────────────────────────────────────

    async def get_input(self) -> str:
        """异步获取用户输入。

        Returns:
            用户输入的文本。
        """
        if self._session is None:
            return await self._fallback_input()

        try:
            from prompt_toolkit.key_binding import KeyBindings
        except ImportError:
            return await self._fallback_input()

        # 自定义按键绑定
        bindings = KeyBindings()

        @bindings.add("escape")
        def _(event):
            """Esc: 关闭补全菜单或取消输入。"""
            # 如果补全菜单打开，先关闭菜单
            if event.app.current_buffer.complete_state:
                event.app.current_buffer.cancel_completion()
            # 不取消整个输入，Esc 只关闭菜单

        try:
            text = await self._session.prompt_async(
                message="",
                key_bindings=bindings,
                bottom_toolbar=self._toolbar,
            )
            return text
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as e:
            logger.warning("prompt_toolkit 输入异常: %s，回退基础输入", e)
            self._session = None
            return await self._fallback_input()

    async def _fallback_input(self) -> str:
        """降级到原生 input()。"""
        if not self._warning_printed:
            print(
                "[WARNING] prompt_toolkit 未安装或初始化失败，"
                "使用基础输入模式。运行 pip install prompt-toolkit 可启用自动补全。",
                file=sys.stderr,
            )
            self._warning_printed = True
        return await asyncio.to_thread(input, "")

    # ── 内部属性 ─────────────────────────────────────────────

    @property
    def _toolbar(self) -> str:
        """底部快捷键提示栏。"""
        return (
            " [↑↓历史] "
            "[Tab补全] "
            "[/指令] "
            "[@文件] "
            "[Esc关闭补全]"
        )

    @staticmethod
    def _default_history_path() -> Path:
        """默认历史文件路径: ~/.xcodeagent/history"""
        return Path.home() / ".xcodeagent" / "history"
