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


# 事件类型字符串常量（与 design.md §3.3.2 AgentEvent 表对齐）
EVENT_THINKING_CHUNK = "thinking_chunk"
EVENT_TOKEN_CHUNK = "token_chunk"
EVENT_TOOL_CALL_START = "tool_call_start"
EVENT_TOOL_CALL_END = "tool_call_end"
EVENT_FINAL_ANSWER = "final_answer"
EVENT_ERROR = "error"
EVENT_INFO = "info"

ALL_EVENT_TYPES: tuple[str, ...] = (
    EVENT_THINKING_CHUNK,
    EVENT_TOKEN_CHUNK,
    EVENT_TOOL_CALL_START,
    EVENT_TOOL_CALL_END,
    EVENT_FINAL_ANSWER,
    EVENT_ERROR,
    EVENT_INFO,
)


@dataclass(frozen=True)
class AgentEvent:
    """
    统一事件对象（design.md §3.3.2）。

    Fields:
        type:    事件类型字符串，取自 `ALL_EVENT_TYPES`
        payload: 事件载荷 dict —— 各事件类型 payload schema 见 design.md §3.3.2 表
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
