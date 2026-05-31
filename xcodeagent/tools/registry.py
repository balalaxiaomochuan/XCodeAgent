"""工具注册中心：集中管理所有工具实例。"""

from __future__ import annotations

from xcodeagent.tools.base import BaseTool


class ToolRegistry:
    """集中注册和查找工具。"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具实例。同名工具重复注册抛出 ValueError。"""
        if tool.name in self._tools:
            raise ValueError(f"工具 '{tool.name}' 已注册，不能重复注册")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """按名称查找工具。未找到时抛出 KeyError。"""
        if name not in self._tools:
            raise KeyError(f"工具 '{name}' 未注册")
        return self._tools[name]

    def list_all(self) -> list[BaseTool]:
        """返回所有已注册工具。"""
        return list(self._tools.values())

    def get_definitions(self) -> list[dict]:
        """生成 Anthropic tool use 格式的工具定义列表。

        每个工具定义格式：
            {
                "name": "...",
                "description": "...",
                "input_schema": { ... }   # 来自 BaseTool.parameters
            }
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in self._tools.values()
        ]
