"""
Chat 端点：非流式与 SSE 流式对话；请求前可走语义缓存与模型路由（均软失败，不阻断对话）。

- POST /api/chat：非流式对话
- POST /api/chat/stream：SSE 流式对话
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

import src.config as _cfg
from src.agent.agent_api import AgentAPI
from src.agent.core import run_cancel
from src.api.sse_outbound import SseOutbound
from src.stores.user_context import use_user
from src.api.deps import get_agent, get_session_store, get_current_user, get_user_store
from src.api.routes.auth import effective_llm_prefs
from src.api.schemas.chat import ChatRequest, ChatResponse, assert_message_within_limit
from src.llm import model_router
from src.llm.model_router import RouteDecision
from src.stores import semantic_cache
from src.stores.session_store import SessionStore
from src.stores.trace_store import TraceCollector, record_trace_safe
from src.stores.usage_store import (
    cost_of,
    get_shared_store,
    merged_pricing,
    record_cache_lookup,
    record_saving,
    record_usage,
)
from src.stores.user_store import UserStore
from src.services.log_setup import set_session_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _check_session_owner(store: SessionStore, session_id: str | None, user_id: int) -> None:
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

# 视为"瞬时"的 LLM 错误（限流 / 网关 / 超时）→ 路由降级模型遇此可回退基准重试。
# 4xx 只认 408 超时 / 425 too-early / 429 限流，其余 4xx 多是请求本身错，不重试。
_TRANSIENT_STATUS = {408, 425, 429}

# 可缓存的"只读检索"工具：用了这些仍可写缓存（KB 变更会全量作废缓存兜底）。
# 联网搜索 / 文件写 / MCP 等带实时性或副作用的工具一律不可缓存。
_CACHEABLE_TOOLS = {"search_knowledge"}


def _is_transient_llm_error(exc: Exception) -> bool:
    """判断 LLM 调用异常是否瞬时（可换模型重试）；保守起见 400/401/403 等不算。"""
    sc = getattr(exc, "status_code", None)
    if sc is None:
        sc = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(sc, int) and (sc in _TRANSIENT_STATUS or 500 <= sc <= 599):
        return True
    name = type(exc).__name__.lower()
    return any(
        k in name
        for k in ("timeout", "ratelimit", "internalserver", "serviceunavailable", "apiconnection")
    )


def _estimate_tokens(text: str) -> int:
    """粗估 token 数（约 4 字符/token），仅用于缓存命中的节省估算。"""
    return max(1, len(text or "") // 4)


def _is_fresh_session(history: SessionStore, session_id: str, user_id: int) -> bool:
    """会话此前无消息 = 单轮起步，才允许语义缓存查询 / 写入。"""
    try:
        return not history.load_last_n_messages(session_id, n=1, user_id=user_id)
    except Exception:
        return False


def _make_usage_capture() -> tuple[dict[str, Any], Any]:
    """返回 (holder, callback)：从 final_answer 事件抓 usage / 答案文本 / 可缓存标记。

    只认 AgentAPI 的 final_answer 事件契约（三种实现通用），对实现零侵入。
    """
    holder: dict[str, Any] = {}

    def _capture(event: Any) -> None:
        et = getattr(event, "type", None)
        if et == "tool_call_start":
            name = (getattr(event, "payload", None) or {}).get("name")
            if name:
                holder.setdefault("tool_names", set()).add(name)
        elif et == "final_answer":
            payload = getattr(event, "payload", None) or {}
            holder["usage"] = payload.get("usage")
            holder["text"] = payload.get("text")
            holder["used_tools"] = bool(payload.get("used_tools"))
            holder["personalized"] = bool(payload.get("personalized"))

    return holder, _capture


def _record_run_usage(
    user_id: int, model_id: str, thinking: bool, usage: Any, session_id: str
) -> None:
    """把本次 run 的用量落库（旁路，record_usage 内部已吞异常）。model_id 取实际跑的模型。"""
    record_usage(
        user_id=user_id,
        model_id=model_id,
        thinking=thinking,
        usage=usage,
        session_id=session_id,
    )


def _record_route_saving(user_id: int, decision: RouteDecision, usage: Any) -> None:
    """路由降级的估算节省 = 用基准模型的成本 − 实际成本（按本次实际 token）。"""
    if not decision.downgraded or usage is None:
        return
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    comp = int(getattr(usage, "completion_tokens", 0) or 0)
    if prompt == 0 and comp == 0:
        return
    pricing = merged_pricing(get_shared_store())
    saved = (cost_of(decision.baseline, prompt, comp, pricing)
             - cost_of(decision.model_id, prompt, comp, pricing))
    if saved > 0:
        record_saving(user_id, "route", decision.baseline, decision.model_id, saved, prompt + comp)


def _estimate_cache_saving(would_use_model: str, query: str, answer: str) -> float:
    """缓存命中的估算节省 = 本应用 would_use_model 完整生成的成本（按答案长度粗估）。"""
    pricing = merged_pricing(get_shared_store())
    prompt = _estimate_tokens(query)
    comp = _estimate_tokens(answer)
    return cost_of(would_use_model, prompt, comp, pricing)


def _log_cache_skip_query(fresh: bool, skip_cache: bool, is_deep: bool = False) -> None:
    """记录"为何不查缓存"，便于排查"两轮相同问题为何没命中"。整体关了就别刷屏。"""
    if not _cfg.SEMANTIC_CACHE_ENABLED:
        return
    if is_deep:
        logger.info("[cache] 跳过查询：Deep Research（永不缓存）")
    elif skip_cache:
        logger.info("[cache] 跳过查询：重新生成（skip_cache，强制重答）")
    elif not fresh:
        logger.info("[cache] 跳过查询：非单轮起步（会话已有历史，仅开场问题查缓存）")


def _maybe_store_cache(
    cache_on: bool, holder: dict[str, Any], query: str, user_id: int, model_id: str
) -> None:
    """run 结束后按可缓存条件写语义缓存：单轮起步 + 无工具 + 未注入个性化 + 有答案。

    不满足时记一条 info 说明原因 —— 否则用户只看到"下次还是没命中"，无从排查。
    """
    if not cache_on:
        return  # 查询阶段已记过"为何不查"，写入沿用同一判定，不再重复刷屏
    answer = holder.get("text")
    if not answer:
        logger.info("[cache] 不写入：本轮无最终答案")
        return
    if holder.get("personalized"):
        logger.info("[cache] 不写入：本轮注入了个性化（答案因人而异，不缓存）")
        return
    # 工具判定：只有"全是只读检索工具"才可缓存；联网 / 写操作等不可缓存。
    # 拿不到工具名单却报 used_tools（非默认实现）时保守不写。
    tool_names = holder.get("tool_names") or set()
    if holder.get("used_tools") or tool_names:
        non_cacheable = (tool_names - _CACHEABLE_TOOLS) if tool_names else {"<未知工具>"}
        if non_cacheable:
            logger.info(
                "[cache] 不写入：本轮用了不可缓存的工具 %s（仅纯检索可缓存）", sorted(non_cacheable)
            )
            return
    semantic_cache.store_cached(query, answer, user_id, model_id=model_id)


# ─── 非流式（保留作为 fallback / 测试入口）─────────────────────────

@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    agent: AgentAPI = Depends(get_agent),
    user: dict = Depends(get_current_user),
    history: SessionStore = Depends(get_session_store),
    users: UserStore = Depends(get_user_store),
) -> ChatResponse:
    """单轮聊天：先查缓存 → 路由选模型 → Agent.run（带 fallback）→ 写缓存 + 记节省。

    同步路由（不加 async）—— FastAPI 会自动把它扔到 thread pool 跑，不阻塞 event loop。
    """
    _check_session_owner(history, req.session_id, user["id"])
    assert_message_within_limit(req.message)
    prefs = effective_llm_prefs(users, user["id"])
    session_id = req.session_id or str(uuid.uuid4())
    fresh = _is_fresh_session(history, session_id, user["id"])

    decision = model_router.route(req.message, prefs.active_model)
    used_model = decision.model_id
    logger.info("[/api/chat] 路由：模型=%s（%s）", used_model, decision.reason)

    # 语义缓存：单轮起步先查，命中即跳过整次 run（「重新生成」勾 skip_cache 时不查不写）
    cache_on = _cfg.SEMANTIC_CACHE_ENABLED and fresh and not req.skip_cache
    if not cache_on:
        _log_cache_skip_query(fresh, req.skip_cache)
    if cache_on:
        cached = semantic_cache.lookup_cached(req.message, user["id"])
        # 命中 / 未命中 + 命中估算节省一并记进 cache_lookups（缓存统计唯一口径）
        saved = _estimate_cache_saving(used_model, req.message, cached) if cached is not None else 0.0
        record_cache_lookup(user["id"], cached is not None, saved=saved)
        if cached is not None:
            history.append(session_id, {"role": "user", "content": req.message}, user_id=user["id"])
            history.append(session_id, {"role": "assistant", "content": cached}, user_id=user["id"])
            return ChatResponse(reply=cached, session_id=session_id, model="", cached=True)

    trace_id = str(uuid.uuid4())

    def _run_once(model: str) -> tuple[str, dict[str, Any], TraceCollector]:
        holder, ucb = _make_usage_capture()
        collector = TraceCollector()

        def _cb(event: Any) -> None:
            ucb(event)
            collector.on_event(event)

        with _AGENT_SEMAPHORE, use_user(user["id"]), _cfg.use_llm_prefs(
            model, prefs.thinking_enabled, prefs.thinking_budget
        ):
            r = agent.run(req.message, session_id=session_id, event_callback=_cb)
        return r, holder, collector

    try:
        reply, usage_holder, collector = _run_once(used_model)
    except Exception as exc:
        if fresh and decision.downgraded and _is_transient_llm_error(exc):
            logger.warning(
                "[/api/chat] 路由模型 %s 瞬时失败，回退基准 %s 重试：%s",
                used_model, decision.baseline, exc,
            )
            history.clear(session_id, user_id=user["id"])
            used_model = decision.baseline
            try:
                reply, usage_holder, collector = _run_once(used_model)
            except Exception as exc2:
                logger.exception("[/api/chat] fallback 仍失败")
                raise HTTPException(status_code=500, detail=f"agent error: {exc2}") from exc2
        else:
            logger.exception("[/api/chat] agent.run 抛异常")
            raise HTTPException(status_code=500, detail=f"agent error: {exc}") from exc

    usage = usage_holder.get("usage")
    _record_run_usage(user["id"], used_model, prefs.thinking_enabled, usage, session_id)
    record_trace_safe(
        collector, trace_id, user["id"], session_id, used_model, prefs.thinking_enabled,
    )
    if used_model == decision.model_id:  # 未 fallback 才记路由节省
        _record_route_saving(user["id"], decision, usage)
    _maybe_store_cache(cache_on, usage_holder, req.message, user["id"], used_model)
    return ChatResponse(reply=reply, session_id=session_id, model=used_model, cached=False)


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


def _sse_frame(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"event": "message", "data": json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False)}


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    agent: AgentAPI = Depends(get_agent),
    user: dict = Depends(get_current_user),
    history: SessionStore = Depends(get_session_store),
    users: UserStore = Depends(get_user_store),
) -> EventSourceResponse:
    """SSE 流式聊天：Agent.run 在 executor 线程执行，事件经 Queue 推给客户端。

    每帧 event=message，data 为 JSON（含 type、payload）。运行结束（含异常）后发sentinel 关流。
    语义缓存命中时只发 token_chunk + final_answer 两帧（payload.cached=True），不启动 Agent。
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=422, detail="message must be non-empty")
    assert_message_within_limit(req.message)

    _check_session_owner(history, req.session_id, user["id"])
    prefs = effective_llm_prefs(users, user["id"])
    session_id = req.session_id or str(uuid.uuid4())
    fresh = _is_fresh_session(history, session_id, user["id"])

    # Deep Research：重质量不重速度 —— 跳过语义缓存（多源研究永不可缓存）+ 跳过模型降级
    # 路由（不向下换便宜模型），用用户选定 / 基准模型。
    is_deep = bool(req.mode == "deep_research" and _cfg.DEEP_RESEARCH_ENABLED)
    if is_deep:
        # 跳过难度分类 / 降级路由（enabled=False 不调分类 LLM），但仍要把 auto 解析成
        # 具体模型 —— 否则 "auto" 会原样传到 chat() / get_active_model() 解析失败。
        base = model_router.route(req.message, prefs.active_model, enabled=False)
        used_model = base.model_id
        decision = RouteDecision(
            model_id=used_model, baseline=used_model, downgraded=False,
            difficulty="-", mode="deep_research", reason="deep_research: 跳过路由",
        )
        logger.info("[/api/chat/stream] Deep Research：跳过缓存 / 路由，模型=%s", used_model)
    else:
        decision = model_router.route(req.message, prefs.active_model)
        used_model = decision.model_id
        logger.info("[/api/chat/stream] 路由：模型=%s（%s）", used_model, decision.reason)

    # 语义缓存命中：直接两帧返回，不跑 agent（Deep Research / skip_cache 不查缓存）
    cache_on = _cfg.SEMANTIC_CACHE_ENABLED and fresh and not is_deep and not req.skip_cache
    if not cache_on:
        _log_cache_skip_query(fresh, req.skip_cache, is_deep)
    if cache_on:
        cached = semantic_cache.lookup_cached(req.message, user["id"])
        saved = _estimate_cache_saving(used_model, req.message, cached) if cached is not None else 0.0
        record_cache_lookup(user["id"], cached is not None, saved=saved)
        if cached is not None:
            history.append(session_id, {"role": "user", "content": req.message}, user_id=user["id"])
            history.append(session_id, {"role": "assistant", "content": cached}, user_id=user["id"])

            async def _cached_gen():
                yield _sse_frame("token_chunk", {"text": cached})
                yield _sse_frame("final_answer", {"text": cached, "usage": None, "cached": True})

            return EventSourceResponse(_cached_gen(), headers=_SSE_HEADERS)

    loop = asyncio.get_running_loop()
    cancel_event = threading.Event()
    outbound = SseOutbound(
        loop,
        maxsize=_cfg.SSE_QUEUE_MAXSIZE,
        merge_max_chars=_cfg.SSE_TOKEN_MERGE_MAX_CHARS,
        merge_interval_s=_cfg.SSE_TOKEN_MERGE_INTERVAL_MS / 1000.0,
    )
    queue = outbound.queue
    trace_id = str(uuid.uuid4())

    usage_holder: dict[str, Any] = {}
    # fallback 期间把第一次尝试的 error 帧暂存，不立即下发；真正失败时才 flush，
    # 成功 fallback 则丢弃，避免用户先看到一条 error 又看到正常答案。
    _state: dict[str, Any] = {
        "collector": TraceCollector(),
        "held_errors": [],
        "suppress": fresh and decision.downgraded and _cfg.MODEL_ROUTING_ENABLED,
    }

    def _on_event(event: Any) -> None:
        et = getattr(event, "type", None)
        if et == "tool_call_start":
            name = (getattr(event, "payload", None) or {}).get("name")
            if name:
                usage_holder.setdefault("tool_names", set()).add(name)
        elif et == "final_answer":
            payload = getattr(event, "payload", None) or {}
            usage_holder["usage"] = payload.get("usage")
            usage_holder["text"] = payload.get("text")
            usage_holder["used_tools"] = bool(payload.get("used_tools"))
            usage_holder["personalized"] = bool(payload.get("personalized"))
        _state["collector"].on_event(event)
        try:
            frame = {"type": event.type, "payload": _sanitize_payload(event.payload)}
        except Exception:
            logger.exception("[/api/chat/stream] sanitize 事件失败 type=%s", getattr(event, "type", "?"))
            return
        # 透明度：在 final_answer 帧带上本次实际应答模型 + 是否被降级，供前端气泡标注
        if et == "final_answer" and isinstance(frame.get("payload"), dict):
            frame["payload"]["model"] = used_model
            frame["payload"]["downgraded"] = bool(
                decision.downgraded and used_model == decision.model_id
            )
        if et == "error" and _state["suppress"]:
            _state["held_errors"].append(frame)
            return
        outbound.enqueue_from_thread(frame)

    def _run_with(model: str) -> None:
        with run_cancel.cancel_scope(cancel_event):
            with _cfg.use_llm_prefs(model, prefs.thinking_enabled, prefs.thinking_budget):
                set_session_id(session_id)
                if is_deep:
                    from src.agent.core.research_engine import ResearchEngine
                    ResearchEngine(history, user["id"]).run(
                        req.message, session_id=session_id, event_callback=_on_event,
                    )
                else:
                    agent.run(req.message, session_id=session_id, event_callback=_on_event)

    def _sync_run() -> None:
        nonlocal used_model
        with _AGENT_SEMAPHORE, use_user(user["id"]):
            try:
                _run_with(used_model)
            except Exception as exc:
                if _state["suppress"] and _is_transient_llm_error(exc):
                    logger.warning(
                        "[/api/chat/stream] 路由模型 %s 瞬时失败，回退基准 %s 重试：%s",
                        used_model, decision.baseline, exc,
                    )
                    history.clear(session_id, user_id=user["id"])
                    _state["held_errors"].clear()
                    _state["suppress"] = False
                    _state["collector"] = TraceCollector()
                    usage_holder.clear()
                    used_model = decision.baseline
                    _run_with(used_model)  # 再失败则向上抛
                else:
                    raise

    async def _drive_agent() -> None:
        try:
            await loop.run_in_executor(None, _sync_run)
        except Exception as exc:
            logger.exception("[/api/chat/stream] agent.run 抛异常")
            for f in _state["held_errors"]:
                outbound.enqueue_now(f)
            outbound.enqueue_now({
                "type": "error",
                "payload": {"message": str(exc), "recoverable": False, "phase": "run"},
            })
        finally:
            _record_run_usage(
                user["id"], used_model, prefs.thinking_enabled,
                usage_holder.get("usage"), session_id,
            )
            record_trace_safe(
                _state["collector"], trace_id, user["id"], session_id,
                used_model, prefs.thinking_enabled,
            )
            if used_model == decision.model_id:
                _record_route_saving(user["id"], decision, usage_holder.get("usage"))
            _maybe_store_cache(cache_on, usage_holder, req.message, user["id"], used_model)
            # flush 合并缓冲后再发 sentinel，避免尾部 token 被 close 丢掉
            outbound._flush_pending()
            await queue.put(_STREAM_SENTINEL)

    run_task = asyncio.create_task(_drive_agent())

    async def _watch_disconnect() -> None:
        while not cancel_event.is_set():
            if await request.is_disconnected():
                cancel_event.set()
                logger.info("[/api/chat/stream] 客户端断开，请求协作式取消")
                return
            await asyncio.sleep(0.2)

    disconnect_task = asyncio.create_task(_watch_disconnect())

    async def _event_gen():
        try:
            while True:
                if cancel_event.is_set() and queue.empty() and run_task.done():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    if cancel_event.is_set() and run_task.done():
                        break
                    continue
                if item is _STREAM_SENTINEL:
                    break
                yield {
                    "event": "message",
                    "data": json.dumps(item, ensure_ascii=False),
                }
        finally:
            cancel_event.set()
            disconnect_task.cancel()
            outbound.close()
            dropped = outbound.drain()
            if dropped:
                logger.debug("[/api/chat/stream] 断开后清空队列 %d 条", dropped)
            if not run_task.done():
                run_task.cancel()

    return EventSourceResponse(_event_gen(), headers=_SSE_HEADERS)
