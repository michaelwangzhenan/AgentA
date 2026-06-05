"""MCP 端点请求 / 响应模型"""

from typing import Any

from pydantic import BaseModel, Field


class MCPServer(BaseModel):
    """单个 server 的运行时 + 配置摘要。

    `status` 是运行时状态（connecting / connected / failed / closed）；
    `enabled` 来自 disabled.json，未启用的 server 不会被启动 → status 一般是 closed/缺失。
    `args` / `env` 保留 ${VAR} 字面量（未做 env 展开），便于 UI 编辑回显。
    """

    name: str
    status: str
    enabled: bool = True
    tool_count: int = 0
    error: str | None = None
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MCPServerListResponse(BaseModel):
    servers: list[MCPServer]


class MCPTool(BaseModel):
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = Field(default_factory=dict)
    server: str


class MCPToolListResponse(BaseModel):
    tools: list[MCPTool]


# ── CRUD 请求 / 响应 ─────────────────────────────────────────────────────────


class MCPServerCreateRequest(BaseModel):
    """POST /api/mcp/servers 新建 server"""

    name: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, description="可执行命令，如 npx / python")
    args: list[str] = Field(default_factory=list, description="命令行参数列表")
    env: dict[str, str] = Field(
        default_factory=dict,
        description="环境变量；value 内 ${VAR} 启动时按当前进程 env 展开",
    )


class MCPServerUpdateRequest(BaseModel):
    """PUT /api/mcp/servers/{name} 整体替换 command / args / env；name 不可改，走 rename"""

    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MCPServerRenameRequest(BaseModel):
    """POST /api/mcp/servers/{name}/rename 改名"""

    new_name: str = Field(min_length=1, max_length=64)


class MCPServerToggleRequest(BaseModel):
    """POST /api/mcp/servers/{name}/toggle 启用 / 禁用"""

    enabled: bool


class MCPServerToggleResponse(BaseModel):
    name: str
    enabled: bool


class MCPReloadResponse(BaseModel):
    """POST /api/mcp/reload 整体重读配置后的状态汇总"""

    total: int = Field(description="config.json 中 server 总数")
    enabled: int = Field(description="未在 disabled 列表中的 server 数")
    connected: int = Field(description="reload 完成后已连接的 server 数")
    failed: int = Field(description="启动失败的 server 数")
