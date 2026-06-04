"""MCP 端点响应模型"""

from typing import Any

from pydantic import BaseModel


class MCPServer(BaseModel):
    name: str
    status: str
    tool_count: int
    error: str | None = None
    command: str = ""


class MCPServerListResponse(BaseModel):
    servers: list[MCPServer]


class MCPTool(BaseModel):
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = {}
    server: str


class MCPToolListResponse(BaseModel):
    tools: list[MCPTool]
