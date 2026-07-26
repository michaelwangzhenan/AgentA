"""
主账号端点：用户列表、新建、改角色与删除；删号时级联清理该用户的隔离业务数据。

- GET /api/admin/users：列出所有用户
- POST /api/admin/users：新建用户
- PATCH /api/admin/users/{user_id}/role：改用户角色
- DELETE /api/admin/users/{user_id}：删除用户 + 级联清理；主账号不可删
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import (
    get_plan_store,
    get_quiz_store,
    get_session_store,
    get_srs_store,
    get_trace_store,
    get_usage_store,
    get_user_memory_store,
    get_user_store,
    is_super_admin_user,
    require_super_admin,
)
from src.api.schemas.auth import (
    CreateUserRequest,
    OkResponse,
    UpdateUserRoleRequest,
    UserInfo,
    UserListResponse,
)
from src.api.user_info import to_user_info
from src.stores.user_store import ROLE_ADMIN, ROLE_READONLY, ROLE_USER, UserStore

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
    from src.stores.semantic_cache import delete_for_user_soft

    delete_for_user_soft(user_id)


def _reject_super_admin_mutation(target: dict) -> None:
    if is_super_admin_user(target):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="主账号不可修改"
        )


@router.get("/users", response_model=UserListResponse)
def list_users(
    _: dict = Depends(require_super_admin),
    store: UserStore = Depends(get_user_store),
) -> UserListResponse:
    """列出所有用户（仅主账号）。"""
    return UserListResponse(users=[to_user_info(u) for u in store.list_users()])


@router.post("/users", response_model=UserInfo, status_code=status.HTTP_201_CREATED)
def create_user(
    req: CreateUserRequest,
    _: dict = Depends(require_super_admin),
    store: UserStore = Depends(get_user_store),
) -> UserInfo:
    """新建用户（仅主账号）；成功不下发 cookie。"""
    role = req.role if req.role in (ROLE_READONLY, ROLE_USER, ROLE_ADMIN) else ROLE_USER
    user = store.create_user(req.username, req.password, role=role)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用或无效"
        )
    logger.info("[admin] 新建用户 id=%d username=%r role=%s", user["id"], user["username"], role)
    return to_user_info(user)


@router.patch("/users/{user_id}/role", response_model=UserInfo)
def update_user_role(
    user_id: int,
    req: UpdateUserRoleRequest,
    _: dict = Depends(require_super_admin),
    store: UserStore = Depends(get_user_store),
) -> UserInfo:
    """改用户角色（仅主账号）；主账号本身不可改。"""
    if req.role not in (ROLE_READONLY, ROLE_USER, ROLE_ADMIN):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效角色")
    target = store.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    _reject_super_admin_mutation(target)
    if not store.update_role(user_id, req.role):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法更新角色")
    updated = store.get_user_by_id(user_id)
    assert updated is not None
    logger.info("[admin] 改用户角色 id=%d role=%s", user_id, req.role)
    return to_user_info(updated)


@router.delete("/users/{user_id}", response_model=OkResponse)
def delete_user(
    user_id: int,
    _: dict = Depends(require_super_admin),
    store: UserStore = Depends(get_user_store),
) -> OkResponse:
    """删除用户 + 级联清理其全部业务数据（仅主账号）；主账号不可删。"""
    target = store.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    _reject_super_admin_mutation(target)
    purge_user_data(user_id)
    store.delete_user(user_id)
    logger.info("[admin] 删除用户 id=%d 并清理其全部业务数据", user_id)
    return OkResponse()
