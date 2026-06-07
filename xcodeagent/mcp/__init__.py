"""MCP (Model Context Protocol) 客户端模块。

让 XCodeAgent 自动发现并注册外部 MCP Server 提供的工具。
支持 stdio（子进程管道）和 Streamable HTTP 两种传输方式。
"""

from __future__ import annotations

from xcodeagent.mcp.config import (
    MCPServerConfig,
    merge_mcp_servers,
    parse_mcp_servers,
)
from xcodeagent.mcp.manager import MCPManager, MCPTool

__all__ = [
    "MCPManager",
    "MCPTool",
    "MCPServerConfig",
    "merge_mcp_servers",
    "parse_mcp_servers",
]
