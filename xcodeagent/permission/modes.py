"""Layer 4: 权限模式决策。

在 Layer 3 无法明确判断后，根据当前权限模式对工具执行做最终决策。

三种模式:
    default     — 读工具自动放行, 写工具/Bash 需要确认
    acceptEdits — 读/文件写自动放行, Bash 需要确认
    plan        — 读工具放行, 写工具/Bash 全部拦截
"""

from __future__ import annotations

from enum import Enum

from xcodeagent.permission import PermissionResult, Verdict


class PermissionMode(Enum):
    """权限模式枚举。"""
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"

    @classmethod
    def from_string(cls, s: str) -> PermissionMode:
        """从字符串解析权限模式。无效值抛出 ValueError。"""
        try:
            return cls(s)
        except ValueError:
            raise ValueError(
                f"无效的权限模式 '{s}'，仅支持: default / acceptEdits / plan"
            )


# ── 模式决策矩阵 ──────────────────────────────────────────────────

# 格式: {mode: {tool_category_or_name: Verdict}}
# "read" 类工具在所有模式都是 ALLOW（除非 plan 也 ALLOW 读）
_MODE_DECISION: dict[PermissionMode, dict[str, Verdict]] = {
    PermissionMode.DEFAULT: {
        "read": Verdict.ALLOW,
        "write_file": Verdict.ASK,
        "edit_file": Verdict.ASK,
        "bash": Verdict.ASK,
    },
    PermissionMode.ACCEPT_EDITS: {
        "read": Verdict.ALLOW,
        "write_file": Verdict.ALLOW,
        "edit_file": Verdict.ALLOW,
        "bash": Verdict.ASK,
    },
    PermissionMode.PLAN: {
        "read": Verdict.ALLOW,
        "write": Verdict.BLOCK,   # 所有写工具
    },
}


# ── 决策器 ────────────────────────────────────────────────────────


class ModeDecision:
    """Layer 4: 权限模式决策器。

    使用方式:
        decider = ModeDecision()
        result = decider.decide(PermissionMode.DEFAULT, "bash")
        # result.verdict == Verdict.ASK
    """

    @staticmethod
    def decide(
        mode: PermissionMode,
        tool_name: str,
    ) -> PermissionResult:
        """根据权限模式对工具做出决策。

        Args:
            mode: 当前权限模式。
            tool_name: 工具名称。

        Returns:
            PermissionResult with source_layer=4。
        """
        matrix = _MODE_DECISION.get(mode, {})

        # 先精确匹配工具名
        if tool_name in matrix:
            verdict = matrix[tool_name]
        elif tool_name in ("write_file", "edit_file", "bash"):
            # 写工具，检查 "write" 分类
            verdict = matrix.get("write", Verdict.ASK)
        else:
            # 读类工具，检查 "read" 分类
            verdict = matrix.get("read", Verdict.ALLOW)

        return ModeDecision._verdict_to_result(verdict, mode, tool_name)

    @staticmethod
    def _verdict_to_result(
        verdict: Verdict,
        mode: PermissionMode,
        tool_name: str,
    ) -> PermissionResult:
        """将 Verdict + 模式信息转换为带描述的 PermissionResult。"""
        if verdict == Verdict.ALLOW:
            return PermissionResult.allowed(
                reason=f"模式 '{mode.value}' 自动放行 {tool_name}",
                source_layer=4,
            )
        elif verdict == Verdict.BLOCK:
            if mode == PermissionMode.PLAN:
                return PermissionResult.blocked(
                    reason=(
                        f"[Plan 模式] 写操作 '{tool_name}' 已拦截。"
                        "Plan 模式下只允许读操作（ReadFile/Glob/Grep）。\n"
                        "使用 /mode default 恢复正常模式。"
                    ),
                    risk_level="medium",
                    source_layer=4,
                )
            return PermissionResult.blocked(
                reason=f"模式 '{mode.value}' 拦截 {tool_name}",
                risk_level="medium",
                source_layer=4,
            )
        else:  # ASK
            return PermissionResult.ask(
                reason=f"模式 '{mode.value}' — {tool_name} 需要用户确认",
                risk_level="medium",
                source_layer=4,
                summary=f"{tool_name} 需要确认（模式: {mode.value}）",
            )
