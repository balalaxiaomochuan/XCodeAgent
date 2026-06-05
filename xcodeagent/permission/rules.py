"""Layer 3: 细粒度权限规则引擎。

基于可配置规则列表，按优先级匹配。每条规则可以是 allow / deny / ask。
内置规则集覆盖常见的 git 操作、测试命令、依赖安装、文件访问等场景。
用户可在 config.json 中自定义规则，插入到内置规则之前或之后。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from xcodeagent.permission import PermissionResult, Verdict


# ── 规则数据模型 ──────────────────────────────────────────────────


@dataclass
class _PermissionRule:
    """一条权限规则（内部使用）。"""
    tool: str                 # 工具名 或 "*"
    pattern: str              # Python 正则表达式
    action: str               # "allow" | "deny" | "ask"
    note: str = ""            # 规则说明
    compiled: re.Pattern = field(init=False)

    def __post_init__(self):
        self.compiled = re.compile(self.pattern)

    def matches(self, tool_name: str, target: str) -> bool:
        """检查此规则是否匹配给定的工具和匹配目标。"""
        if self.tool != "*" and self.tool != tool_name:
            return False
        return bool(self.compiled.search(target))


# ── 内置规则集 ────────────────────────────────────────────────────


def _builtin_rules() -> list[dict]:
    """返回内置权限规则的字典列表。"""
    return [
        # ── Bash: 禁止操作 ──
        {
            "tool": "bash",
            "pattern": r"git\s+push\s+.*(--force|-f).*\b(main|master)\b",
            "action": "deny",
            "note": "禁止强制推送 main/master 分支",
        },
        {
            "tool": "bash",
            "pattern": r"git\s+push\s+.*--delete.*\b(main|master)\b",
            "action": "deny",
            "note": "禁止删除远程 main/master 分支",
        },
        {
            "tool": "bash",
            "pattern": r"(pip|pip3)\s+install\b",
            "action": "deny",
            "note": "禁止 pip 安装包",
        },
        {
            "tool": "bash",
            "pattern": r"(pip|pip3)\s+uninstall\b",
            "action": "deny",
            "note": "禁止 pip 卸载包",
        },
        {
            "tool": "bash",
            "pattern": r"uv\s+pip\s+install\b",
            "action": "deny",
            "note": "禁止 uv pip 安装包",
        },
        {
            "tool": "bash",
            "pattern": r"uv\s+pip\s+uninstall\b",
            "action": "deny",
            "note": "禁止 uv pip 卸载包",
        },
        {
            "tool": "bash",
            "pattern": r"\b(npm|yarn|pnpm)\s+(install|add|remove|uninstall)\b",
            "action": "deny",
            "note": "禁止 npm/yarn/pnpm 安装/卸载包",
        },
        {
            "tool": "bash",
            "pattern": r"\b(gem|cargo|go)\s+install\b",
            "action": "deny",
            "note": "禁止 gem/cargo/go 安装包",
        },
        {
            "tool": "bash",
            "pattern": r"\b(apt-get|apt|brew|choco|scoop)\s+(install|uninstall|remove)\b",
            "action": "deny",
            "note": "禁止系统包管理器安装/卸载",
        },
        {
            "tool": "bash",
            "pattern": r"\bpipx\s+install\b",
            "action": "deny",
            "note": "禁止 pipx 安装",
        },
        {
            "tool": "bash",
            "pattern": (
                r"(curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|irm)"
                r"\s+.*\|\s*(sh|bash|python|ruby|perl|node)"
            ),
            "action": "deny",
            "note": "禁止 curl/wget pipe to shell（远程代码执行）",
        },

        # ── Bash: 安全命令自动放行 ──
        {
            "tool": "bash",
            "pattern": (
                r"^git\s+(status|diff|log|branch|add|commit|stash|checkout|switch|"
                r"restore|show|blame|tag|remote|fetch|pull|merge|rebase|cherry-pick|"
                r"bisect|grep|rev-parse|rev-list|ls-files|ls-tree|cat-file|"
                r"config\s+--list|describe|shortlog|submodule|worktree|clean|rm|mv|"
                r"reset\s+(?!.*--hard\s+HEAD~)(?!.*origin/)|reflog|init|clone)"
            ),
            "action": "allow",
            "note": "允许安全的 git 日常操作",
        },
        {
            "tool": "bash",
            "pattern": (
                r"^(pytest|python\s+-m\s+pytest|tox|nox|mypy|ruff|black|isort|"
                r"flake8|pylint|eslint|prettier|cargo\s+test|go\s+test|"
                r"jest|vitest|rspec|unittest)(\s.*)?$"
            ),
            "action": "allow",
            "note": "允许运行测试和代码检查工具",
        },
        {
            "tool": "bash",
            "pattern": r"^(uv\s+run|poetry\s+run|pipenv\s+run|hatch\s+run)",
            "action": "allow",
            "note": "允许通过包管理器运行命令",
        },

        # ── Bash: 需用户确认 ──
        {
            "tool": "bash",
            "pattern": (
                r"^(python|node|npx|tsx|ts-node|deno\s+run|bun\s+run|"
                r"cargo\s+run|go\s+run|make|just|npm\s+run|yarn\s+run|"
                r"pnpm\s+run)(\s.*)?$"
            ),
            "action": "ask",
            "note": "运行项目命令需要用户确认",
        },
        {
            "tool": "bash",
            "pattern": (
                r"^(docker|podman)(\s+run|\s+compose|\s+stack|\s+swarm|"
                r"\s+start|\s+stop|\s+rm|\s+rmi|\s+build|\s+push)"
            ),
            "action": "ask",
            "note": "Docker/Podman 操作需要用户确认",
        },
        {
            "tool": "bash",
            "pattern": r"^(curl|wget|Invoke-WebRequest|iwr)\s+",
            "action": "ask",
            "note": "网络请求需要用户确认",
        },

        # ── 文件: 敏感文件禁止读取/修改 ──
        {
            "tool": "read_file",
            "pattern": r"(^|[\\/])\.env(\..*)?$",
            "action": "deny",
            "note": "禁止读取 .env 系列文件（含密钥）",
        },
        {
            "tool": "write_file",
            "pattern": r"(^|[\\/])\.env(\..*)?$",
            "action": "deny",
            "note": "禁止覆写 .env 文件",
        },
        {
            "tool": "edit_file",
            "pattern": r"(^|[\\/])\.env(\..*)?$",
            "action": "deny",
            "note": "禁止编辑 .env 文件",
        },
        {
            "tool": "read_file",
            "pattern": r"\.pem$",
            "action": "deny",
            "note": "禁止读取私钥文件",
        },
        {
            "tool": "read_file",
            "pattern": r"(^|[\\/])(id_rsa|id_ed25519|id_ecdsa)$",
            "action": "deny",
            "note": "禁止读取 SSH 私钥",
        },
        {
            "tool": "write_file",
            "pattern": r"(^|[\\/])\.git[\\/]config$",
            "action": "deny",
            "note": "禁止修改 git 配置",
        },
        {
            "tool": "edit_file",
            "pattern": r"(^|[\\/])\.git[\\/]config$",
            "action": "deny",
            "note": "禁止修改 git 配置",
        },
    ]


# ── 规则引擎 ──────────────────────────────────────────────────────


class RulesEngine:
    """Layer 3: 细粒度权限规则引擎。

    规则按顺序匹配，首次命中即停止。
    支持用户自定义规则插入到内置规则之前或之后。

    使用方式:
        engine = RulesEngine(user_rules=[...], prepend=False)
        result = engine.check(tool_name, params)
        # result 可能是 ALLOW / BLOCK / ASK，或 None（无匹配）
    """

    def __init__(
        self,
        user_rules: list[dict] | None = None,
        prepend: bool = False,
    ):
        rule_dicts = _builtin_rules()

        if user_rules:
            if prepend:
                rule_dicts = list(user_rules) + rule_dicts
            else:
                rule_dicts = rule_dicts + list(user_rules)

        self._rules: list[_PermissionRule] = []
        for r in rule_dicts:
            self._rules.append(_PermissionRule(
                tool=r.get("tool", "*"),
                pattern=r.get("pattern", ""),
                action=r.get("action", "ask"),
                note=r.get("note", ""),
            ))

    def check(
        self,
        tool_name: str,
        params: dict,
    ) -> PermissionResult | None:
        """按顺序匹配规则，首次命中即停止。

        Args:
            tool_name: 工具名称。
            params: 工具参数字典。

        Returns:
            PermissionResult 如果规则匹配，None 如果无规则匹配。
        """
        target = self._extract_target(tool_name, params)
        if target is None:
            return None

        for rule in self._rules:
            if rule.matches(tool_name, target):
                return self._to_result(rule)

        return None

    @staticmethod
    def _extract_target(tool_name: str, params: dict) -> str | None:
        """从工具参数中提取规则匹配目标字符串。"""
        if tool_name == "bash":
            return params.get("command", "")
        elif tool_name in ("write_file", "edit_file", "read_file"):
            return params.get("file_path", "")
        elif tool_name in ("glob", "grep"):
            return params.get("pattern", "")
        return None

    def _to_result(self, rule: _PermissionRule) -> PermissionResult:
        """将匹配到的规则转换为 PermissionResult。"""
        action_map = {
            "allow": Verdict.ALLOW,
            "deny": Verdict.BLOCK,
            "ask": Verdict.ASK,
        }
        verdict = action_map.get(rule.action, Verdict.ASK)

        risk = "medium"
        if rule.action == "deny":
            risk = "high"

        reason = f"规则: {rule.note}" if rule.note else f"规则匹配: action={rule.action}"
        if rule.action == "deny":
            reason = f"[规则拦截] {rule.note}" if rule.note else "[规则拦截]"

        return PermissionResult(
            verdict=verdict,
            reason=reason,
            risk_level=risk,
            source_layer=3,
            matched_rule=rule.note,
            summary=rule.note,
        )
