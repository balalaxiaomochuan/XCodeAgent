"""WebFetch 工具：获取网页内容并提取文本。"""

from __future__ import annotations

import re

from xcodeagent.tools.base import BaseTool, ToolResult


class WebFetch(BaseTool):
    """获取网页 URL 的内容，提取纯文本返回。

    适合：阅读搜索结果中的具体网页内容。
    不适合：搜索网页 → 用 WebSearch；本地文件 → 用 ReadFile。
    """

    name = "web_fetch"
    category = "read"
    description = (
        "获取指定 URL 的网页内容，提取纯文本后返回。"
        "何时使用：阅读 WebSearch 返回的具体网页、查看在线文档、获取 API 响应。"
        "何时不用：搜索网页 → 用 WebSearch；读取本地文件 → 用 ReadFile。"
        "参数：url 为要获取的网页地址（必填）；max_chars 为最大返回字符数（可选，默认 8000，上限 20000）。"
        "注意：无法处理需要登录的页面，部分网站可能屏蔽爬虫。"
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要获取的网页 URL，必须以 http:// 或 https:// 开头",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "最大返回字符数，默认 8000，上限 20000",
                },
            },
            "required": ["url"],
        }

    async def execute(self, url: str, max_chars: int = 8000) -> ToolResult:
        if not url.startswith(("http://", "https://")):
            return ToolResult(success=False, error=f"URL 必须以 http:// 或 https:// 开头: {url}")

        max_chars = min(max(max_chars, 100), 20000)

        try:
            import httpx
        except ImportError:
            return ToolResult(
                success=False,
                error="缺少 httpx 依赖，请运行: uv sync",
            )

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/130.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml,*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                )

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ToolResult(
                    success=False,
                    error=f"不支持的内容类型: {content_type}，仅支持 HTML/文本",
                )

            text = self._extract_text(response.text)

            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n... (内容截断，共 {len(text)} 字符)"

            if not text.strip():
                return ToolResult(success=True, output=f"网页内容为空: {url}")

            return ToolResult(
                success=True,
                output=f"网页内容 ({url}):\n\n{text}",
            )

        except httpx.HTTPStatusError as e:
            return ToolResult(
                success=False,
                error=f"HTTP 错误 {e.response.status_code}: {url}",
            )
        except httpx.RequestError as e:
            return ToolResult(success=False, error=f"请求失败: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"获取网页失败: {e}")

    @staticmethod
    def _extract_text(html: str) -> str:
        """从 HTML 中提取纯文本。"""
        # 移除 script 和 style 标签及其内容
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # 移除 HTML 标签
        text = re.sub(r'<[^>]+>', ' ', html)

        # 解码常见的 HTML 实体
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        text = text.replace('&nbsp;', ' ')
        # 数字实体
        text = re.sub(r'&#\d+;', ' ', text)
        text = re.sub(r'&#x[0-9a-fA-F]+;', ' ', text)

        # 合并连续空白
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()
