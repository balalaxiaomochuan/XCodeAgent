"""JSON-RPC 2.0 消息类型和编解码。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ── JSON-RPC 2.0 消息类型 ──────────────────────────────────────


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 请求。"""
    method: str
    params: dict | None = None
    id: int | str = 0

    def to_dict(self) -> dict:
        msg: dict = {"jsonrpc": "2.0", "method": self.method, "id": self.id}
        if self.params is not None:
            msg["params"] = self.params
        return msg


@dataclass
class JSONRPCNotification:
    """JSON-RPC 2.0 通知（无 id，不需要响应）。"""
    method: str
    params: dict | None = None

    def to_dict(self) -> dict:
        msg: dict = {"jsonrpc": "2.0", "method": self.method}
        if self.params is not None:
            msg["params"] = self.params
        return msg


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 响应。"""
    id: int | str
    result: Any = None
    error: JSONRPCError | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def from_dict(cls, data: dict) -> JSONRPCResponse:
        return cls(
            id=data.get("id", 0),
            result=data.get("result"),
            error=JSONRPCError.from_dict(data["error"]) if "error" in data else None,
        )


@dataclass
class JSONRPCError:
    """JSON-RPC 2.0 错误对象。"""
    code: int
    message: str
    data: Any = None

    @classmethod
    def from_dict(cls, data: dict) -> JSONRPCError:
        return cls(
            code=data.get("code", -1),
            message=data.get("message", "Unknown error"),
            data=data.get("data"),
        )


def encode(msg: JSONRPCRequest | JSONRPCNotification) -> bytes:
    """编码 JSON-RPC 消息为字节串。"""
    return (json.dumps(msg.to_dict(), ensure_ascii=False) + "\n").encode("utf-8")


def decode(data: bytes | str) -> JSONRPCResponse | JSONRPCRequest:
    """解码 JSON-RPC 响应或请求。"""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    obj = json.loads(data.strip())
    if "method" in obj and "id" not in obj:
        # 通知/请求（但不会有响应这个路径）
        return JSONRPCRequest(
            method=obj["method"],
            params=obj.get("params"),
            id=obj.get("id", 0),
        )
    return JSONRPCResponse.from_dict(obj)


# ── MCP 特定数据结构 ───────────────────────────────────────────


@dataclass
class MCPServerInfo:
    """MCP Server initialize 响应中的服务器信息。"""
    name: str = ""
    version: str = ""


@dataclass
class MCPToolDef:
    """MCP tools/list 响应中的工具定义。"""
    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": [],
    })

    @classmethod
    def from_dict(cls, data: dict) -> MCPToolDef:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            parameters=data.get("inputSchema", {
                "type": "object",
                "properties": {},
                "required": [],
            }),
        )
