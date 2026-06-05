"""命令分析器：将工具调用参数翻译为人类可读的操作说明。

用于权限确认对话框中展示"这个命令会做什么"和"执行后的影响"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CommandAnalysis:
    """命令分析结果。"""
    summary: str        # 一句话说明这个命令做什么
    impact: str         # 执行后的具体影响/后果
    risk_hint: str = ""  # 额外风险提示（可选）


# ── Bash 命令模式 ─────────────────────────────────────────────


def _analyze_bash(command: str) -> CommandAnalysis:
    """分析 Bash 命令并返回人类可读的说明。"""
    cmd = command.strip()

    # ── Git 操作 ──
    if _match(r"^git\s+status\b", cmd):
        return CommandAnalysis(
            summary="查看 Git 仓库当前状态",
            impact="列出所有已修改/未跟踪的文件（只读操作，无副作用）",
        )
    if _match(r"^git\s+diff\b", cmd):
        return CommandAnalysis(
            summary="查看 Git 仓库中文件的详细差异",
            impact="显示文件内容的具体变更（只读操作，无副作用）",
        )
    if _match(r"^git\s+log\b", cmd):
        return CommandAnalysis(
            summary="查看 Git 提交历史记录",
            impact="显示提交日志（只读操作，无副作用）",
        )
    if _match(r"^git\s+branch\b", cmd):
        return CommandAnalysis(
            summary="查看/管理 Git 分支",
            impact="列出分支信息，如果带 -d/-D 会删除分支",
        )
    if _match(r"^git\s+add\s", cmd):
        files = _git_files(cmd)
        return CommandAnalysis(
            summary="暂存文件变更到 Git 索引",
            impact=f"将 {files} 标记为待提交（不会改变文件内容，只是 Git 记录）",
        )
    if _match(r"^git\s+commit\b", cmd):
        return CommandAnalysis(
            summary="提交 Git 暂存区的内容",
            impact="创建一条新的 Git 提交记录（修改了本地仓库历史）",
        )
    if _match(r"^git\s+checkout\b", cmd):
        return CommandAnalysis(
            summary="切换 Git 分支或恢复文件",
            impact="切换当前工作目录的分支，文件内容会变为目标分支的版本",
        )
    if _match(r"^git\s+push\b", cmd):
        return CommandAnalysis(
            summary="推送本地 Git 提交到远程仓库",
            impact="将本地提交同步到远程服务器（其他人将看到这些变更）",
        )
    if _match(r"^git\s+pull\b", cmd):
        return CommandAnalysis(
            summary="从远程仓库拉取并合并最新代码",
            impact="下载远程变更并合并到当前分支（可能产生合并冲突）",
        )
    if _match(r"^git\s+fetch\b", cmd):
        return CommandAnalysis(
            summary="从远程仓库下载最新提交",
            impact="仅下载远程数据，不会修改本地工作目录（只读操作）",
        )
    if _match(r"^git\s+merge\b", cmd):
        return CommandAnalysis(
            summary="合并 Git 分支",
            impact="将一个分支的变更合并到当前分支（可能产生冲突）",
        )
    if _match(r"^git\s+rebase\b", cmd):
        return CommandAnalysis(
            summary="变基 Git 分支",
            impact="重写提交历史，将当前分支的提交移到目标分支之上（会改变 commit SHA）",
        )
    if _match(r"^git\s+stash\b", cmd):
        return CommandAnalysis(
            summary="暂存当前工作区修改",
            impact="将未提交的修改临时保存起来，工作区恢复干净状态",
        )
    if _match(r"^git\s+reset\s", cmd):
        return CommandAnalysis(
            summary="重置 Git 索引或工作区",
            impact="撤销暂存或提交，可能丢失未提交的修改（取决于 --hard/--soft）",
        )
    if _match(r"^git\s+clone\b", cmd):
        return CommandAnalysis(
            summary="克隆 Git 仓库到本地",
            impact="从远程下载完整的仓库副本到本地新目录",
        )
    if _match(r"^git\s+remote\b", cmd):
        return CommandAnalysis(
            summary="管理 Git 远程仓库地址",
            impact="查看/添加/修改远程仓库的 URL",
        )
    if _match(r"^git\s+clean\b", cmd):
        return CommandAnalysis(
            summary="清理 Git 仓库中未跟踪的文件",
            impact="删除所有未被 Git 跟踪的文件（不可恢复！）",
        )
    if _match(r"^git\s+init\b", cmd):
        return CommandAnalysis(
            summary="在当前目录初始化 Git 仓库",
            impact="创建 .git 目录，开始版本控制",
        )

    # ── 测试/检查 ──
    if _match(r"^(pytest|python\s+-m\s+pytest)", cmd):
        return CommandAnalysis(
            summary="运行 Python 项目测试",
            impact="执行所有测试用例并输出结果，不会修改源代码",
        )
    if _match(r"^(mypy|ruff|black|isort|flake8|pylint)", cmd):
        tool = cmd.split()[0]
        return CommandAnalysis(
            summary=f"运行代码质量检查工具 {tool}",
            impact=f"检查代码风格/类型（{'会直接修改代码' if tool in ('black', 'isort') else '只读检查，不会修改代码'}）",
        )
    if _match(r"^(go\s+test|cargo\s+test|jest|vitest|rspec)", cmd):
        return CommandAnalysis(
            summary="运行项目测试",
            impact="执行测试用例并输出结果，不会修改源代码",
        )

    # ── 文件读取 ──
    if _match(r"^(cat|type)\s", cmd):
        file = cmd.split(None, 1)[1] if len(cmd.split(None, 1)) > 1 else "文件"
        return CommandAnalysis(
            summary=f"查看文件 {file} 的内容",
            impact="将文件内容输出到终端（只读操作，无副作用）",
        )
    if _match(r"^head\s", cmd):
        return CommandAnalysis(
            summary="查看文件开头部分",
            impact="显示文件前几行内容（只读操作，无副作用）",
        )
    if _match(r"^tail\s", cmd):
        return CommandAnalysis(
            summary="查看文件末尾部分",
            impact="显示文件最后几行内容（只读操作，无副作用）",
        )
    if _match(r"^(ls|dir)\s", cmd):
        return CommandAnalysis(
            summary="列出目录中的文件和子目录",
            impact="显示文件名列表（只读操作，无副作用）",
        )
    if _match(r"^Get-Content\b", cmd):
        file = _extract_path(cmd)
        return CommandAnalysis(
            summary=f"读取文件 {file} 的内容",
            impact="将文件内容输出（只读操作，不会修改文件）",
        )

    # ── 文件写入/删除 ──
    if _match(r"^rm\s", cmd):
        target = cmd.split(None, 1)[1] if len(cmd.split(None, 1)) > 1 else "文件"
        return CommandAnalysis(
            summary=f"删除 {target}",
            impact="永久删除文件或目录（不可恢复！请确认是否真的要删除）",
            risk_hint="如果目标是重要的项目文件，删除后无法恢复",
        )
    if _match(r"^(mv|move)\s", cmd):
        return CommandAnalysis(
            summary="移动/重命名文件",
            impact="改变文件的位置或名称",
        )
    if _match(r"^(cp|copy)\s", cmd):
        return CommandAnalysis(
            summary="复制文件或目录",
            impact="创建文件的副本（不会修改原始文件）",
        )
    if _match(r"^(echo|printf)\s.*[>]", cmd):
        return CommandAnalysis(
            summary="写入内容到文件",
            impact="向文件写入新内容，可能覆盖原有内容",
        )
    if _match(r"^Set-Content\b", cmd):
        file = _extract_path(cmd)
        return CommandAnalysis(
            summary=f"写入内容到文件 {file}",
            impact="覆写文件内容，原有内容将丢失",
        )
    if _match(r"^Out-File\b", cmd):
        file = _extract_path(cmd)
        return CommandAnalysis(
            summary=f"将输出写入到文件 {file}",
            impact="覆写文件内容",
        )
    if _match(r"^(mkdir|New-Item\s.*-ItemType\s+Directory)", cmd):
        return CommandAnalysis(
            summary="创建新目录",
            impact="在项目中新建一个文件夹",
        )

    # ── 进程/服务 ──
    if _match(r"^(python|python3)\s", cmd):
        script = cmd.split(None, 1)[1] if len(cmd.split(None, 1)) > 1 else "Python 脚本"
        return CommandAnalysis(
            summary=f"运行 Python 脚本: {script}",
            impact="执行 Python 代码，可能有各种副作用（取决于脚本内容）",
        )
    if _match(r"^(node|npx|tsx|ts-node|deno|bun)\s", cmd):
        script = cmd.split(None, 1)[1] if len(cmd.split(None, 1)) > 1 else "脚本"
        return CommandAnalysis(
            summary=f"运行 JS/TS 脚本: {script}",
            impact="执行 JavaScript/TypeScript 代码",
        )
    if _match(r"^(npm|yarn|pnpm)\s+run\b", cmd):
        return CommandAnalysis(
            summary="运行 package.json 中定义的脚本",
            impact="执行项目构建/开发/测试等脚本命令",
        )
    if _match(r"^(uv\s+run|poetry\s+run|pipenv\s+run|hatch\s+run)", cmd):
        return CommandAnalysis(
            summary="通过 Python 包管理器运行命令",
            impact="在项目虚拟环境中运行指定命令",
        )
    if _match(r"^(docker|podman)\s", cmd):
        return CommandAnalysis(
            summary="运行 Docker/Podman 容器命令",
            impact="启动/管理容器，可能占用系统资源（CPU、内存、端口）",
        )

    # ── 网络请求 ──
    if _match(r"^(curl|wget|Invoke-WebRequest|iwr)\s", cmd):
        url = _extract_url(cmd)
        return CommandAnalysis(
            summary=f"发送网络请求到 {url}",
            impact="从网络下载数据，可能向外部服务器发送信息",
        )
    if _match(r"^(ping|tracert|nslookup|dig)\s", cmd):
        return CommandAnalysis(
            summary="执行网络诊断命令",
            impact="向目标地址发送网络探测包（只读网络操作）",
        )

    # ── 系统信息 ──
    if _match(r"^(whoami|id|uname|hostname)\b", cmd):
        return CommandAnalysis(
            summary="查看系统信息",
            impact="显示当前用户/系统名称（只读操作，无副作用）",
        )
    if _match(r"^(ps|tasklist|top|htop)\b", cmd):
        return CommandAnalysis(
            summary="查看系统进程列表",
            impact="列出当前运行的进程（只读操作，无副作用）",
        )
    if _match(r"^(env|set|printenv|echo\s+\$)\b", cmd):
        return CommandAnalysis(
            summary="查看环境变量",
            impact="显示当前环境变量值（只读操作，但可能泄露敏感配置）",
        )
    if _match(r"^(which|where|whereis|type\s+(?!.*\.))", cmd):
        return CommandAnalysis(
            summary="查找命令的安装位置",
            impact="显示可执行文件路径（只读操作，无副作用）",
        )

    # ── 包管理 ──
    if _match(r"^(pip|pip3)\s+install\b", cmd):
        pkg = _extract_package(cmd)
        return CommandAnalysis(
            summary=f"安装 Python 包: {pkg}",
            impact=f"从 PyPI 下载并安装 {pkg}，会修改 Python 环境",
        )
    if _match(r"^(pip|pip3)\s+uninstall\b", cmd):
        return CommandAnalysis(
            summary="卸载 Python 包",
            impact="从 Python 环境中移除指定的包",
        )
    if _match(r"^(npm|yarn|pnpm)\s+(install|add)\b", cmd):
        return CommandAnalysis(
            summary="安装 Node.js/npm 包",
            impact="下载并安装包到 node_modules，可能修改 package.json",
        )

    # ── 脚本语言 ──
    if _match(r"^powershell\s+-Command\s+", cmd):
        inner = _extract_powershell_inner(cmd)
        if inner:
            sub = _analyze_bash(inner)
            return CommandAnalysis(
                summary=f"通过 PowerShell 执行: {sub.summary}",
                impact=sub.impact,
                risk_hint=sub.risk_hint,
            )
        return CommandAnalysis(
            summary="执行 PowerShell 命令",
            impact="运行 PowerShell 脚本，具体影响取决于命令内容",
        )

    # ── 默认 ──
    return CommandAnalysis(
        summary=f"执行命令: {cmd[:80]}",
        impact="影响未知（未能识别命令模式，请自行判断）",
    )


# ── 文件工具 ─────────────────────────────────────────────────


def _analyze_write_file(params: dict) -> CommandAnalysis:
    """分析 WriteFile 操作。"""
    path = params.get("file_path", "")
    content = params.get("content", "")
    size = len(content.encode("utf-8"))
    filename = path.split("/")[-1] if "/" in path else path.split("\\")[-1] if "\\" in path else path

    if path.endswith(".py"):
        file_type = "Python 源代码文件"
    elif path.endswith((".js", ".ts", ".jsx", ".tsx")):
        file_type = "JavaScript/TypeScript 文件"
    elif path.endswith((".json", ".yaml", ".yml", ".toml")):
        file_type = "配置文件"
    elif path.endswith((".md", ".rst", ".txt")):
        file_type = "文档/文本文件"
    elif path.endswith((".html", ".css", ".scss")):
        file_type = "前端代码文件"
    elif path.endswith((".env",)):
        file_type = "环境变量配置文件"
    else:
        file_type = "文件"

    return CommandAnalysis(
        summary=f"创建/覆写 {file_type}: {filename}",
        impact=f"写入 {size} 字节到 {path}，如果文件已存在将被完全覆盖",
    )


def _analyze_edit_file(params: dict) -> CommandAnalysis:
    """分析 EditFile 操作。"""
    path = params.get("file_path", "")
    edits = params.get("edits", [])
    n_edits = len(edits)
    filename = path.split("/")[-1] if "/" in path else path.split("\\")[-1] if "\\" in path else path

    return CommandAnalysis(
        summary=f"在 {filename} 中修改 {n_edits} 处代码",
        impact=f"对 {path} 执行 {n_edits} 段精确文本替换，任一匹配失败则整体回滚",
    )


# ── 统一入口 ─────────────────────────────────────────────────


def analyze_tool_call(tool_name: str, params: dict) -> CommandAnalysis:
    """分析任意工具调用，返回人类可读的操作说明。

    Args:
        tool_name: 工具名称。
        params: 工具参数字典。

    Returns:
        CommandAnalysis with summary, impact, risk_hint.
    """
    if tool_name == "bash":
        command = params.get("command", "")
        return _analyze_bash(command)
    elif tool_name == "write_file":
        return _analyze_write_file(params)
    elif tool_name == "edit_file":
        return _analyze_edit_file(params)
    else:
        return CommandAnalysis(
            summary=f"调用工具: {tool_name}",
            impact="具体影响未知",
        )


# ── 辅助函数 ─────────────────────────────────────────────────


def _match(pattern: str, text: str) -> bool:
    """检查 text 是否匹配 pattern。"""
    try:
        return bool(re.search(pattern, text, re.IGNORECASE))
    except re.error:
        return False


def _git_files(cmd: str) -> str:
    """从 git add 命令中提取目标文件。"""
    parts = cmd.split()
    # git add <files>  -- 跳过 git 和 add
    files = [p for p in parts[2:] if not p.startswith("-")]
    if not files:
        return "所有修改的文件"
    if len(files) <= 3:
        return " ".join(files)
    return f"{' '.join(files[:3])} 等 {len(files)} 个文件"


def _extract_path(cmd: str) -> str:
    """从命令中提取文件路径。"""
    parts = cmd.split()
    for p in parts[1:]:
        if not p.startswith("-") and not p.startswith("'") and not p.startswith('"'):
            # 找看起来像路径的参数
            if "/" in p or "\\" in p or "." in p:
                return p
    return "指定文件"


def _extract_url(cmd: str) -> str:
    """从网络命令中提取 URL。"""
    parts = cmd.split()
    for p in parts[1:]:
        if p.startswith("http") or "://" in p:
            return p
    return "外部地址"


def _extract_package(cmd: str) -> str:
    """从包管理命令中提取包名。"""
    parts = cmd.split()
    for i, p in enumerate(parts):
        if p in ("install", "add", "uninstall", "remove"):
            if i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                return parts[i + 1]
    return "未知包"


def _extract_powershell_inner(cmd: str) -> str | None:
    """从 PowerShell -Command 中提取内部命令。"""
    match = re.search(r'-Command\s+[`"\'](.+?)[`"\']?\s*$', cmd, re.IGNORECASE)
    if match:
        return match.group(1)
    # 尝试匹配不包含引号的情况
    idx = cmd.lower().find("-command")
    if idx >= 0:
        rest = cmd[idx + len("-command"):].strip()
        rest = rest.strip("'\"`")
        return rest
    return None
