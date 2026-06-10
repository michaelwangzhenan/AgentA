"""
EventBus —— 统一流式事件分发（Helper 层）

职责：把 Agent loop 内的各类流式事件（thinking_chunk / token_chunk / tool_call_start /
tool_call_end / final_answer / error / info）按事件类型分发给若干订阅者。

设计要点：
- **强类型 payload**：`publish(event: AgentEvent)` 单参签名，避免 (type_str, payload)
  二元组散落代码各处导致字段漂移
- **多订阅**：同一事件可有多个订阅者（CLI render + Web UI 推送 + 日志记录共存）
- **异常隔离**：单个订阅者抛异常不影响其他订阅者，吞掉并 log.warning

被三种 Agent 实现共享：Python / LangChain / AutoGPT。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 事件类型字符串常量
EVENT_THINKING_CHUNK = "thinking_chunk"
EVENT_TOKEN_CHUNK = "token_chunk"
EVENT_TOOL_CALL_START = "tool_call_start"
EVENT_TOOL_CALL_END = "tool_call_end"
# 工具内部阶段进度（如 search_knowledge 的 改写中/检索中/校验中），仅用于 UI 感知
EVENT_TOOL_PROGRESS = "tool_progress"
EVENT_FINAL_ANSWER = "final_answer"
EVENT_ERROR = "error"
EVENT_INFO = "info"
# Plan-Execute 三类事件
EVENT_PLAN_CREATED = "plan_created"
EVENT_PLAN_STEP_START = "plan_step_start"
EVENT_PLAN_STEP_END = "plan_step_end"
# Deep Research 四阶段进度事件（仅 ResearchEngine 发；旧端收到未知类型忽略）
EVENT_RESEARCH_STARTED = "research_started"
EVENT_RESEARCH_PLAN = "research_plan"
EVENT_RESEARCH_SUBAGENT_START = "research_subagent_start"
EVENT_RESEARCH_SUBAGENT_PROGRESS = "research_subagent_progress"
EVENT_RESEARCH_SUBAGENT_END = "research_subagent_end"
EVENT_RESEARCH_REFLECT = "research_reflect"
EVENT_RESEARCH_SYNTHESIZING = "research_synthesizing"

ALL_EVENT_TYPES: tuple[str, ...] = (
    EVENT_THINKING_CHUNK,
    EVENT_TOKEN_CHUNK,
    EVENT_TOOL_CALL_START,
    EVENT_TOOL_CALL_END,
    EVENT_TOOL_PROGRESS,
    EVENT_FINAL_ANSWER,
    EVENT_ERROR,
    EVENT_INFO,
    EVENT_PLAN_CREATED,
    EVENT_PLAN_STEP_START,
    EVENT_PLAN_STEP_END,
    EVENT_RESEARCH_STARTED,
    EVENT_RESEARCH_PLAN,
    EVENT_RESEARCH_SUBAGENT_START,
    EVENT_RESEARCH_SUBAGENT_PROGRESS,
    EVENT_RESEARCH_SUBAGENT_END,
    EVENT_RESEARCH_REFLECT,
    EVENT_RESEARCH_SYNTHESIZING,
)


@dataclass(frozen=True)
class AgentEvent:
    """
    统一事件对象。

    Fields:
        type:    事件类型字符串，取自 `ALL_EVENT_TYPES`
        payload: 事件载荷 dict —— 各事件类型 payload schema 由发射方约定
        ts:      Unix 时间戳（秒，float），由发射方填或自动 default
    """
    type: str
    payload: Any
    ts: float = field(default_factory=time.time)


Subscriber = Callable[[Any], None]


class EventBus:
    """
    简易事件总线：按事件类型维护订阅者列表，`publish` 时逐一调用并隔离异常。

    设计取舍：不引入 asyncio / queue，订阅者同步执行。
    UI 层若需要异步推送，自行把 publish 转成 asyncio.Queue.put_nowait。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = {}

    # ── 订阅管理 ────────────────────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        """注册订阅者。同一 handler 多次 subscribe 会被记录多次（调用方负责去重）。"""
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Subscriber) -> bool:
        """
        取消订阅。返回是否真的移除了一个 handler。

        若同一 handler 被注册多次，仅移除第一个匹配；未找到返回 False（不抛异常）。
        """
        handlers = self._subscribers.get(event_type)
        if not handlers:
            return False
        try:
            handlers.remove(handler)
            return True
        except ValueError:
            return False

    def clear(self, event_type: str | None = None) -> None:
        """清空指定事件的全部订阅者；不传则清空全部事件类型。"""
        if event_type is None:
            self._subscribers.clear()
        else:
            self._subscribers.pop(event_type, None)

    def subscribers(self, event_type: str) -> list[Subscriber]:
        """返回指定事件的订阅者副本（便于测试与诊断）。"""
        return list(self._subscribers.get(event_type, ()))

    # ── 事件分发 ────────────────────────────────────────────────────────────

    def publish(self, event: AgentEvent) -> None:
        """
        把 `event` 派发给所有订阅了 `event.type` 的 handler。

        订阅者收到的是 `event.payload`（保持与旧 callback 签名兼容：
        `Callable[[payload], None]`），不是整个 `AgentEvent` 对象。
        需要事件类型 / 时间戳的订阅者请用 `Agent.set_event_callback` 接全套。

        每个 handler 用 try/except 隔离 —— 单个订阅者抛异常不影响其他订阅者，
        异常记 log.warning 后继续。
        """
        for handler in self._subscribers.get(event.type, ()):
            try:
                handler(event.payload)
            except Exception as exc:
                logger.warning(
                    "[EventBus] 订阅者抛异常已隔离: event=%s handler=%r exc=%s",
                    event.type, handler, exc,
                )


# ── 工具阶段进度（contextvar 透传，避免改一长串工具函数签名） ──────────────────
import contextlib  # noqa: E402
from contextvars import ContextVar  # noqa: E402

# 当前工具调用的 (bus, call_id)；仅在 tool_call_engine 执行某个 tool 期间有值。
# 线程内有效，并发各 run 互不串台（每个 run 在自己的 executor 线程）。
_tool_progress_ctx: "ContextVar[tuple[EventBus, str] | None]" = ContextVar(
    "tool_progress_ctx", default=None
)


@contextlib.contextmanager
def tool_progress_scope(bus: "EventBus | None", call_id: str):
    """在执行单个工具期间绑定 (bus, call_id)，供工具内部用 publish_tool_progress 发阶段事件。"""
    if bus is None:
        yield
        return
    token = _tool_progress_ctx.set((bus, call_id))
    try:
        yield
    finally:
        _tool_progress_ctx.reset(token)


def publish_tool_progress(stage: str, label: str) -> None:
    """工具内部发一条阶段进度事件（如 检索中）；不在工具调用上下文内时静默忽略。"""
    ctx = _tool_progress_ctx.get()
    if ctx is None:
        return
    bus, call_id = ctx
    bus.publish(AgentEvent(
        type=EVENT_TOOL_PROGRESS,
        payload={"call_id": call_id, "stage": stage, "label": label},
    ))
