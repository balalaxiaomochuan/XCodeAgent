"""MCP 管理器：多 Server 生命周期管理和工具注册。"""

from __future__ import annotations

import sys
from pathlib import Path

from xcodeagent.mcp.client import MCPClient, MCPClientError
from xcodeagent.mcp.config import MCPServerConfig
from xcodeagent.mcp.transport import HttpTransport, StdioTransport, TransportError
from xcodeagent.tools.base import BaseTool, ToolResult


class MCPTool(BaseTool):
    """将 MCP 远程工具包装为 XCodeAgent 的 BaseTool 接口。

    Agent 通过 ToolRegistry 调用时完全无感——和调用内置工具一样。
    """

    category = "read"  # MCP 工具默认为只读

    def __init__(
        self,
        tool_name: str,
        tool_description: str,
        parameters: dict,
        client: MCPClient,
        project_root: Path,
    ):
        super().__init__(project_root)
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._parameters_schema = parameters
        self._client = client

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return self._tool_description

    @property
    def parameters(self) -> dict:
        return self._parameters_schema

    async def execute(self, **kwargs) -> ToolResult:
        return await self._client.call_tool(self._tool_name, kwargs)


class MCPManager:
    """管理多个 MCP Server 的连接生命周期和工具发现。

    用法：
        manager = MCPManager(servers_config, project_root)
        await manager.start()                # 连接所有 Server，发现工具
        tools = manager.get_tools()          # 获取 BaseTool 列表
        # ... 注册到 ToolRegistry ...
        await manager.stop()                 # 关闭所有连接
    """

    def __init__(
        self,
        servers: list[MCPServerConfig],
        project_root: str | Path = ".",
    ):
        self._servers = servers
        self._project_root = Path(project_root).resolve()
        self._clients: list[MCPClient] = []
        self._tools: list[BaseTool] = []

    @property
    def servers(self) -> list[MCPServerConfig]:
        return self._servers

    async def start(self) -> None:
        """启动所有 MCP Server 连接并发现工具。

        单个 Server 连接失败仅输出 warning，不影响其他 Server。
        """
        for server_config in self._servers:
            try:
                transport = self._create_transport(server_config)
                client = MCPClient(server_config.name, transport)

                await client.connect()
                tool_defs = await client.list_tools()

                self._clients.append(client)

                for td in tool_defs:
                    tool = MCPTool(
                        tool_name=td.name,
                        tool_description=td.description,
                        parameters=td.parameters,
                        client=client,
                        project_root=self._project_root,
                    )
                    self._tools.append(tool)

                print(
                    f"[MCP] 已连接 Server '{server_config.name}'，"
                    f"发现 {len(tool_defs)} 个工具"
                )

            except (TransportError, MCPClientError, OSError) as e:
                print(
                    f"[WARNING] MCP Server '{server_config.name}' 连接失败: {e}",
                    file=sys.stderr,
                )
            except Exception as e:
                print(
                    f"[WARNING] MCP Server '{server_config.name}' 意外的错误: {e}",
                    file=sys.stderr,
                )

        if self._tools:
            names = [t.name for t in self._tools]
            print(f"[MCP] 共发现 {len(self._tools)} 个 MCP 工具: {', '.join(names)}")
        elif self._servers:
            print("[MCP] 没有可用的 MCP 工具")

    def get_tools(self) -> list[BaseTool]:
        """返回所有已发现的 MCP 工具（BaseTool 列表）。"""
        return self._tools

    async def stop(self) -> None:
        """关闭所有 MCP 连接。"""
        for client in self._clients:
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()
        self._tools.clear()

    @staticmethod
    def _create_transport(config: MCPServerConfig):
        """根据配置创建对应的传输层实例。"""
        if config.transport_type == "stdio":
            if config.command is None:
                raise TransportError(f"stdio Server '{config.name}' 缺少 command")
            return StdioTransport(
                command=config.command,
                args=config.args,
                env=config.env,
            )
        elif config.transport_type == "http":
            if config.url is None:
                raise TransportError(f"http Server '{config.name}' 缺少 url")
            return HttpTransport(
                url=config.url,
                headers=config.headers,
            )
        else:
            raise TransportError(
                f"Server '{config.name}' 未指定 command 或 url"
            )
