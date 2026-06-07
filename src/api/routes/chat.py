"""Chat 端点 —— 非流式（Step 1） + SSE 流式（Step 2）"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

import src.config as _cfg
from src.agent.agent_api import AgentAPI
from src.agent.core.user_context import use_user
from src.api.deps import get_agent, get_chat_history, get_current_user, get_user_store
from src.api.routes.auth import effective_llm_prefs
from src.api.schemas.chat import ChatRequest, ChatResponse
from src.memory.chat_history import ChatHistoryStore
from src.memory.user_store import UserStore

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_session_owner(store: ChatHistoryStore, session_id: str | None, user_id: int) -> None:
    """若 session 已存在且不属于当前用户 → 403。新 session（不存在）放行。"""
    if not session_id:
        return
    owner = store.get_session_owner(session_id)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")

# 进程级 Agent 单例共享 session_id / event_callback 等可变属性；并发请求
# 必须串行化进入 agent.run（含前后 set_event_callback / 设 session_id），
# 否则后到的请求会覆盖前一个还在跑的属性，导致 user_input 写错 session 等。
# 单用户场景几乎无感；牺牲并发换 thread-safety。
_AGENT_LOCK = threading.Lock()


# ─── Step 1：非流式（保留作为 fallback / 测试入口）─────────────────────────

@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    agent: AgentAPI = Depends(get_agent),
    user: dict = Depends(get_current_user),
    history: ChatHistoryStore = Depends(get_chat_history),
    users: UserStore = Depends(get_user_store),
) -> ChatResponse:
    """单轮聊天：转发用户消息给 Agent.run、返回完整答案。

    同步路由（不加 async）—— FastAPI 会自动把它扔到 thread pool 跑，
    不阻塞 event loop。`_AGENT_LOCK` 保证并发请求按到达顺序串行化执行。
    """
    _check_session_owner(history, req.session_id, user["id"])
    prefs = effective_llm_prefs(users, user["id"])
    # use_user 在锁内设当前用户（让 tools 调 store 落到本人数据），退出时复位，
    # 避免线程复用导致 user_id 残留到下个请求。use_llm_prefs 把该用户选的
    # 模型 / thinking 压进 contextvar，多用户互不干扰。
    with _AGENT_LOCK, use_user(user["id"]), _cfg.use_llm_prefs(
        prefs.active_model, prefs.thinking_enabled, prefs.thinking_budget
    ):
        if req.session_id:
            agent.session_id = req.session_id
        try:
            reply = agent.run(req.message)
        except Exception as exc:
            logger.exception("[/api/chat] agent.run 抛异常")
            raise HTTPException(status_code=500, detail=f"agent error: {exc}") from exc
        # 在锁内读 session_id，避免下一请求改 agent.session_id 后读到错值
        session_id = agent.session_id

    return ChatResponse(reply=reply, session_id=session_id)


# ─── Step 2：SSE 流式 ────────────────────────────────────────────────────

_STREAM_SENTINEL: dict[str, Any] = {"__sentinel__": True}


def _sanitize_payload(value: Any) -> Any:
    """递归把 AgentEvent payload 转成 JSON-friendly 结构。

    NamedTuple（如 `TokenUsage`）→ dict；dict / list / tuple 递归处理；
    其余类型原样返回（让 json.dumps 自己处理，遇到不支持再 fallback 到 str）。
    NamedTuple 也是 tuple，必须先于 tuple 判断（用 `_asdict` duck typing）。
    """
    if hasattr(value, "_asdict"):  # NamedTuple
        return {k: _sanitize_payload(v) for k, v in value._asdict().items()}
    if isinstance(value, dict):
        return {k: _sanitize_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(v) for v in value]
    return value


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    agent: AgentAPI = Depends(get_agent),
    user: dict = Depends(get_current_user),
    history: ChatHistoryStore = Depends(get_chat_history),
    users: UserStore = Depends(get_user_store),
) -> EventSourceResponse:
    """SSE 流式聊天：Agent.run 扔 thread pool，事件经 asyncio.Queue 流给 SSE。

    帧格式：`event: message` + `data: {"type": "<event_type>", "payload": {...}}`
    其中 `<event_type>` 取值参见 `src.agent.core.event_bus.ALL_EVENT_TYPES`。
    收到 `final_answer` 或 Agent.run 结束（含异常） → 流自动关闭。
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=422, detail="message must be non-empty")

    _check_session_owner(history, req.session_id, user["id"])
    # 在请求线程算好该用户生效偏好，闭包带进 executor 线程内设置 contextvar
    prefs = effective_llm_prefs(users, user["id"])

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    # 同步事件回调（运行在 executor 线程）→ 经 call_soon_threadsafe 转回主 loop 入队
    # asyncio.Queue 非线程安全，必须 call_soon_threadsafe 调度 put_nowait
    def _on_event(event: Any) -> None:
        try:
            frame = {
                "type": event.type,
                "payload": _sanitize_payload(event.payload),
            }
        except Exception:
            logger.exception("[/api/chat/stream] sanitize 事件失败 type=%s", getattr(event, "type", "?"))
            return
        loop.call_soon_threadsafe(queue.put_nowait, frame)

    # 整段 agent 交互（设 session_id / 装 callback / run / 卸 callback）必须在锁内
    # 一次完成，避免被其他并发请求穿插覆盖 agent 共享属性。
    def _sync_run() -> None:
        # contextvar 不随 run_in_executor 传播，必须在 executor 线程内重设；
        # use_user / use_llm_prefs 退出时复位，避免值残留到复用该线程的下个请求。
        with _AGENT_LOCK, use_user(user["id"]), _cfg.use_llm_prefs(
            prefs.active_model, prefs.thinking_enabled, prefs.thinking_budget
        ):
            if req.session_id:
                agent.session_id = req.session_id
            agent.set_event_callback(_on_event)
            try:
                agent.run(req.message)
            finally:
                agent.set_event_callback(None)

    async def _drive_agent() -> None:
        try:
            await loop.run_in_executor(None, _sync_run)
        except Exception as exc:
            logger.exception("[/api/chat/stream] agent.run 抛异常")
            await queue.put({
                "type": "error",
                "payload": {
                    "message": str(exc),
                    "recoverable": False,
                    "phase": "run",
                },
            })
        finally:
            await queue.put(_STREAM_SENTINEL)

    run_task = asyncio.create_task(_drive_agent())

    async def _event_gen():
        try:
            while True:
                item = await queue.get()
                if item is _STREAM_SENTINEL:
                    break
                yield {
                    "event": "message",
                    "data": json.dumps(item, ensure_ascii=False),
                }
        finally:
            # cancel 仅取消 asyncio 包装层；executor 里同步的 agent.run 不会真停，
            # 仍持有锁直到自然结束。callback 解绑已由 _sync_run finally 兜底。
            if not run_task.done():
                run_task.cancel()

    return EventSourceResponse(_event_gen())
