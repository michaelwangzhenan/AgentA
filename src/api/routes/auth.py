"""
认证端点：登录 / 退出、账号资料与每用户 LLM 偏好；登录态为服务端 token + HttpOnly cookie。

- POST /api/auth/login：登录，下发 cookie
- POST /api/auth/logout：退出，删除 token 并清 cookie
- GET /api/auth/me：当前登录用户信息
- PATCH /api/auth/username：改当前用户名
- POST /api/auth/password：改当前密码（需校验旧密码）
- GET /api/auth/llm-prefs：读当前用户 LLM 偏好（未设字段回落全局默认）
- PATCH /api/auth/llm-prefs：写当前用户 LLM 偏好
- DELETE /api/auth/me：注销当前账号 + 级联清理业务数据
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

import src.config as _cfg
from src.api.deps import get_current_user, get_user_store
from src.api.routes.admin import purge_user_data
from src.llm import model_router
from src.api.schemas.auth import (
    AuthRequest,
    AuthResponse,
    ChangePasswordRequest,
    LlmPrefs,
    LlmPrefsUpdate,
    LogoutResponse,
    OkResponse,
    UpdateUsernameRequest,
    UserInfo,
)
from src.stores.user_store import ROLE_ADMIN, UserStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_cfg.AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=_cfg.AUTH_SESSION_TTL_DAYS * 86400,
        path="/",
    )


@router.post("/login", response_model=AuthResponse)
def login(
    req: AuthRequest,
    response: Response,
    store: UserStore = Depends(get_user_store),
) -> AuthResponse:
    """登录：校验密码，下发 cookie。失败 401。"""
    user = store.verify_password(req.username, req.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    token = store.create_session(user["id"], _cfg.AUTH_SESSION_TTL_DAYS)
    _set_session_cookie(response, token)
    return AuthResponse(user=UserInfo(**user))


@router.post("/logout", response_model=LogoutResponse)
def logout(
    request: Request,
    response: Response,
    store: UserStore = Depends(get_user_store),
) -> LogoutResponse:
    """退出：删除当前 token + 清 cookie。"""
    token = request.cookies.get(_cfg.AUTH_COOKIE_NAME)
    if token:
        store.delete_session(token)
    response.delete_cookie(_cfg.AUTH_COOKIE_NAME, path="/")
    return LogoutResponse(ok=True)


@router.patch("/username", response_model=UserInfo)
def update_username(
    req: UpdateUsernameRequest,
    user: dict = Depends(get_current_user),
    store: UserStore = Depends(get_user_store),
) -> UserInfo:
    """改当前用户的用户名。占用返回 409。"""
    result = store.update_username(user["id"], req.username)
    if result == "taken":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用")
    if result != "ok":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名无效")
    updated = store.get_user_by_id(user["id"])
    return UserInfo(**updated)  # type: ignore[arg-type]


@router.post("/password", response_model=OkResponse)
def change_password(
    req: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
    store: UserStore = Depends(get_user_store),
) -> OkResponse:
    """改当前用户密码，需旧密码校验。旧密码错返回 400。"""
    result = store.update_password(user["id"], req.old_password, req.new_password)
    if result == "wrong_old":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="旧密码不正确")
    if result != "ok":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码无效")
    return OkResponse()
    


def effective_llm_prefs(store: UserStore, user_id: int) -> LlmPrefs:
    """合并某用户存储的 LLM 偏好与全局默认，得到本次生效值。"""
    s = store.get_settings(user_id)
    return LlmPrefs(
        active_model=s["active_model"] or _cfg.ACTIVE_MODEL,
        thinking_enabled=(
            _cfg.THINKING_ENABLED if s["thinking_enabled"] is None else s["thinking_enabled"]
        ),
        thinking_budget=s["thinking_budget"] or _cfg.THINKING_BUDGET,
    )


@router.get("/llm-prefs", response_model=LlmPrefs)
def get_llm_prefs(
    user: dict = Depends(get_current_user),
    store: UserStore = Depends(get_user_store),
) -> LlmPrefs:
    """当前用户生效的模型 / thinking 偏好（每用户独立，未设置回落全局默认）。"""
    return effective_llm_prefs(store, user["id"])


@router.patch("/llm-prefs", response_model=LlmPrefs)
def update_llm_prefs(
    req: LlmPrefsUpdate,
    user: dict = Depends(get_current_user),
    store: UserStore = Depends(get_user_store),
) -> LlmPrefs:
    """更新当前用户的模型 / thinking 偏好；只改传入字段。不影响其他用户。

    active_model 允许 "auto"（交给模型路由按难度选）或任一已知模型 id。
    """
    if (
        req.active_model is not None
        and req.active_model != model_router.AUTO_MODEL
        and req.active_model not in _cfg.MODEL_CONFIGS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的模型: {req.active_model}",
        )
    store.set_settings(
        user["id"],
        active_model=req.active_model,
        thinking_enabled=req.thinking_enabled,
        thinking_budget=req.thinking_budget,
    )
    return effective_llm_prefs(store, user["id"])


@router.delete("/me", response_model=OkResponse)
def delete_own_account(
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
    store: UserStore = Depends(get_user_store),
) -> OkResponse:
    """注销当前账号：清空本人全部业务数据 + 删账号 + 清登录态。

    最后一个管理员不允许注销，否则无人可管理用户。
    """
    if user.get("role") == ROLE_ADMIN and store.count_admins() <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能注销最后一个管理员"
        )
    purge_user_data(user["id"])
    store.delete_user(user["id"])
    token = request.cookies.get(_cfg.AUTH_COOKIE_NAME)
    if token:
        store.delete_session(token)
    response.delete_cookie(_cfg.AUTH_COOKIE_NAME, path="/")
    logger.info("[auth] 用户注销账号: id=%d (%s)", user["id"], user["username"])
    return OkResponse()


@router.get("/me", response_model=UserInfo)
def me(
    request: Request,
    store: UserStore = Depends(get_user_store),
) -> UserInfo:
    """返回当前登录用户；未登录 401。"""
    if not _cfg.AUTH_ENABLED:
        return UserInfo(id=_cfg.DEFAULT_USER_ID, username="local", role=ROLE_ADMIN)
    token = request.cookies.get(_cfg.AUTH_COOKIE_NAME)
    user = store.get_user_by_token(token or "")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或登录已过期"
        )
    return UserInfo(**user)
