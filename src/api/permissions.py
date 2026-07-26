"""按 scope 判定写权限；能力表为前后端对齐的单一事实源。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from src.api.deps import get_current_user, is_super_admin_user
from src.stores.user_store import ROLE_ADMIN, ROLE_READONLY, ROLE_USER

logger = logging.getLogger(__name__)

ROLE_ORDER: dict[str, int] = {
    ROLE_READONLY: 0,
    ROLE_USER: 1,
    ROLE_ADMIN: 2,
}

# scope → 最低可写角色（users 另靠 can_manage_users）
SCOPE_MIN_ROLE: dict[str, str] = {
    "chat": ROLE_USER,
    "kb": ROLE_ADMIN,
    "memory": ROLE_USER,
    "usage": ROLE_ADMIN,
    "quality": ROLE_ADMIN,
    "skills": ROLE_ADMIN,
    "db": ROLE_ADMIN,
    "backup": ROLE_ADMIN,
    "profile": ROLE_USER,
    "account": ROLE_USER,
    "config": ROLE_ADMIN,
}

ALL_SCOPES: tuple[str, ...] = tuple(SCOPE_MIN_ROLE.keys())


def role_level(role: str) -> int:
    return ROLE_ORDER.get(role, -1)


def can_write(role: str, scope: str, *, can_manage_users: bool = False) -> bool:
    if scope == "users":
        return can_manage_users
    min_role = SCOPE_MIN_ROLE.get(scope)
    if min_role is None:
        return False
    return role_level(role) >= role_level(min_role)


def capabilities_for_user(user: dict[str, Any]) -> list[str]:
    role = user.get("role") or ROLE_USER
    can_manage = is_super_admin_user(user)
    caps = [s for s in ALL_SCOPES if can_write(role, s, can_manage_users=can_manage)]
    if can_manage:
        caps.append("users")
    return caps


def require_write(scope: str):
    """依赖工厂：当前用户须具备 scope 写权限，否则 403。"""

    async def _dep(
        request: Request,
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        can_manage = is_super_admin_user(user)
        if not can_write(user.get("role") or "", scope, can_manage_users=can_manage):
            logger.warning(
                "写权限拒绝: user=%s role=%s scope=%s path=%s",
                user.get("username"),
                user.get("role"),
                scope,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前账号无修改权限",
            )
        return user

    return _dep
