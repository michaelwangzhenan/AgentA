"""Chat 端点 —— 非流式（Step 1） + SSE 流式（Step 2）"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.agent.agent_api import AgentAPI
from src.api.deps import get_agent
from src.api.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Step 1：非流式（保留作为 fallback / 测试入口）─────────────────────────

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, agent: AgentAPI = Depends(get_agent)) -> ChatResponse:
    """单轮聊天：转发用户消息给 Agent.run、返回完整答案。

    同步路由（不加 async）—— FastAPI 会自动把它扔到 thread pool 跑，
    不阻塞 event loop。
    """
    try:
        reply = agent.run(req.message)
    except Exception as exc:
        logger.exception("[/api/chat] agent.run 抛异常")
        raise HTTPException(status_code=500, detail=f"agent error: {exc}") from exc

    return ChatResponse(reply=reply, session_id=agent.session_id)


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

    agent.set_event_callback(_on_event)

    # Agent.run 是同步阻塞，扔 thread pool；完事用 SENTINEL 通知 generator
    async def _drive_agent() -> None:
        try:
            await loop.run_in_executor(None, agent.run, req.message)
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
            # 客户端断开 / 流自然结束 / 异常退出 三种情形统一兜底：
            # 1. 解绑事件 callback（不解的话，下一轮请求来时旧 callback 还在 EventBus，
            #    引用的是上一轮已不再活跃的 queue —— 内存泄漏 + 事件丢错地方）
            # 2. cancel agent 驱动 task（仅取消 asyncio 包装层；executor 里同步的
            #    Agent.run 不会真停，但本期接受，留给后续）
            agent.set_event_callback(None)
            if not run_task.done():
                run_task.cancel()

    return EventSourceResponse(_event_gen())
