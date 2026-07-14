"""Web 入库取消注册表。

前端 fetch abort 经 Vite 代理时，后端 `request.is_disconnected()` 往往感知不到断开；
用户点「取消」时前端应额外调 `/api/kb/upload/cancel` 置位对应 ingest_id 的 Event。
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_events: dict[str, threading.Event] = {}


def register(ingest_id: str) -> threading.Event:
    """登记一次入库任务，返回可被 trigger / is_disconnected 共用的取消 Event。"""
    ev = threading.Event()
    with _lock:
        _events[ingest_id] = ev
    return ev


def trigger(ingest_id: str) -> bool:
    """显式取消；ingest_id 不存在时返回 False（可能已结束或未开始）。"""
    with _lock:
        ev = _events.get(ingest_id)
    if ev is None:
        return False
    ev.set()
    return True


def unregister(ingest_id: str) -> None:
    with _lock:
        _events.pop(ingest_id, None)
