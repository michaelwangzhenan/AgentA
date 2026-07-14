"""流式 Agent run 的协作式取消（threading.Event + contextvars）。

Web SSE 在客户端断开时 set 事件；agent.run / ResearchEngine 在轮次边界轮询后尽早退出。
"""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from typing import Iterator

_cancel_event: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "run_cancel_event",
    default=None,
)


def activate(event: threading.Event) -> contextvars.Token:
    """绑定本次 run 的取消事件（须在跑 agent 的线程 / copy_context 内）。"""
    return _cancel_event.set(event)


def deactivate(token: contextvars.Token) -> None:
    _cancel_event.reset(token)


def is_cancelled() -> bool:
    ev = _cancel_event.get()
    return ev is not None and ev.is_set()


@contextmanager
def cancel_scope(event: threading.Event) -> Iterator[threading.Event]:
    """with cancel_scope(ev): ... 自动绑定 / 解绑。"""
    token = activate(event)
    try:
        yield event
    finally:
        deactivate(token)
