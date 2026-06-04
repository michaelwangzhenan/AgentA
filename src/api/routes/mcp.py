"""MCP 只读列表端点。

复用 `MCPManager.status` 和 `list_tools`，由 Agent 启动时已经 start_all。
UI 不提供添加 / 删除 server（server config 在 `.agenta/mcp.json`，需重启进程）。
"""

from fastapi import APIRouter, Depends

from src.agent.core.mcp_manager import MCPManager
from src.api.deps import get_mcp_manager
from src.api.schemas.mcp import (
    MCPServer,
    MCPServerListResponse,
    MCPTool,
    MCPToolListResponse,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/servers", response_model=MCPServerListResponse)
def list_servers(
    manager: MCPManager = Depends(get_mcp_manager),
) -> MCPServerListResponse:
    rows = manager.status()
    return MCPServerListResponse(servers=[MCPServer(**row) for row in rows])


@router.get("/tools", response_model=MCPToolListResponse)
def list_tools(
    manager: MCPManager = Depends(get_mcp_manager),
) -> MCPToolListResponse:
    rows = manager.list_tools()
    return MCPToolListResponse(tools=[MCPTool(**row) for row in rows])
