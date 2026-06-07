"""认证相关请求 / 响应 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    """注册 / 登录共用：用户名 + 密码。"""

    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码")


class UserInfo(BaseModel):
    """当前用户信息（不含密码）。"""

    id: int
    username: str
    role: str
    created_at: str = ""


class AuthResponse(BaseModel):
    """登录 / 注册成功响应。"""

    user: UserInfo


class LogoutResponse(BaseModel):
    ok: bool = True


class UpdateUsernameRequest(BaseModel):
    """改用户名。"""

    username: str = Field(..., min_length=1, max_length=64, description="新用户名")


class ChangePasswordRequest(BaseModel):
    """改密码：需旧密码校验。"""

    old_password: str = Field(..., min_length=1, max_length=128, description="旧密码")
    new_password: str = Field(..., min_length=1, max_length=128, description="新密码")


class UserListResponse(BaseModel):
    """用户列表（admin 用户管理页）。"""

    users: list[UserInfo]


class OkResponse(BaseModel):
    ok: bool = True


class LlmPrefs(BaseModel):
    """某用户当前生效的 LLM 偏好（已合并全局默认）。"""

    active_model: str
    thinking_enabled: bool
    thinking_budget: int


class LlmPrefsUpdate(BaseModel):
    """更新 LLM 偏好；只传要改的字段，其余保持原值。"""

    active_model: str | None = None
    thinking_enabled: bool | None = None
    thinking_budget: int | None = Field(default=None, ge=512, le=64000)
