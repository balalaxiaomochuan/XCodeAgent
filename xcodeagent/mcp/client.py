"""MCP 客户端：单个 Server 的完整会话管理。"""

from __future__ import annotations

import json

from xcodeagent.mcp.protocol import (
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPToolDef,
)
from xcodeagent.mcp.transport import MCPTransport, TransportError
from xcodeagent.tools.base import ToolResult


class MCPClientError(Exception):
    """MCP 客户端错误。"""
    pass


class MCPClient:
    """管理单个 MCP Server 的完整会话。

    生命周期：connect() → list_tools() → call_tool()*n → close()

    协议流程：
    1. 发送 initialize 请求，获取服务器能力
    2. 发送 initialized 通知，完成握手
    3. 发送 tools/list 请求，获取工具列表
    4. 按需发送 tools/call 请求，调用工具
    """

    def __init__(self, server_name: str, transport: MCPTransport):
        self._name = server_name
        self._transport = transport
        self._next_id = 1
        self._tools: dict[str, MCPToolDef] = {}

    @property
    def server_name(self) -> str:
        return self._name

    # ── 会话管理 ─────────────────────────────────────────────

    async def connect(self) -> None:
        """建立连接并完成 MCP 握手。"""
        await self._transport.connect()

        # Step 1: initialize
        init_req = JSONRPCRequest(
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "XCodeAgent",
                    "version": "0.1.0",
                },
            },
            id=self._next_id,
        )
        self._next_id += 1

        resp = await self._transport.send(init_req)
        if resp.is_error:
            err = resp.error
            raise MCPClientError(
                f"initialize 失败: [{err.code}] {err.message}"
            )

        # Step 2: initialized notification
        notif = JSONRPCNotification(method="notifications/initialized")
        await self._transport.send_notification(notif)

    async def list_tools(self) -> list[MCPToolDef]:
        """获取 Server 提供的工具列表。"""
        req = JSONRPCRequest(
            method="tools/list",
            params={},
            id=self._next_id,
        )
        self._next_id += 1

        resp = await self._transport.send(req)
        if resp.is_error:
            err = resp.error
            raise MCPClientError(
                f"tools/list 失败: [{err.code}] {err.message}"
            )

        tools_data = resp.result.get("tools", []) if isinstance(resp.result, dict) else []
        tools = [MCPToolDef.from_dict(t) for t in tools_data]
        self._tools = {t.name: t for t in tools}
        return tools

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        """调用指定工具。"""
        req = JSONRPCRequest(
            method="tools/call",
            params={
                "name": name,
                "arguments": arguments,
            },
            id=self._next_id,
        )
        self._next_id += 1

        try:
            resp = await self._transport.send(req)
        except TransportError as e:
            return ToolResult(success=False, error=f"MCP 传输错误: {e}")

        if resp.is_error:
            err = resp.error
            return ToolResult(
                success=False,
                error=f"MCP 错误 [{err.code}]: {err.message}",
            )

        # 解析 tools/call 响应
        # MCP 返回 content 数组，每项有 type ("text"/"image"/"resource") 和 text/data
        result = resp.result
        if isinstance(result, dict):
            content = result.get("content", [])
            is_error = result.get("isError", False)

            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif isinstance(item, dict) and item.get("type") == "resource":
                    text_parts.append(f"[Resource: {item.get('resource', {})}]")
                elif isinstance(item, dict) and item.get("type") == "image":
                    text_parts.append("[Image data]")

            output = "\n".join(text_parts) if text_parts else json.dumps(result, ensure_ascii=False)

            return ToolResult(
                success=not is_error,
                output=output,
                error=f"MCP 工具 '{name}' 返回错误" if is_error else None,
            )

        return ToolResult(success=True, output=str(result))

    async def close(self) -> None:
        """关闭连接。"""
        await self._transport.close()
