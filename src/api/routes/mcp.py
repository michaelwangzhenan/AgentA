"""
MCP servers / tools 端点：管理 .agenta/mcp/ 下 server 配置、实时启停与 tool 列表。

- GET /api/mcp/servers：列出 server 状态 + enabled 标志 + command/args/env
- GET /api/mcp/tools：列出已连接 server 暴露的 tool（带 namespace 前缀）
- POST /api/mcp/servers：新建 server（写 config.json + 实时启动）
- PUT /api/mcp/servers/{name}：更新 server（重启使新配置生效）
- POST /api/mcp/servers/{name}/rename：改名（JSON key + disabled 列表迁移）
- DELETE /api/mcp/servers/{name}：删除 server（stop + 从 config.json 移除）
- POST /api/mcp/servers/{name}/toggle：启用 / 禁用（改 disabled.json + 实时启停）
- POST /api/mcp/reload：重读 config + disabled，按差异 diff 启停 server
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.agent.core.mcp_config import (
    MCPConfigError,
    add_server,
    cleanup_disabled_orphans,
    delete_server,
    list_specs,
    read_disabled_list,
    rename_server,
    toggle_server,
    update_server,
)
from src.agent.core.mcp_manager import MCPManager
from src.api.deps import get_current_user, get_mcp_manager, require_admin
from src.api.schemas.mcp import (
    MCPReloadResponse,
    MCPServer,
    MCPServerCreateRequest,
    MCPServerListResponse,
    MCPServerRenameRequest,
    MCPServerToggleRequest,
    MCPServerToggleResponse,
    MCPServerUpdateRequest,
    MCPTool,
    MCPToolListResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp"])


# 把 MCPConfigError.code 映射到 HTTP 状态码（对齐 Skills）
_CODE_TO_STATUS = {
    "invalid_name": status.HTTP_400_BAD_REQUEST,
    "invalid_field": status.HTTP_400_BAD_REQUEST,
    "already_exists": status.HTTP_409_CONFLICT,
    "not_found": status.HTTP_404_NOT_FOUND,
    "parse_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "read_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "write_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _raise_from_config_error(e: MCPConfigError) -> None:
    code = _CODE_TO_STATUS.get(e.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    raise HTTPException(status_code=code, detail={"code": e.code, "message": e.message})


def _build_server_list(manager: MCPManager) -> list[MCPServer]:
    """合并 config.json + disabled.json + manager.status() 三源，输出 UI 完整视图。

    - config.json 是真相（决定 UI 列哪些 server）
    - disabled.json 决定 enabled 标志
    - manager.status() 提供运行时 status / tool_count / error
    """
    try:
        specs = list_specs()
    except MCPConfigError as e:
        # 配置文件解析失败时，UI 仍能显示后台已运行的 server（启动时加载过的）
        logger.warning("[MCP API] 读 config.json 失败：%s（按运行时状态回填）", e.message)
        specs = []
    disabled = read_disabled_list()
    runtime = {row["name"]: row for row in manager.status()}

    items: list[MCPServer] = []
    seen: set[str] = set()
    for spec in specs:
        seen.add(spec.name)
        run = runtime.get(spec.name)
        items.append(
            MCPServer(
                name=spec.name,
                status=run["status"] if run else "closed",
                enabled=spec.name not in disabled,
                tool_count=run["tool_count"] if run else 0,
                error=run["error"] if run else None,
                command=spec.command,
                args=list(spec.args),
                env=dict(spec.env),
            )
        )
    # 兜底：manager 里有但 list_specs 没拿到的（config.json 解析失败 / 已删）
    # enabled 仍按 disabled.json 判定，避免把正在跑的 server 误标"未启用"
    for name in sorted(set(runtime.keys()) - seen):
        run = runtime[name]
        items.append(
            MCPServer(
                name=name,
                status=run["status"],
                enabled=name not in disabled,
                tool_count=run["tool_count"],
                error=run["error"],
                command=run.get("command", ""),
            )
        )
    return items


@router.get("/servers", response_model=MCPServerListResponse)
def list_servers(
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(get_current_user),
) -> MCPServerListResponse:
    return MCPServerListResponse(servers=_build_server_list(manager))


@router.get("/tools", response_model=MCPToolListResponse)
def list_tools(
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(get_current_user),
) -> MCPToolListResponse:
    rows = manager.list_tools()
    return MCPToolListResponse(tools=[MCPTool(**row) for row in rows])


@router.post(
    "/servers",
    response_model=MCPServer,
    status_code=status.HTTP_201_CREATED,
)
def create_server_endpoint(
    req: MCPServerCreateRequest,
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(require_admin),
) -> MCPServer:
    try:
        spec = add_server(req.name, req.command, list(req.args), dict(req.env))
    except MCPConfigError as e:
        _raise_from_config_error(e)
    # 新建默认启用：直接拉起，让下一轮 chat 立即可见
    manager.start_one(spec)
    logger.info("[MCP API] 新建 server: %s", req.name)
    return _server_after_change(req.name, manager)


@router.put("/servers/{name}", response_model=MCPServer)
def update_server_endpoint(
    name: str,
    req: MCPServerUpdateRequest,
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(require_admin),
) -> MCPServer:
    try:
        spec = update_server(name, req.command, list(req.args), dict(req.env))
    except MCPConfigError as e:
        _raise_from_config_error(e)
    # 重启让新 command/args/env 生效（仅当当前未禁用）
    if name not in read_disabled_list():
        manager.stop_one(name)
        manager.start_one(spec)
    logger.info("[MCP API] 更新 server: %s", name)
    return _server_after_change(name, manager)


@router.post("/servers/{name}/rename", response_model=MCPServer)
def rename_server_endpoint(
    name: str,
    req: MCPServerRenameRequest,
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(require_admin),
) -> MCPServer:
    try:
        spec = rename_server(name, req.new_name)
    except MCPConfigError as e:
        _raise_from_config_error(e)
    # 旧 name 在 manager 里还跑着 → stop；新 name 启动（除非禁用）
    manager.stop_one(name)
    if req.new_name not in read_disabled_list():
        manager.start_one(spec)
    logger.info("[MCP API] 改名 server: %s → %s", name, req.new_name)
    return _server_after_change(req.new_name, manager)


@router.delete("/servers/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server_endpoint(
    name: str,
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(require_admin),
) -> None:
    try:
        delete_server(name)
    except MCPConfigError as e:
        _raise_from_config_error(e)
    manager.stop_one(name)
    # 同步清理 disabled.json 里的孤儿（即使删除时该 server 处于 disabled）
    cleanup_disabled_orphans()
    logger.info("[MCP API] 删除 server: %s", name)


@router.post("/servers/{name}/toggle", response_model=MCPServerToggleResponse)
def toggle_server_endpoint(
    name: str,
    req: MCPServerToggleRequest,
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(require_admin),
) -> MCPServerToggleResponse:
    # 校验 name 在 config.json 中存在
    try:
        specs = list_specs()
    except MCPConfigError as e:
        _raise_from_config_error(e)
    valid_names = {s.name for s in specs}
    try:
        new_state = toggle_server(name, req.enabled, valid_names=valid_names)
    except MCPConfigError as e:
        _raise_from_config_error(e)

    # 实时启停：开 → start_one；关 → stop_one
    if req.enabled:
        spec = next((s for s in specs if s.name == name), None)
        if spec is not None:
            manager.start_one(spec)
    else:
        manager.stop_one(name)

    logger.info("[MCP API] toggle server: %s → enabled=%s", name, new_state)
    return MCPServerToggleResponse(name=name, enabled=new_state)


@router.post("/reload", response_model=MCPReloadResponse)
def reload_endpoint(
    manager: MCPManager = Depends(get_mcp_manager),
    _: dict = Depends(require_admin),
) -> MCPReloadResponse:
    """重读 config.json + disabled.json，按差异 diff 启停 server。

    用户编辑磁盘后 / 怀疑状态偏移时手动触发。已加载到 LLM 的 system prompt
    无法撤回，需开新一轮对话才能让 LLM 看到 tool 集变化。
    """
    try:
        specs = list_specs()
    except MCPConfigError as e:
        _raise_from_config_error(e)
    disabled = read_disabled_list()
    cleanup_disabled_orphans()
    manager.reload(specs, disabled_names=disabled)

    runtime = manager.status()
    connected = sum(1 for r in runtime if r["status"] == "connected")
    failed = sum(1 for r in runtime if r["status"] == "failed")
    logger.info(
        "[MCP API] reload: total=%d enabled=%d connected=%d failed=%d",
        len(specs), len(specs) - len(disabled & {s.name for s in specs}),
        connected, failed,
    )
    return MCPReloadResponse(
        total=len(specs),
        enabled=len(specs) - len(disabled & {s.name for s in specs}),
        connected=connected,
        failed=failed,
    )


def _server_after_change(name: str, manager: MCPManager) -> MCPServer:
    """改动 / 启停后构造单 server 视图返回给前端，便于行内更新。

    若 manager 暂时还没拿到该 server 状态（比如 stop 后立即查），按 closed 兜底。
    """
    items = _build_server_list(manager)
    for it in items:
        if it.name == name:
            return it
    # 极端兜底：name 在配置里但 list_specs 异常 → 返 closed 占位
    return MCPServer(name=name, status="closed", enabled=True)
