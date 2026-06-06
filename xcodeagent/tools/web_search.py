"""WebSearch 工具：使用 DuckDuckGo 搜索网页，返回标题、摘要和链接。"""

from __future__ import annotations

import asyncio

from xcodeagent.tools.base import BaseTool, ToolResult


class WebSearch(BaseTool):
    """搜索网页，返回相关结果的标题、摘要和 URL。

    适合：查找最新信息、API 文档、技术方案、新闻等。
    不适合：读取网页内容 → 用 WebFetch；文件搜索 → 用 Glob/Grep。
    """

    name = "web_search"
    category = "read"
    description = (
        "使用 DuckDuckGo 搜索引擎搜索网页，返回相关结果的标题、摘要和 URL。"
        "何时使用：查找最新技术信息、API 文档、编程问题、新闻、库/框架用法。"
        "何时不用：读取特定网页内容 → 用 WebFetch；本地文件搜索 → 用 Glob/Grep。"
        "参数：query 为搜索关键词（必填）；region 为地区代码如 cn、us（可选，默认 cn）；"
        "max_results 为最大结果数（可选，默认 10，上限 20）。"
        "返回格式：每条结果为 '序号. 标题\\n   摘要\\n   URL'。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如 'Python asyncio usage'",
                },
                "region": {
                    "type": "string",
                    "description": "地区代码，如 'cn'（中文结果优先）、'us'（英文结果），默认 'cn'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数，默认 10，上限 20",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        region: str = "cn",
        max_results: int = 10,
    ) -> ToolResult:
        if not query.strip():
            return ToolResult(success=False, error="搜索关键词不能为空")

        max_results = min(max(max_results, 1), 20)

        try:
            results = await asyncio.to_thread(
                self._search, query.strip(), region, max_results
            )
        except ImportError:
            return ToolResult(
                success=False,
                error="缺少 ddgs 依赖，请运行: uv sync",
            )
        except Exception as e:
            return ToolResult(success=False, error=f"搜索失败: {e}")

        if not results:
            return ToolResult(success=True, output=f"未找到与 '{query}' 相关的结果")

        output = f"搜索 '{query}' 的结果（{len(results)} 条）:\n\n"
        for i, r in enumerate(results, 1):
            output += f"{i}. {r['title']}\n"
            output += f"   {r['snippet']}\n"
            output += f"   {r['href']}\n\n"

        return ToolResult(success=True, output=output.rstrip())

    @staticmethod
    def _search(query: str, region: str, max_results: int) -> list[dict]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            raw = ddgs.text(query, region=region, max_results=max_results)

        return [
            {
                "title": r.get("title", "(无标题)"),
                "href": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
