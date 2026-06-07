"""Skills 端点。

| Method | Path | 说明 |
|---|---|---|
| GET    | /api/skills              | 列出 .agenta/skills/ 扫描结果（含 body + disabled 列表）|
| POST   | /api/skills/reload       | 重新扫盘 + 清 Agent 缓存（免重启 uvicorn）|
| POST   | /api/skills              | 新建 skill（创目录 + 写 SKILL.md + reload）|
| PUT    | /api/skills/{name}       | 更新 skill（description + body + frontmatter_extra；name 不可改，走 rename）|
| POST   | /api/skills/{name}/rename| 改名（目录改 + frontmatter name 同步 + disabled list 迁移）|
| DELETE | /api/skills/{name}       | 删除 skill 目录 + reload |
| POST   | /api/skills/{name}/toggle| 启用 / 禁用（仅修改 .agenta/skills/disabled.json）|
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_agent, get_current_user, require_admin
from src.api.schemas.skills import (
    SkillCreateRequest,
    SkillFailure,
    SkillItem,
    SkillRenameRequest,
    SkillReloadResponse,
    SkillsResponse,
    SkillToggleRequest,
    SkillToggleResponse,
    SkillUpdateRequest,
)
from src.cli.skill_loader import (
    SkillIOError,
    create_skill,
    delete_skill,
    rename_skill,
    scan_skills,
    toggle_skill,
    update_skill,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skills", tags=["skills"])


# 把 SkillIOError.code 映射到 HTTP 状态码：name 校验 → 400；冲突 → 409；缺失 → 404；其余 → 500
_CODE_TO_STATUS = {
    "invalid_name": status.HTTP_400_BAD_REQUEST,
    "missing_description": status.HTTP_400_BAD_REQUEST,
    "already_exists": status.HTTP_409_CONFLICT,
    "not_found": status.HTTP_404_NOT_FOUND,
    "write_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _raise_from_io_error(e: SkillIOError) -> None:
    code = _CODE_TO_STATUS.get(e.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    raise HTTPException(status_code=code, detail={"code": e.code, "message": e.message})


def _info_to_item(info) -> SkillItem:
    return SkillItem(
        name=info.name,
        description=info.description,
        location=str(info.location),
        body=info.body,
        frontmatter_extra=dict(info.frontmatter_extra),
    )


def _scan_to_response(result) -> SkillsResponse:
    return SkillsResponse(
        loaded=[_info_to_item(s) for s in result.loaded.values()],
        disabled=[_info_to_item(s) for s in result.disabled.values()],
        failed=[SkillFailure(path=str(f.path), reason=f.reason) for f in result.failed],
    )


def _reload_agent_cache() -> None:
    """CRUD / toggle 后清 Agent 单例，让下次 chat 重建实例读到磁盘新内容。"""
    get_agent.cache_clear()


@router.get("", response_model=SkillsResponse)
def list_skills(_: dict = Depends(get_current_user)) -> SkillsResponse:
    return _scan_to_response(scan_skills())


@router.post("/reload", response_model=SkillReloadResponse)
def reload_skills(_: dict = Depends(require_admin)) -> SkillReloadResponse:
    """重新扫描 .agenta/skills/ 并清空 Agent 单例缓存。

    下一次 chat 请求会触发 `get_agent()` 重建实例，新 catalog 立即可用。
    已经发给 LLM 的 system prompt 不可撤回，所以"当前对话还看不到新 skill"，
    必须用户开新一轮对话才能让 LLM 看到。
    """
    _reload_agent_cache()
    result = scan_skills()
    return SkillReloadResponse(
        loaded_count=len(result.loaded),
        disabled_count=len(result.disabled),
        failed_count=len(result.failed),
    )


@router.post("", response_model=SkillItem, status_code=status.HTTP_201_CREATED)
def create_skill_endpoint(
    req: SkillCreateRequest, _: dict = Depends(require_admin)
) -> SkillItem:
    try:
        info = create_skill(
            req.name,
            req.description,
            req.body,
            frontmatter_extra=req.frontmatter_extra,
        )
    except SkillIOError as e:
        _raise_from_io_error(e)
    _reload_agent_cache()
    logger.info("[Skills] 创建 skill: %s", req.name)
    return _info_to_item(info)


@router.put("/{name}", response_model=SkillItem)
def update_skill_endpoint(
    name: str, req: SkillUpdateRequest, _: dict = Depends(require_admin)
) -> SkillItem:
    try:
        info = update_skill(
            name,
            req.description,
            req.body,
            frontmatter_extra=req.frontmatter_extra,
        )
    except SkillIOError as e:
        _raise_from_io_error(e)
    _reload_agent_cache()
    logger.info("[Skills] 更新 skill: %s", name)
    return _info_to_item(info)


@router.post("/{name}/rename", response_model=SkillItem)
def rename_skill_endpoint(
    name: str, req: SkillRenameRequest, _: dict = Depends(require_admin)
) -> SkillItem:
    """改名：目录从 `<dir>/{name}/` 移到 `<dir>/{new_name}/`，同时同步
    frontmatter `name:` 字段（强一致）。如旧 name 在 disabled list 里，
    迁移到新 name 保持禁用状态。
    """
    try:
        info = rename_skill(name, req.new_name)
    except SkillIOError as e:
        _raise_from_io_error(e)
    _reload_agent_cache()
    logger.info("[Skills] 改名 skill: %s → %s", name, req.new_name)
    return _info_to_item(info)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill_endpoint(name: str, _: dict = Depends(require_admin)) -> None:
    try:
        delete_skill(name)
    except SkillIOError as e:
        _raise_from_io_error(e)
    _reload_agent_cache()
    logger.info("[Skills] 删除 skill: %s", name)


@router.post("/{name}/toggle", response_model=SkillToggleResponse)
def toggle_skill_endpoint(
    name: str, req: SkillToggleRequest, _: dict = Depends(require_admin)
) -> SkillToggleResponse:
    # 先扫盘拿到所有已存在的 name（含 disabled），再 toggle，防止操作不存在的 skill
    current = scan_skills()
    valid_names = set(current.loaded.keys()) | set(current.disabled.keys())
    try:
        new_state = toggle_skill(name, req.enabled, valid_names=valid_names)
    except SkillIOError as e:
        _raise_from_io_error(e)
    _reload_agent_cache()
    logger.info("[Skills] toggle skill: %s → enabled=%s", name, new_state)
    return SkillToggleResponse(name=name, enabled=new_state)
