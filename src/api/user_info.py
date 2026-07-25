"""把 user_store 返回的 dict 转成 API UserInfo（含 can_manage_users）。"""

from __future__ import annotations

from typing import Any

from src.api.deps import is_super_admin_user
from src.api.schemas.auth import UserInfo


def to_user_info(user: dict[str, Any]) -> UserInfo:
    return UserInfo(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        created_at=user.get("created_at") or "",
        can_manage_users=is_super_admin_user(user),
    )
