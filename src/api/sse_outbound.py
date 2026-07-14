"""SSE 出站队列：有界缓冲、token/thinking 合并、满队列时丢弃可合并事件。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 队列满时可丢弃的类型（进度类）；终态与工具事件必须保留
_MERGEABLE_TYPES = frozenset({
    "token_chunk",
    "thinking_chunk",
    "progress",
    "research_subagent_progress",
})

_CRITICAL_TYPES = frozenset({"final_answer", "error"})


class SseOutbound:
    """线程安全：生产者从 agent 线程 call_soon_threadsafe 入队，消费者在 asyncio 协程 get。"""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        maxsize: int,
        merge_max_chars: int,
        merge_interval_s: float,
    ) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max(1, maxsize))
        self._merge_max_chars = max(0, merge_max_chars)
        self._merge_interval_s = max(0.0, merge_interval_s)
        self._token_buf = ""
        self._thinking_buf = ""
        self._flush_handle: asyncio.Handle | None = None
        self._closed = False

    @property
    def queue(self) -> asyncio.Queue[Any]:
        return self._queue

    def close(self) -> None:
        """停止合并定时器；先尽量 flush 缓冲，再标记关闭。"""
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        try:
            self._flush_pending()
        except Exception:
            logger.debug("[sse] close 时 flush 失败", exc_info=True)
        self._closed = True
        self._token_buf = ""
        self._thinking_buf = ""

    def enqueue_now(self, frame: dict[str, Any]) -> None:
        """同事件循环线程入队（async 驱动里发 error / 终态前 flush）。"""
        if self._closed:
            return
        self._enqueue(frame)

    def enqueue_from_thread(self, frame: dict[str, Any]) -> None:
        if self._closed:
            return
        self._loop.call_soon_threadsafe(self._enqueue, frame)

    def _enqueue(self, frame: dict[str, Any]) -> None:
        et = frame.get("type")
        if et == "token_chunk" and self._merge_interval_s > 0:
            self._token_buf += str((frame.get("payload") or {}).get("text", ""))
            self._schedule_flush()
            return
        if et == "thinking_chunk" and self._merge_interval_s > 0:
            self._thinking_buf += str((frame.get("payload") or {}).get("text", ""))
            self._schedule_flush()
            return
        self._flush_pending()
        critical = et in _CRITICAL_TYPES
        self._put_frame(frame, critical=critical)

    def _schedule_flush(self) -> None:
        if self._should_flush_now():
            self._flush_pending()
            return
        if self._flush_handle is None and self._merge_interval_s > 0:
            self._flush_handle = self._loop.call_later(
                self._merge_interval_s,
                self._flush_timer,
            )

    def _flush_timer(self) -> None:
        self._flush_handle = None
        self._flush_pending()

    def _should_flush_now(self) -> bool:
        if self._merge_max_chars <= 0:
            return True
        return (
            len(self._token_buf) >= self._merge_max_chars
            or len(self._thinking_buf) >= self._merge_max_chars
        )

    def _flush_pending(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        if self._token_buf:
            text, self._token_buf = self._token_buf, ""
            self._put_frame({"type": "token_chunk", "payload": {"text": text}}, critical=False)
        if self._thinking_buf:
            text, self._thinking_buf = self._thinking_buf, ""
            self._put_frame(
                {"type": "thinking_chunk", "payload": {"text": text}},
                critical=False,
            )

    def _put_frame(self, frame: dict[str, Any], *, critical: bool) -> None:
        if critical:
            self._make_room_for_critical()
        if not critical and self._queue.full():
            if frame.get("type") in _MERGEABLE_TYPES:
                return
            self._drop_one_mergeable()
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            if critical:
                self._drop_one_mergeable()
                try:
                    self._queue.put_nowait(frame)
                except asyncio.QueueFull:
                    logger.warning("[sse] 队列仍满，丢弃终态事件 type=%s", frame.get("type"))
            elif frame.get("type") in _MERGEABLE_TYPES:
                return
            else:
                logger.debug("[sse] 队列满，丢弃 type=%s", frame.get("type"))

    def _drop_one_mergeable(self) -> bool:
        drained: list[Any] = []
        dropped = False
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not dropped and isinstance(item, dict) and item.get("type") in _MERGEABLE_TYPES:
                dropped = True
                continue
            drained.append(item)
        for idx, item in enumerate(drained):
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                leftover = drained[idx:]
                logger.warning(
                    "[sse] 回灌时队列满，丢弃 %d 条（含 type=%s）",
                    len(leftover),
                    leftover[0].get("type") if isinstance(leftover[0], dict) else type(leftover[0]),
                )
                break
        return dropped

    def _make_room_for_critical(self) -> None:
        for _ in range(self._queue.qsize() + 1):
            if not self._queue.full():
                return
            if not self._drop_one_mergeable():
                return

    def drain(self) -> int:
        """清空队列（客户端断开后），返回丢弃条数。"""
        n = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                n += 1
            except asyncio.QueueEmpty:
                break
        return n
