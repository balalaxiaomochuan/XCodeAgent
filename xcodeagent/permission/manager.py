"""五层权限协调器 — 串联 Layer 1-5，返回统一判决。

权限检查流水线:
    Layer 1: 高危命令拦截 (永远生效)
    Layer 2: 路径沙箱 (仅 write_file/edit_file)
    [本轮全部允许?] → 跳过 Layer 3/4
    Layer 3: 规则引擎 (plan 模式跳过)
    Layer 4: 权限模式决策
    如果上述返回 ASK → Layer 5 (确认对话框, 在 Agent/TUI 层实现)
"""

from __future__ import annotations

from pathlib import Path

from xcodeagent.permission import PermissionConfig, PermissionResult, Verdict
from xcodeagent.permission.dangerous_cmds import DangerousCommandFilter
from xcodeagent.permission.modes import ModeDecision, PermissionMode
from xcodeagent.permission.path_sandbox import PathSandbox
from xcodeagent.permission.rules import RulesEngine


class PermissionManager:
    """五层权限协调器。

    使用方式:
        config = PermissionConfig(mode="default")
        mgr = PermissionManager(Path("/project"), config)

        # 检查工具调用
        result = mgr.check("bash", {"command": "git status"})
        if result.is_allow:
            ... 执行 ...
        elif result.is_block:
            ... 返回错误 ...
        elif result.is_ask:
            ... 发送 PermissionRequest 到 TUI ...

        # 用户选择"本轮全部允许"
        mgr.allow_all_this_round()

        # 新一轮开始时重置
        mgr.reset_round()
    """

    def __init__(
        self,
        project_root: Path,
        config: PermissionConfig | None = None,
    ):
        config = config or PermissionConfig()

        self._mode = PermissionMode.from_string(config.mode)
        self._layer1 = DangerousCommandFilter(config.dangerous_commands_extra)
        self._layer2 = PathSandbox(project_root)
        self._layer3 = RulesEngine(config.rules, config.prepend_rules)
        self._layer4 = ModeDecision()

        self._round_allow_all = False

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    @mode.setter
    def mode(self, value: PermissionMode) -> None:
        self._mode = value

    # ── 核心检查方法 ─────────────────────────────────────────

    def check(self, tool_name: str, params: dict) -> PermissionResult:
        """依次经过 Layer 1→4，返回最终判决。

        Args:
            tool_name: 工具名称。
            params: 工具参数字典。

        Returns:
            PermissionResult: ALLOW / BLOCK / ASK
        """
        # ── Layer 1: 高危命令拦截（永远生效, 且对所有工具）──
        if tool_name == "bash":
            result = self._layer1.check(params.get("command", ""))
            if result.is_block:
                return result

        # ── Layer 2: 路径沙箱（仅 write_file / edit_file）──
        if tool_name in ("write_file", "edit_file"):
            result = self._layer2.check(tool_name, params)
            if result.is_block:
                return result

        # ── 本轮全部允许 → 跳过 Layer 3/4 ──
        if self._round_allow_all:
            return PermissionResult.allowed(
                reason="本轮全部允许",
                source_layer=5,
            )

        # ── Layer 3: 规则引擎（plan 模式跳过）──
        if self._mode != PermissionMode.PLAN:
            result = self._layer3.check(tool_name, params)
            if result is not None:
                # 规则明确 allow/deny → 直接返回
                if result.is_allow or result.is_block:
                    return result
                # 规则返回 ASK → 进入 Layer 5（由外部处理）
                return result

        # ── Layer 4: 权限模式决策 ──
        return self._layer4.decide(self._mode, tool_name)

    # ── 本轮状态管理 ─────────────────────────────────────────

    def allow_all_this_round(self) -> None:
        """设置本轮全部允许。后续工具跳过 Layer 3/4。"""
        self._round_allow_all = True

    def reset_round(self) -> None:
        """新一轮开始时重置'本轮全部允许'状态。"""
        self._round_allow_all = False

    def revoke_allow_all(self) -> None:
        """撤销本轮全部允许（/revoke 命令）。"""
        self._round_allow_all = False

    @property
    def is_allow_all_active(self) -> bool:
        """本轮全部允许是否激活。"""
        return self._round_allow_all
