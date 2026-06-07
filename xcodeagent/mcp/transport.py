"""MCP 传输层：stdio 子进程管道和 Streamable HTTP。"""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import httpx

from xcodeagent.mcp.protocol import (
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    decode,
)


class TransportError(Exception):
    """传输层错误。"""
    pass


class MCPTransport(ABC):
    """传输层抽象基类。"""

    @abstractmethod
    async def connect(self) -> None:
        """建立连接。"""
        ...

    @abstractmethod
    async def send(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """发送请求并等待响应。"""
        ...

    @abstractmethod
    async def send_notification(self, notification: JSONRPCNotification) -> None:
        """发送通知（无需响应）。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭连接。"""
        ...


# ── Stdio 传输 ─────────────────────────────────────────────────


class StdioTransport(MCPTransport):
    """通过子进程 stdin/stdout 管道通信。"""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        merged_env = os.environ.copy()
        merged_env.update(self._env)

        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
            )
        except FileNotFoundError:
            raise TransportError(f"命令未找到: {self._command}")
        except Exception as e:
            raise TransportError(f"启动子进程失败: {e}")

    async def send(self, request: JSONRPCRequest) -> JSONRPCResponse:
        if self._process is None or self._process.stdin is None:
            raise TransportError("传输未连接")

        async with self._lock:
            payload = json.dumps(request.to_dict(), ensure_ascii=False) + "\n"
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()

            if self._process.stdout is None:
                raise TransportError("子进程 stdout 不可用")

            line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=30.0,
            )
            if not line:
                raise TransportError("子进程已关闭 stdout")

        return decode(line)

    async def send_notification(self, notification: JSONRPCNotification) -> None:
        if self._process is None or self._process.stdin is None:
            raise TransportError("传输未连接")

        async with self._lock:
            payload = json.dumps(notification.to_dict(), ensure_ascii=False) + "\n"
            self._process.stdin.write(payload.encode("utf-8"))
            await self._process.stdin.drain()

    async def close(self) -> None:
        if self._process is not None:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None


# ── HTTP 传输 ─────────────────────────────────────────────────


class HttpTransport(MCPTransport):
    """通过 Streamable HTTP POST 请求通信。

    支持两种响应格式：
    - 纯 JSON (Content-Type: application/json)
    - SSE 事件流 (Content-Type: text/event-stream)
    自动管理 Mcp-Session-Id 以维持会话。
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ):
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                **self._headers,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=httpx.Timeout(30.0),
        )

    async def send(self, request: JSONRPCRequest) -> JSONRPCResponse:
        if self._client is None:
            raise TransportError("HTTP 客户端未连接")

        headers = self._build_headers()
        try:
            resp = await self._client.post(
                self._url,
                content=json.dumps(request.to_dict(), ensure_ascii=False),
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise TransportError(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except httpx.RequestError as e:
            raise TransportError(f"HTTP 请求失败: {e}")

        # 捕获 session ID（initialize 响应会返回）
        self._capture_session(resp)

        data = self._parse_response(resp)
        return JSONRPCResponse.from_dict(data)

    async def send_notification(self, notification: JSONRPCNotification) -> None:
        if self._client is None:
            raise TransportError("HTTP 客户端未连接")

        headers = self._build_headers()
        try:
            resp = await self._client.post(
                self._url,
                content=json.dumps(notification.to_dict(), ensure_ascii=False),
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.RequestError as e:
            raise TransportError(f"HTTP 通知发送失败: {e}")

        self._capture_session(resp)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._session_id = None

    # ── 内部辅助 ─────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        """构建请求头，含 session ID（如果有的话）。"""
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _capture_session(self, resp: httpx.Response) -> None:
        """捕获响应中的 Mcp-Session-Id。"""
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

    @staticmethod
    def _parse_response(resp: httpx.Response) -> dict:
        """解析响应体：JSON 或 SSE 事件流。"""
        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            return _parse_sse(resp.text)
        else:
            return resp.json()


def _parse_sse(text: str) -> dict:
    """从 SSE 事件流中提取第一条 data 的 JSON。

    SSE 格式：
        event: message
        id: session-abc123
        data: {"result": ...}

    多个事件时只取第一个有 data 的事件。
    """
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                return json.loads(payload)

    raise TransportError("SSE 响应中未找到 data 字段")
