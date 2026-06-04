"""Chat 端点 —— 非流式（Step 1） + SSE 流式（Step 2）"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.agent.agent_api import AgentAPI
from src.api.deps import get_agent
from src.api.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# 进程级 Agent 单例共享 session_id / event_callback 等可变属性；并发请求
# 必须串行化进入 agent.run（含前后 set_event_callback / 设 session_id），
# 否则后到的请求会覆盖前一个还在跑的属性，导致 user_input 写错 session 等。
# 单用户场景几乎无感；牺牲并发换 thread-safety。
_AGENT_LOCK = threading.Lock()


# ─── Step 1：非流式（保留作为 fallback / 测试入口）─────────────────────────

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, agent: AgentAPI = Depends(get_agent)) -> ChatResponse:
    """单轮聊天：转发用户消息给 Agent.run、返回完整答案。

    同步路由（不加 async）—— FastAPI 会自动把它扔到 thread pool 跑，
    不阻塞 event loop。`_AGENT_LOCK` 保证并发请求按到达顺序串行化执行。
    """
    with _AGENT_LOCK:
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
    req: ChatRequest, agent: AgentAPI = Depends(get_agent)
) -> EventSourceResponse:
    """SSE 流式聊天：Agent.run 扔 thread pool，事件经 asyncio.Queue 流给 SSE。

    帧格式：`event: message` + `data: {"type": "<event_type>", "payload": {...}}`
    其中 `<event_type>` 取值参见 `src.agent.core.event_bus.ALL_EVENT_TYPES`。
    收到 `final_answer` 或 Agent.run 结束（含异常） → 流自动关闭。
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=422, detail="message must be non-empty")

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
        with _AGENT_LOCK:
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
