"""MCP 配置：数据类、环境变量展开、两层合并。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPServerConfig:
    """单个 MCP Server 配置。

    两种类型：
    - stdio: 需要 command、可选 args/env
    - http: 需要 url、可选 headers
    """
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def transport_type(self) -> str:
        if self.command:
            return "stdio"
        if self.url:
            return "http"
        return "unknown"

    @classmethod
    def from_dict(cls, name: str, data: dict) -> MCPServerConfig:
        command = data.get("command")
        args = data.get("args", [])
        env = _expand_dict(data.get("env", {}))
        url = data.get("url")
        headers = _expand_dict(data.get("headers", {}))

        if command is None and url is None:
            raise ValueError(
                f"MCP Server '{name}' 必须指定 'command'（stdio 类型）或 'url'（http 类型）"
            )

        return cls(
            name=name,
            command=command,
            args=args,
            env=env,
            url=url,
            headers=headers,
        )


# ── 环境变量展开 ───────────────────────────────────────────────

_VAR_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _expand_string(value: str) -> str:
    """展开字符串中的 ${VAR} 和 $VAR 环境变量。"""
    def _replace(match):
        var_name = match.group(1) or match.group(2)
        return os.environ.get(var_name, "")
    return _VAR_RE.sub(_replace, value)


def _expand_dict(data: dict[str, str]) -> dict[str, str]:
    """展开字典中所有值的环境变量。"""
    return {k: _expand_string(v) for k, v in data.items()}


# ── 配置合并 ───────────────────────────────────────────────────


def merge_mcp_servers(
    user_servers: dict[str, dict] | None,
    project_servers: dict[str, dict] | None,
) -> dict[str, dict]:
    """合并用户级和项目级 MCP Server 配置。

    同名 Server 项目级覆盖用户级。

    Args:
        user_servers: 用户级 ~/.xcodeagent/config.json 中的 mcpServers。
        project_servers: 项目级 ./config.json 中的 mcpServers。

    Returns:
        合并后的 Server 字典。
    """
    merged: dict[str, dict] = {}
    if user_servers:
        merged.update(user_servers)
    if project_servers:
        merged.update(project_servers)
    return merged


def parse_mcp_servers(servers: dict[str, Any] | None) -> list[MCPServerConfig]:
    """解析 mcpServers 配置为 MCPServerConfig 列表。

    Args:
        servers: config.json 中 mcpServers 段的字典。

    Returns:
        MCPServerConfig 列表。解析失败的 Server 跳过并输出 warning。
    """
    if not servers:
        return []

    result: list[MCPServerConfig] = []
    for name, data in servers.items():
        if not isinstance(data, dict):
            import sys
            print(f"[WARNING] MCP Server '{name}' 配置无效，已跳过", file=sys.stderr)
            continue
        try:
            result.append(MCPServerConfig.from_dict(name, data))
        except ValueError as e:
            import sys
            print(f"[WARNING] {e}，已跳过", file=sys.stderr)

    return result
