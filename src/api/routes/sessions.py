"""Session 管理端点 —— 列表 / 创建 / 重命名 / 删除 / 拉历史消息"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_chat_history
from src.api.schemas.session import (
    SessionCreateRequest,
    SessionDeleteResponse,
    SessionInfo,
    SessionListResponse,
    SessionMessagesResponse,
    SessionRenameRequest,
)
from src.memory.chat_history import ChatHistoryStore

logger = logging.getLogger(__name__)

router = APIRouter()


_DEFAULT_SESSION_TITLE = "New Chat"


def _row_to_session_info(row: dict[str, Any]) -> SessionInfo:
    """ChatHistoryStore.list_sessions 行 → SessionInfo（空标题统一显示为 "New Chat"）"""
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
    store: ChatHistoryStore = Depends(get_chat_history),
) -> SessionListResponse:
    """全量列出 session，按 created_at 倒序。"""
    rows = store.list_sessions()
    return SessionListResponse(sessions=[_row_to_session_info(r) for r in rows])


@router.post("/sessions", response_model=SessionInfo)
def create_session(
    req: SessionCreateRequest | None = None,
    store: ChatHistoryStore = Depends(get_chat_history),
) -> SessionInfo:
    """新建空 session。后端生成 uuid，返回完整元数据。"""
    session_id = str(uuid.uuid4())
    title = (req.title if req and req.title else "") or ""
    store.create_empty_session(session_id, title)
    # 复用 list 路径拿 created_at（避免重复 datetime.now() 跟存储层不一致）
    rows = store.list_sessions()
    for r in rows:
        if r["session_id"] == session_id:
            return _row_to_session_info(r)
    raise HTTPException(status_code=500, detail="created session not found in list")


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
def rename_session(
    session_id: str,
    req: SessionRenameRequest,
    store: ChatHistoryStore = Depends(get_chat_history),
) -> SessionInfo:
    """重命名 session。404 if 不存在。"""
    ok = store.rename_session(session_id, req.title)
    if not ok:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    for r in store.list_sessions():
        if r["session_id"] == session_id:
            return _row_to_session_info(r)
    raise HTTPException(status_code=500, detail="renamed session disappeared")


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
def delete_session(
    session_id: str,
    store: ChatHistoryStore = Depends(get_chat_history),
) -> SessionDeleteResponse:
    """硬删 session（级联清 messages + sessions 表）；幂等：不存在返回 deleted=False。"""
    return SessionDeleteResponse(deleted=store.delete_session(session_id))


@router.get(
    "/sessions/{session_id}/messages",
    response_model=SessionMessagesResponse,
)
def get_session_messages(
    session_id: str,
    store: ChatHistoryStore = Depends(get_chat_history),
) -> SessionMessagesResponse:
    """拉某 session 的完整 messages 历史（OpenAI messages 格式，含 tool_calls）。"""
    messages = store.load(session_id)
    return SessionMessagesResponse(messages=messages)
