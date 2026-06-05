"""权限安全系统 — 五层递进式防线。

Layer 1: 高危命令拦截（不可绕过）
Layer 2: 路径沙箱（文件操作边界）
Layer 3: 细粒度权限规则引擎
Layer 4: 权限模式决策
Layer 5: 确认对话框

公开 API：
    Verdict, PermissionResult, PermissionConfig,
    PermissionManager, PermissionMode
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class Verdict(Enum):
    """权限判决结果。"""
    ALLOW = auto()
    BLOCK = auto()
    ASK = auto()


@dataclass
class PermissionResult:
    """单次权限检查的判决结果。

    Attributes:
        verdict: ALLOW / BLOCK / ASK
        reason: 人类可读的判决理由（用于 TUI 展示和 ToolResult）
        risk_level: 风险级别 "low" / "medium" / "high" / "critical"
        source_layer: 做出判决的层级 (1-5)
        matched_rule: 匹配到的规则描述（Layer 3 用）
        summary: 单行摘要（TUI 确认对话框用）
    """
    verdict: Verdict
    reason: str = ""
    risk_level: str = ""
    source_layer: int = 0
    matched_rule: str = ""
    summary: str = ""

    # ── 便捷属性 ──────────────────────────────────────────

    @property
    def is_allow(self) -> bool:
        return self.verdict == Verdict.ALLOW

    @property
    def is_block(self) -> bool:
        return self.verdict == Verdict.BLOCK

    @property
    def is_ask(self) -> bool:
        return self.verdict == Verdict.ASK

    # ── 工厂方法 ──────────────────────────────────────────

    @classmethod
    def allowed(cls, reason: str = "", source_layer: int = 0) -> PermissionResult:
        return cls(verdict=Verdict.ALLOW, reason=reason, source_layer=source_layer)

    @classmethod
    def blocked(
        cls,
        reason: str,
        risk_level: str = "high",
        source_layer: int = 0,
    ) -> PermissionResult:
        return cls(
            verdict=Verdict.BLOCK,
            reason=reason,
            risk_level=risk_level,
            source_layer=source_layer,
        )

    @classmethod
    def ask(
        cls,
        reason: str,
        risk_level: str = "medium",
        source_layer: int = 0,
        summary: str = "",
    ) -> PermissionResult:
        return cls(
            verdict=Verdict.ASK,
            reason=reason,
            risk_level=risk_level,
            source_layer=source_layer,
            summary=summary,
        )


@dataclass
class PermissionConfig:
    """权限系统配置（从 config.json 的 permissions 段加载）。

    Attributes:
        mode: 权限模式 "default" / "acceptEdits" / "plan"
        confirm_timeout: 确认对话框超时秒数, 0=永不超时
        rules: 用户自定义规则列表
        prepend_rules: True=用户规则插入内置规则之前
        dangerous_commands_extra: 追加的高危命令黑名单
    """
    mode: str = "default"
    confirm_timeout: float = 0.0
    rules: list[dict] = field(default_factory=list)
    prepend_rules: bool = False
    dangerous_commands_extra: list[dict] = field(default_factory=list)

    def validate(self) -> list[str]:
        """校验配置合法性，返回错误消息列表。"""
        errors = []
        if self.mode not in ("default", "acceptEdits", "plan"):
            errors.append(
                f"permissions.mode 无效值 '{self.mode}'，"
                "仅支持: default / acceptEdits / plan"
            )
        if self.confirm_timeout < 0:
            errors.append("permissions.confirm_timeout 不能为负数")
        return errors


# ── 延迟导入以避免循环依赖 ──────────────────────────────────────

from xcodeagent.permission.manager import PermissionManager  # noqa: E402, F811
from xcodeagent.permission.modes import PermissionMode  # noqa: E402, F811
