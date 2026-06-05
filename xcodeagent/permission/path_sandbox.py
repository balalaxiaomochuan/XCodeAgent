"""Layer 2: 路径沙箱 — 文件操作限制在项目根目录内。

仅对 write_file / edit_file 生效（Bash 由 Layer 1 和 Layer 3 控制）。
防御路径遍历逃逸、符号链接绕过、Windows UNC/DOS 设备名。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from xcodeagent.permission import PermissionResult, Verdict


# ── Windows 危险路径检测 ──────────────────────────────────────────

# DOS 设备文件名（Windows 保留名称）
_DOS_DEVICE_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9",
})

# UNC 路径检测正则
_UNC_PATTERN = re.compile(r"^\\\\")


def _check_windows_danger(path_str: str) -> str | None:
    """检查 Windows 特定的危险路径。

    Returns:
        错误消息字符串，如果路径安全则返回 None。
    """
    if sys.platform != "win32":
        return None

    # UNC 路径
    if _UNC_PATTERN.match(path_str):
        return f"禁止访问 UNC 网络路径: {path_str}"

    # DOS 设备名（检查文件名和 stem，忽略扩展名）
    p = Path(path_str)
    # 检查完整文件名（如 COM1）和 stem（如 COM1.txt → COM1）
    for name in (p.name.upper(), p.stem.upper()):
        if name in _DOS_DEVICE_NAMES:
            return f"禁止访问 Windows 保留设备名: {p.name}"

    return None


# ── 路径沙箱 ──────────────────────────────────────────────────────


class PathSandbox:
    """Layer 2: 文件操作路径沙箱。

    确保 WriteFile / EditFile 操作的文件路径在项目根目录内。

    使用方式:
        sandbox = PathSandbox(Path("/home/user/project"))
        result = sandbox.check("write_file", {"file_path": "/etc/passwd"})
        # result.verdict == Verdict.BLOCK
    """

    # Layer 2 针对的工具列表
    _SCOPED_TOOLS = frozenset({"write_file", "edit_file"})

    def __init__(self, project_root: Path):
        self._project_root = project_root.resolve()

    @property
    def project_root(self) -> Path:
        return self._project_root

    def check(self, tool_name: str, params: dict) -> PermissionResult:
        """检查文件路径是否在项目根目录内。

        Args:
            tool_name: 工具名称。
            params: 工具参数字典（需包含 file_path 或 content 中的 file_path）。

        Returns:
            ALLOW 如果路径安全，BLOCK 如果路径越界。
        """
        # 仅对文件写工具生效
        if tool_name not in self._SCOPED_TOOLS:
            return PermissionResult.allowed(
                reason=f"Layer 2 仅对文件写工具生效，跳过 {tool_name}",
                source_layer=2,
            )

        file_path = params.get("file_path", "")
        if not file_path:
            return PermissionResult.blocked(
                reason="路径沙箱: 缺少 file_path 参数",
                risk_level="high",
                source_layer=2,
            )

        # ── Windows 危险路径检查 ──
        danger_msg = _check_windows_danger(str(file_path))
        if danger_msg:
            return PermissionResult.blocked(
                reason=f"路径沙箱: {danger_msg}",
                risk_level="high",
                source_layer=2,
            )

        # ── 路径解析与边界检查 ──
        try:
            p = Path(file_path)
            if p.is_absolute():
                resolved = p.resolve()
            else:
                resolved = (self._project_root / p).resolve()

            resolved.relative_to(self._project_root)
        except ValueError:
            return PermissionResult.blocked(
                reason=(
                    f"路径沙箱拦截: 文件路径 \"{file_path}\" "
                    f"不在项目根目录 \"{self._project_root}\" 内。\n"
                    "请使用项目目录内的相对路径或绝对路径。"
                ),
                risk_level="high",
                source_layer=2,
            )
        except OSError as e:
            return PermissionResult.blocked(
                reason=f"路径沙箱: 路径解析失败 - {e}",
                risk_level="high",
                source_layer=2,
            )

        return PermissionResult.allowed(reason="Layer 2 通过", source_layer=2)

    def validate_path(self, file_path: str | Path) -> Path:
        """同步版路径校验（供 BaseTool._validate_path() 直接复用）。

        Returns:
            规范化后的绝对 Path。

        Raises:
            ValueError: 路径不在项目根目录内或无效。
        """
        p = Path(file_path)

        # Windows 危险路径检查
        danger_msg = _check_windows_danger(str(p))
        if danger_msg:
            raise ValueError(danger_msg)

        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self._project_root / p).resolve()

        try:
            resolved.relative_to(self._project_root)
        except ValueError:
            raise ValueError(
                f"路径不在项目根目录内: {file_path}（根目录: {self._project_root}）"
            )
        return resolved
