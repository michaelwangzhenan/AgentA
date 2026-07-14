"""SseOutbound 单元测试。"""

from __future__ import annotations

import asyncio

from src.api.sse_outbound import SseOutbound


def test_token_chunks_merge_before_flush() -> None:
    loop = asyncio.new_event_loop()
    try:
        out = SseOutbound(loop, maxsize=16, merge_max_chars=512, merge_interval_s=0.05)
        out._enqueue({"type": "token_chunk", "payload": {"text": "hi "}})
        out._enqueue({"type": "token_chunk", "payload": {"text": "world"}})
        out._enqueue({"type": "final_answer", "payload": {"text": "done"}})
        items: list[dict] = []
        while not out.queue.empty():
            item = out.queue.get_nowait()
            if isinstance(item, dict):
                items.append(item)
        assert items[0]["type"] == "token_chunk"
        assert items[0]["payload"]["text"] == "hi world"
        assert items[-1]["type"] == "final_answer"
    finally:
        loop.close()


def test_mergeable_dropped_when_queue_full() -> None:
    loop = asyncio.new_event_loop()
    try:
        out = SseOutbound(loop, maxsize=2, merge_max_chars=0, merge_interval_s=0)
        out._enqueue({"type": "progress", "payload": {"n": 1}})
        out._enqueue({"type": "progress", "payload": {"n": 2}})
        out._enqueue({"type": "progress", "payload": {"n": 3}})
        assert out.queue.qsize() <= 2
    finally:
        loop.close()
