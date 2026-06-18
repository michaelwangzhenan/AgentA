"""Session 管理端点 —— 列表 / 创建 / 重命名 / 删除 / 拉历史消息"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_session_store, get_current_user
from src.api.schemas.session import (
    SessionCreateRequest,
    SessionDeleteResponse,
    SessionInfo,
    SessionListResponse,
    SessionMessagesResponse,
    SessionRenameRequest,
    SessionTruncateRequest,
    SessionTruncateResponse,
)
from src.stores.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()


_DEFAULT_SESSION_TITLE = "New Chat"


def _require_owned(store: SessionStore, session_id: str, user_id: int) -> None:
    """session 必须归属当前用户，否则 404（不暴露他人 session 是否存在）。"""
    if not store.owns_session(session_id, user_id):
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")


def _row_to_session_info(row: dict[str, Any]) -> SessionInfo:
    """SessionStore.list_sessions 行 → SessionInfo（空标题统一显示为 "New Chat"）"""
    sid: str = row["session_id"]
    raw_title: str = row.get("first_user_msg") or ""
    title = raw_title or _DEFAULT_SESSION_TITLE
    return SessionInfo(
        id=sid,
        title=title,
        created_at=row["created_at"],
        msg_count=int(row.get("msg_count") or 0),
    )


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    store: SessionStore = Depends(get_session_store),
    user: dict = Depends(get_current_user),
) -> SessionListResponse:
    """列出当前用户的 session，按 created_at 倒序。"""
    rows = store.list_sessions(user_id=user["id"])
    return SessionListResponse(sessions=[_row_to_session_info(r) for r in rows])


@router.post("/sessions", response_model=SessionInfo)
def create_session(
    req: SessionCreateRequest | None = None,
    store: SessionStore = Depends(get_session_store),
    user: dict = Depends(get_current_user),
) -> SessionInfo:
    """新建空 session（归属当前用户）。后端生成 uuid，返回完整元数据。"""
    session_id = str(uuid.uuid4())
    title = (req.title if req and req.title else "") or ""
    store.create_empty_session(session_id, title, user_id=user["id"])
    # 复用 list 路径拿 created_at（避免重复 datetime.now() 跟存储层不一致）
    rows = store.list_sessions(user_id=user["id"])
    for r in rows:
        if r["session_id"] == session_id:
            return _row_to_session_info(r)
    raise HTTPException(status_code=500, detail="created session not found in list")


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
def rename_session(
    session_id: str,
    req: SessionRenameRequest,
    store: SessionStore = Depends(get_session_store),
    user: dict = Depends(get_current_user),
) -> SessionInfo:
    """重命名 session。404 if 不存在或非本人所有。"""
    _require_owned(store, session_id, user["id"])
    ok = store.rename_session(session_id, req.title, user_id=user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    for r in store.list_sessions(user_id=user["id"]):
        if r["session_id"] == session_id:
            return _row_to_session_info(r)
    raise HTTPException(status_code=500, detail="renamed session disappeared")


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
def delete_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
    user: dict = Depends(get_current_user),
) -> SessionDeleteResponse:
    """硬删 session（级联清 messages + sessions 表）；非本人所有返回 deleted=False。"""
    if not store.owns_session(session_id, user["id"]):
        return SessionDeleteResponse(deleted=False)
    return SessionDeleteResponse(deleted=store.delete_session(session_id, user_id=user["id"]))


@router.post(
    "/sessions/{session_id}/truncate",
    response_model=SessionTruncateResponse,
)
def truncate_session(
    session_id: str,
    req: SessionTruncateRequest,
    store: SessionStore = Depends(get_session_store),
    user: dict = Depends(get_current_user),
) -> SessionTruncateResponse:
    """从第 N 条 user 消息起截断 session（编辑重发 / 重新生成的前置步骤）。

    截断后调用方再发 `POST /api/chat/stream`，Agent.run 会重新追加用户消息 + 新回答。
    """
    _require_owned(store, session_id, user["id"])
    deleted = store.truncate_from_user_message(
        session_id, req.user_message_index, user_id=user["id"]
    )
    return SessionTruncateResponse(deleted=deleted)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=SessionMessagesResponse,
)
def get_session_messages(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
    user: dict = Depends(get_current_user),
) -> SessionMessagesResponse:
    """拉某 session 的完整 messages 历史（OpenAI messages 格式，含 tool_calls）。

    session 不存在 → 返回空列表（保持幂等）；存在但归属他人 → 404。
    """
    owner = store.get_session_owner(session_id)
    if owner is not None and owner != user["id"]:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    # 必须显式传 user_id：本路由不设 current_user contextvar，load() 缺省会回落到
    # DEFAULT_USER_ID(1) 再做一次归属校验，非 1 号用户的 session 会被误判为不归属而返回空。
    messages = store.load(session_id, user_id=user["id"])
    return SessionMessagesResponse(messages=messages)
