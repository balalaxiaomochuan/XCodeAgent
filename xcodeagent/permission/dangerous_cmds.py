"""Layer 1: 高危命令拦截 — 基于黑名单的硬拦截，不可绕过。

对 Bash 命令字符串做正则匹配，命中即返回 BLOCK。
同时按管道符 / 分隔符分割命令，逐段检查。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from xcodeagent.permission import PermissionResult, Verdict


@dataclass
class _DangerousPattern:
    """一条高危命令匹配模式（内部使用）。"""
    pattern: str
    category: str
    description: str
    severity: str       # "critical" | "high"
    compiled: re.Pattern = None

    def __post_init__(self):
        self.compiled = re.compile(self.pattern)


# ── 命令分割 ─────────────────────────────────────────────────────


def _split_command(command: str) -> list[str]:
    """按管道/分隔符拆分命令，返回所有需要检查的段。

    返回的第一个元素是完整命令字符串，后续是各管道段。
    完整命令也参与匹配（用于检测 fork 炸弹等需要完整上下文的模式）。
    """
    segments = [command.strip()]

    # 按常见的命令分隔符拆分
    # 注意: 需要保留被切割后的独立段
    parts = re.split(r'[|;&]|\$\$|&&|\|\|', command)
    for part in parts:
        stripped = part.strip()
        if stripped:
            segments.append(stripped)

    # 额外处理 $() 和 `` 命令替换
    # 提取 $(...) 和 `...` 中的内容作为额外段检查
    subshell_patterns = [
        re.compile(r'\$\(([^)]+)\)'),
        re.compile(r'`([^`]+)`'),
    ]
    for pattern in subshell_patterns:
        for match in pattern.finditer(command):
            inner = match.group(1).strip()
            if inner:
                segments.append(inner)

    return segments


# ── 内置黑名单 ───────────────────────────────────────────────────


_BUILTIN_PATTERNS: list[dict] = [
    # ═══════════════════════════════════════════════════════════
    # 1. 不可逆数据破坏
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r"rm\s+-r[f]\s+(/|/\*|~/)",
        "category": "不可逆数据破坏",
        "description": "递归强制删除根目录或用户主目录",
        "severity": "critical",
    },
    {
        "pattern": r"^rm\s+-rf\s+/",
        "category": "不可逆数据破坏",
        "description": "强制递归删除根目录",
        "severity": "critical",
    },
    {
        "pattern": r"rm\s+-rf\s+~(/|\s|$)",
        "category": "不可逆数据破坏",
        "description": "强制递归删除用户主目录",
        "severity": "critical",
    },
    {
        "pattern": r"\bmkfs\.",
        "category": "不可逆数据破坏",
        "description": "格式化文件系统",
        "severity": "critical",
    },
    {
        "pattern": r"\bmke2fs\b",
        "category": "不可逆数据破坏",
        "description": "创建 ext2/3/4 文件系统（格式化）",
        "severity": "critical",
    },
    {
        "pattern": r"dd\s+.*of=/dev/(sd[a-z]|nvme\d|disk|mmcblk)",
        "category": "不可逆数据破坏",
        "description": "直接写入块设备（破坏分区/数据）",
        "severity": "critical",
    },
    {
        "pattern": r"\bwipefs\b",
        "category": "不可逆数据破坏",
        "description": "擦除文件系统签名",
        "severity": "critical",
    },
    {
        "pattern": r"\bshred\s+/dev/",
        "category": "不可逆数据破坏",
        "description": "安全擦除设备内容",
        "severity": "critical",
    },
    {
        "pattern": r"\bformat\s+[A-Za-z]:",
        "category": "不可逆数据破坏",
        "description": "Windows 格式化磁盘",
        "severity": "critical",
    },

    # ═══════════════════════════════════════════════════════════
    # 2. 系统级破坏
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r"chmod\s+(-R\s+)?777\s+/",
        "category": "系统级破坏",
        "description": "开放根目录所有权限",
        "severity": "critical",
    },
    {
        "pattern": r"chown\s+-R\s+\S+\s+/",
        "category": "系统级破坏",
        "description": "递归篡改根目录所有者",
        "severity": "critical",
    },
    {
        "pattern": r"\bmodprobe\s+-r\b",
        "category": "系统级破坏",
        "description": "卸载内核模块",
        "severity": "high",
    },
    {
        "pattern": r"\binsmod\b",
        "category": "系统级破坏",
        "description": "加载内核模块",
        "severity": "high",
    },
    {
        "pattern": r"\brmmod\b",
        "category": "系统级破坏",
        "description": "卸载内核模块",
        "severity": "high",
    },

    # ═══════════════════════════════════════════════════════════
    # 3. 系统进程与资源劫持
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r":\(\)\s*\{\s*:\|:",
        "category": "资源劫持",
        "description": "Fork 炸弹（经典 shell 函数炸弹）",
        "severity": "critical",
    },
    {
        "pattern": r"\bwhile\s+true\s*;\s*do\b",
        "category": "资源劫持",
        "description": "无限循环 fork（潜在炸弹）",
        "severity": "high",
    },
    {
        "pattern": r"\b(shutdown|reboot|halt|poweroff)\b",
        "category": "资源劫持",
        "description": "关机/重启命令",
        "severity": "critical",
    },
    {
        "pattern": r"\binit\s+[06]\b",
        "category": "资源劫持",
        "description": "切换运行级别（关机/重启）",
        "severity": "critical",
    },
    {
        "pattern": r"\bkill\s+-9\s+-1\b",
        "category": "资源劫持",
        "description": "向所有进程发送 SIGKILL",
        "severity": "critical",
    },
    {
        "pattern": r"\bpkill\s+-9\b",
        "category": "资源劫持",
        "description": "强制批量杀进程",
        "severity": "high",
    },
    {
        "pattern": r"\bkillall\s+-9\b",
        "category": "资源劫持",
        "description": "强制批量杀进程 (killall)",
        "severity": "high",
    },
    {
        "pattern": r"\byes\s*>\s*/dev/null",
        "category": "资源劫持",
        "description": "填满磁盘（yes 输出重定向）",
        "severity": "high",
    },
    {
        "pattern": r"\bfallocate\b",
        "category": "资源劫持",
        "description": "预分配大文件（可能填满磁盘）",
        "severity": "high",
    },

    # ═══════════════════════════════════════════════════════════
    # 4. 系统配置篡改
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r"(>|>>)\s*/etc/(passwd|shadow|group|gshadow)\b",
        "category": "系统配置篡改",
        "description": "覆写/追加系统账户文件",
        "severity": "critical",
    },
    {
        "pattern": r"(>|>>)\s*/etc/hosts\b",
        "category": "系统配置篡改",
        "description": "修改 hosts 文件（DNS 劫持）",
        "severity": "high",
    },
    {
        "pattern": r"\bsystemctl\s+enable\b",
        "category": "系统配置篡改",
        "description": "启用 systemd 服务（持久化）",
        "severity": "high",
    },
    {
        "pattern": r"(>|>>)\s*/etc/systemd/system/",
        "category": "系统配置篡改",
        "description": "写入 systemd 单元文件",
        "severity": "high",
    },
    {
        "pattern": r"\bcrontab\b",
        "category": "系统配置篡改",
        "description": "修改 crontab 定时任务",
        "severity": "high",
    },
    {
        "pattern": r"(>|>>)\s*/etc/crontab\b",
        "category": "系统配置篡改",
        "description": "覆写/追加系统 crontab",
        "severity": "high",
    },
    {
        "pattern": r"(>|>>)\s*/etc/ssh/sshd_config\b",
        "category": "系统配置篡改",
        "description": "修改 SSH 服务端配置",
        "severity": "critical",
    },
    {
        "pattern": r"(>|>>)\s*\~?/\.ssh/authorized_keys",
        "category": "系统配置篡改",
        "description": "修改 SSH 授权密钥（后门）",
        "severity": "critical",
    },
    {
        "pattern": r"(date\s+-s|hwclock\s+--set|timedatectl\s+set-time)",
        "category": "系统配置篡改",
        "description": "修改系统时钟",
        "severity": "high",
    },

    # ═══════════════════════════════════════════════════════════
    # 5. 提权与权限提升
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r"\bsudo\b",
        "category": "提权操作",
        "description": "使用 sudo 提升权限",
        "severity": "critical",
    },
    {
        "pattern": r"\bsu\s+(-|root)\b",
        "category": "提权操作",
        "description": "切换用户身份",
        "severity": "critical",
    },
    {
        "pattern": r"\bpkexec\b",
        "category": "提权操作",
        "description": "PolicyKit 权限提升",
        "severity": "critical",
    },
    {
        "pattern": r"\bdoas\b",
        "category": "提权操作",
        "description": "OpenBSD 权限提升",
        "severity": "critical",
    },
    {
        "pattern": r"\bvisudo\b",
        "category": "提权操作",
        "description": "编辑 sudoers 配置（持久化提权）",
        "severity": "critical",
    },
    {
        "pattern": r"(>|>>)\s*/etc/sudoers",
        "category": "提权操作",
        "description": "覆写/追加 sudoers 配置（持久化提权）",
        "severity": "critical",
    },
    {
        "pattern": r"(>|>>)\s*/etc/sudoers\.d/",
        "category": "提权操作",
        "description": "写入 sudoers.d 配置",
        "severity": "critical",
    },

    # ═══════════════════════════════════════════════════════════
    # 6. 系统后门与隐藏
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r"\bchattr\b",
        "category": "系统后门",
        "description": "修改文件扩展属性（隐藏文件）",
        "severity": "high",
    },
    {
        "pattern": r"(>|truncate\s+-s\s+0)\s+/var/log/",
        "category": "系统后门",
        "description": "清空/截断系统日志",
        "severity": "high",
    },
    {
        "pattern": r"\bsetenforce\s+0\b",
        "category": "系统后门",
        "description": "禁用 SELinux 强制模式",
        "severity": "critical",
    },
    {
        "pattern": r"\baa-disable\b",
        "category": "系统后门",
        "description": "禁用 AppArmor 配置文件",
        "severity": "high",
    },
    {
        "pattern": r"\bsystemctl\s+disable\s+rsyslog",
        "category": "系统后门",
        "description": "禁用系统日志服务",
        "severity": "high",
    },

    # ═══════════════════════════════════════════════════════════
    # 7. 网络攻击与信息窃取
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r"/dev/tcp/",
        "category": "网络攻击",
        "description": "bash 反弹 shell（/dev/tcp）",
        "severity": "critical",
    },
    {
        "pattern": r"\bnc\s+.*-e\s+/bin/(sh|bash)",
        "category": "网络攻击",
        "description": "netcat 反弹 shell",
        "severity": "critical",
    },
    {
        "pattern": r"python.*socket\.(socket|connect)",
        "category": "网络攻击",
        "description": "Python 反弹 shell",
        "severity": "critical",
    },
    {
        "pattern": r"curl.*POST.*@/etc/(passwd|shadow)",
        "category": "信息窃取",
        "description": "curl 外泄系统账户文件",
        "severity": "critical",
    },
    {
        "pattern": r"wget\s+.*--post-file=",
        "category": "信息窃取",
        "description": "wget 外泄文件内容",
        "severity": "high",
    },
    {
        "pattern": r"\bnmap\b",
        "category": "网络攻击",
        "description": "端口扫描工具",
        "severity": "high",
    },
    {
        "pattern": r"\bmasscan\b",
        "category": "网络攻击",
        "description": "大规模端口扫描",
        "severity": "high",
    },
    {
        "pattern": r"\btcpdump\s+-w\b",
        "category": "网络攻击",
        "description": "抓包并写入文件（流量嗅探）",
        "severity": "high",
    },
    {
        "pattern": r"\btshark\b",
        "category": "网络攻击",
        "description": "Wireshark 命令行抓包",
        "severity": "high",
    },
    {
        "pattern": r"\b(ettercap|bettercap|arpspoof)\b",
        "category": "网络攻击",
        "description": "ARP 欺骗/中间人攻击工具",
        "severity": "critical",
    },
    {
        "pattern": r"(curl|wget|Invoke-WebRequest|iwr)\s+.*\|\s*(sh|bash|python|ruby|perl)",
        "category": "网络攻击",
        "description": "curl/wget pipe to shell（经典远程代码执行）",
        "severity": "critical",
    },

    # ═══════════════════════════════════════════════════════════
    # 8. Git 危险操作
    # ═══════════════════════════════════════════════════════════
    {
        "pattern": r"git\s+push\s+.*(--force|-f)\s+.*\b(main|master)\b",
        "category": "Git 危险操作",
        "description": "强制推送 main/master 分支（破坏远程主分支历史）",
        "severity": "critical",
    },
    {
        "pattern": r"git\s+push\s+.*--delete\s+.*\b(main|master)\b",
        "category": "Git 危险操作",
        "description": "删除远程 main/master 分支",
        "severity": "critical",
    },
    {
        "pattern": r"git\s+reset\s+--hard\s+HEAD~(\d+)",
        "category": "Git 危险操作",
        "description": "硬重置丢弃大量本地提交",
        "severity": "high",
    },
    {
        "pattern": r"git\s+reset\s+--hard\s+origin/",
        "category": "Git 危险操作",
        "description": "硬重置丢弃所有本地修改",
        "severity": "high",
    },
]


# ── 高危命令过滤器 ──────────────────────────────────────────────


class DangerousCommandFilter:
    """Layer 1: 高危命令黑名单过滤器。

    检查 Bash 命令是否命中内置或用户追加的黑名单规则。
    命中即返回 BLOCK，不可绕过。

    使用方式:
        f = DangerousCommandFilter()
        result = f.check("rm -rf /")
        # result.verdict == Verdict.BLOCK
    """

    def __init__(self, extra_patterns: list[dict] | None = None):
        self._patterns: list[_DangerousPattern] = []

        # 加载内置黑名单
        for p in _BUILTIN_PATTERNS:
            self._patterns.append(_DangerousPattern(**p))

        # 追加用户自定义黑名单
        if extra_patterns:
            for p in extra_patterns:
                self._patterns.append(_DangerousPattern(
                    pattern=p.get("pattern", ""),
                    category=p.get("category", "用户追加"),
                    description=p.get("description", ""),
                    severity=p.get("severity", "high"),
                ))

    def check(self, command: str) -> PermissionResult:
        """检查命令是否命中高危黑名单。

        Args:
            command: 完整的 shell 命令字符串。

        Returns:
            BLOCK 如果命中黑名单，ALLOW 如果安全。
        """
        if not command or not command.strip():
            return PermissionResult.allowed(reason="空命令", source_layer=1)

        segments = _split_command(command)

        for dp in self._patterns:
            for segment in segments:
                if dp.compiled.search(segment):
                    return PermissionResult.blocked(
                        reason=(
                            f"[高危命令拦截] {dp.description}\n"
                            f"  匹配模式: {dp.pattern}\n"
                            f"  风险类别: {dp.category}\n"
                            f"  严重级别: {dp.severity}"
                        ),
                        risk_level=dp.severity,
                        source_layer=1,
                    )

        return PermissionResult.allowed(reason="Layer 1 通过", source_layer=1)
