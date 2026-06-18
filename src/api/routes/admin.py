"""管理员端点：用户管理（列表 / 删除，含业务数据级联清理）。

仅 admin 可访问。删用户时连带清理其全部隔离数据（会话 / 记忆 / 计划 / 测验 / SRS），
共享数据（知识库 / skills / mcp / 系统配置）不属于个人、不清理。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import (
    get_session_store,
    get_plan_store,
    get_quiz_store,
    get_srs_store,
    get_trace_store,
    get_usage_store,
    get_user_memory_store,
    get_user_store,
    require_admin,
)
from src.api.schemas.auth import OkResponse, UserInfo, UserListResponse
from src.stores.user_store import ROLE_ADMIN, UserStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def purge_user_data(user_id: int) -> None:
    """级联清理某用户的全部业务数据（会话 / 记忆 / 计划 / 测验 / SRS）。"""
    get_session_store().delete_all_for_user(user_id)
    mem = get_user_memory_store()
    if mem is not None:
        mem.clear(user_id)
    get_plan_store().delete_all_for_user(user_id)
    get_quiz_store().delete_all_for_user(user_id)
    get_srs_store().delete_all_for_user(user_id)
    get_usage_store().delete_all_for_user(user_id)
    get_trace_store().delete_all_for_user(user_id)
    # 语义缓存按 user_id 隔离，删号时一并清掉该用户的缓存答案（软失败）
    from src.stores.semantic_cache import delete_for_user_soft
    delete_for_user_soft(user_id)


@router.get("/users", response_model=UserListResponse)
def list_users(
    _: dict = Depends(require_admin),
    store: UserStore = Depends(get_user_store),
) -> UserListResponse:
    """列出所有用户（仅 admin）。"""
    return UserListResponse(users=[UserInfo(**u) for u in store.list_users()])


@router.delete("/users/{user_id}", response_model=OkResponse)
def delete_user(
    user_id: int,
    me: dict = Depends(require_admin),
    store: UserStore = Depends(get_user_store),
) -> OkResponse:
    """删除用户 + 级联清理其全部业务数据（仅 admin）。

    禁止删自己；禁止删最后一个 admin。
    """
    if user_id == me["id"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除自己")
    target = store.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if target["role"] == ROLE_ADMIN and store.count_admins() <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除最后一个管理员"
        )
    purge_user_data(user_id)
    store.delete_user(user_id)
    logger.info("[admin] 删除用户 id=%d 并清理其全部业务数据", user_id)
    return OkResponse()
