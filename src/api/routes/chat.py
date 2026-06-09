"""Chat 端点 —— 非流式 + SSE 流式"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

import src.config as _cfg
from src.agent.agent_api import AgentAPI
from src.core.user_context import use_user
from src.api.deps import get_agent, get_chat_history, get_current_user, get_user_store
from src.api.routes.auth import effective_llm_prefs
from src.api.schemas.chat import ChatRequest, ChatResponse
from src.api.schemas.auth import LlmPrefs
from src.memory.chat_history import ChatHistoryStore
from src.memory.usage_store import record_usage
from src.memory.user_store import UserStore
from src.log_setup import set_session_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_session_owner(store: ChatHistoryStore, session_id: str | None, user_id: int) -> None:
    """若 session 已存在且不属于当前用户 → 403。新 session（不存在）放行。"""
    if not session_id:
        return
    owner = store.get_session_owner(session_id)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")

# 进程级 Agent 单例可被多请求并发调用：session_id / 事件回调都改为 agent.run 的
# per-run 入参（不再写共享实例属性），所以无需串行化。仅用信号量限制同时在跑的
# run 数，避免并发把 LLM 配额 / CPU（含 search_knowledge 精排）打满；超出的请求排队。
_AGENT_SEMAPHORE = threading.BoundedSemaphore(_cfg.MAX_CONCURRENT_AGENT_RUNS)


def _make_usage_capture() -> tuple[dict[str, Any], Any]:
    """返回 (holder, callback)：callback 抓 final_answer 事件里的 per-run TokenUsage。

    这是 iter_11 §4.1 的"公共采集点"——只认 AgentAPI 的 final_answer 事件契约，
    三种实现（PYTHON / LANGCHAIN / AUTOGPT）都满足，故对实现零侵入。
    """
    holder: dict[str, Any] = {}

    def _capture(event: Any) -> None:
        if getattr(event, "type", None) == "final_answer":
            payload = getattr(event, "payload", None) or {}
            holder["usage"] = payload.get("usage")

    return holder, _capture


def _record_run_usage(
    user_id: int, prefs: LlmPrefs, usage: Any, session_id: str
) -> None:
    """把本次 run 的用量落库（旁路，record_usage 内部已吞异常）。"""
    record_usage(
        user_id=user_id,
        model_id=prefs.active_model,
        thinking=prefs.thinking_enabled,
        usage=usage,
        session_id=session_id,
    )


# ─── 非流式（保留作为 fallback / 测试入口）─────────────────────────

@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    agent: AgentAPI = Depends(get_agent),
    user: dict = Depends(get_current_user),
    history: ChatHistoryStore = Depends(get_chat_history),
    users: UserStore = Depends(get_user_store),
) -> ChatResponse:
    """单轮聊天：转发用户消息给 Agent.run、返回完整答案。

    同步路由（不加 async）—— FastAPI 会自动把它扔到 thread pool 跑，不阻塞 event loop。
    session_id 由调用方生成并作为 per-run 入参传给 agent.run，单例 Agent 不被写脏，
    并发请求互不串台；`_AGENT_SEMAPHORE` 仅做并发数限流。
    """
    _check_session_owner(history, req.session_id, user["id"])
    prefs = effective_llm_prefs(users, user["id"])
    # 调用方生成 session_id（空则新建 uuid），不再回读 agent.session_id —— 既避免并发
    # 串台，也修掉"新会话复用单例构造期 uuid"的旧隐患。
    session_id = req.session_id or str(uuid.uuid4())
    # use_user 设当前用户（让 tools 调 store 落到本人数据），退出时复位，避免线程复用
    # 导致 user_id 残留到下个请求。use_llm_prefs 把该用户选的模型 / thinking 压进
    # contextvar，多用户互不干扰。
    # 通过 final_answer 事件抓 per-run usage（公共采集点，三实现通用）
    usage_holder, usage_cb = _make_usage_capture()
    with _AGENT_SEMAPHORE, use_user(user["id"]), _cfg.use_llm_prefs(
        prefs.active_model, prefs.thinking_enabled, prefs.thinking_budget
    ):
        try:
            reply = agent.run(req.message, session_id=session_id, event_callback=usage_cb)
        except Exception as exc:
            logger.exception("[/api/chat] agent.run 抛异常")
            raise HTTPException(status_code=500, detail=f"agent error: {exc}") from exc

    _record_run_usage(user["id"], prefs, usage_holder.get("usage"), session_id)
    return ChatResponse(reply=reply, session_id=session_id)


# ─── SSE 流式 ────────────────────────────────────────────────────

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
    # 调用方生成 session_id（空则新建 uuid），作为 per-run 入参传给 agent.run
    session_id = req.session_id or str(uuid.uuid4())

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    # 同步事件回调（运行在 executor 线程）→ 经 call_soon_threadsafe 转回主 loop 入队
    # asyncio.Queue 非线程安全，必须 call_soon_threadsafe 调度 put_nowait
    # 顺带抓 per-run usage（final_answer 事件），run 结束后落库（公共采集点）
    usage_holder: dict[str, Any] = {}

    def _on_event(event: Any) -> None:
        if getattr(event, "type", None) == "final_answer":
            payload = getattr(event, "payload", None) or {}
            usage_holder["usage"] = payload.get("usage")
        try:
            frame = {
                "type": event.type,
                "payload": _sanitize_payload(event.payload),
            }
        except Exception:
            logger.exception("[/api/chat/stream] sanitize 事件失败 type=%s", getattr(event, "type", "?"))
            return
        loop.call_soon_threadsafe(queue.put_nowait, frame)

    # session_id 与事件回调都作为 per-run 入参传进 agent.run（不写共享实例属性），
    # 多请求并发互不串台；信号量仅限流。
    def _sync_run() -> None:
        # contextvar 不随 run_in_executor 传播，必须在 executor 线程内重设；
        # use_user / use_llm_prefs 退出时复位，避免值残留到复用该线程的下个请求。
        with _AGENT_SEMAPHORE, use_user(user["id"]), _cfg.use_llm_prefs(
            prefs.active_model, prefs.thinking_enabled, prefs.thinking_budget
        ):
            # 把当前 session 写进日志上下文（ContextVar，线程内有效），
            # 使本次 agent.run 期间的日志带 s:<session>
            set_session_id(session_id)
            agent.run(req.message, session_id=session_id, event_callback=_on_event)

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
            # run 结束（含异常）后落库本次 usage；usage 缺失则内部跳过
            _record_run_usage(user["id"], prefs, usage_holder.get("usage"), session_id)
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
            # 会自然跑完并释放信号量。事件回调是本次 run 的局部 bus，run 结束即失效。
            if not run_task.done():
                run_task.cancel()

    return EventSourceResponse(_event_gen())
