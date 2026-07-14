"""TraceStore + TraceCollector + record_trace_safe 旁路软失败 UT（iter_14）。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple
from unittest.mock import MagicMock

import pytest

import src.config as config
from src.agent.core.event_bus import AgentEvent
from src.stores.trace_store import (
    TraceCollector,
    TraceStore,
    record_trace_safe,
    reset_shared_store_for_testing,
)


class _Usage(NamedTuple):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@pytest.fixture
def store(tmp_path: Path) -> Iterator[TraceStore]:
    s = TraceStore(str(tmp_path / "usage.db"))
    yield s
    s.close()


def _trace(trace_id: str, **kw) -> dict:
    base = dict(trace_id=trace_id, user_id=1, session_id="s1", model_id="kimi-k2.5",
                total_ms=100.0, llm_ms=60.0, tool_ms=20.0, retrieval_ms=20.0,
                llm_calls=1, tool_calls=2, total_tokens=150, status="ok")
    base.update(kw)
    return base


# ── 写入 + 读取 ───────────────────────────────────────────────────────────────


def test_record_and_get_with_spans(store: TraceStore) -> None:
    spans = [
        {"stage": "llm", "name": "LLM 第 1 轮", "start_ms": 0, "duration_ms": 60},
        {"stage": "retrieval", "name": "search_knowledge", "start_ms": 60, "duration_ms": 20},
    ]
    store.record_trace(_trace("t1"), spans)
    got = store.get_trace("t1")
    assert got is not None
    assert got["total_ms"] == 100.0
    assert len(got["spans"]) == 2
    assert got["spans"][0]["stage"] == "llm"


def test_overview_percentiles_and_error_rate(store: TraceStore) -> None:
    for i, ms_v in enumerate([100, 200, 300, 400], start=1):
        store.record_trace(_trace(f"t{i}", total_ms=float(ms_v)), [])
    store.record_trace(_trace("terr", total_ms=500.0, status="error", error_phase="llm_call"), [])
    now = int(time.time())
    ov = store.overview(now - 3600, now + 3600)
    assert ov["count"] == 5
    assert ov["error_count"] == 1
    assert ov["error_rate"] == 0.2
    # p50 介于中间值附近
    assert 250 <= ov["latency_p50_ms"] <= 350


def test_overview_large_dataset_uses_sql_not_fetchall(
    store: TraceStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """万级 trace 概览仍正确，且走 SQL 聚合 + 采样分位数。"""
    from src.stores import trace_store as mod

    monkeypatch.setattr(mod, "_TRACE_PERCENTILE_SAMPLE_CAP", 100)
    monkeypatch.setattr(mod, "_TRACE_PERCENTILE_SAMPLE_SIZE", 50)
    now = int(time.time())
    for i in range(150):
        store.record_trace(
            _trace(f"bulk-{i}", total_ms=float(100 + i), llm_ms=10.0, tool_ms=5.0, retrieval_ms=2.0),
            [],
        )
    ov = store.overview(now - 3600, now + 3600)
    assert ov["count"] == 150
    assert ov["latency_avg_ms"] == pytest.approx(174.5, abs=0.5)
    assert ov["avg_llm_ms"] == 10.0
    assert 120 <= ov["latency_p50_ms"] <= 230
    assert ov["latency_p95_ms"] >= ov["latency_p50_ms"]


def test_series_grouped_by_day(store: TraceStore) -> None:
    store.record_trace(_trace("t1", total_ms=100.0), [])
    store.record_trace(_trace("t2", total_ms=300.0), [])
    now = int(time.time())
    rows = store.series(now - 3600, now + 3600)
    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert rows[0]["avg_ms"] == 200.0


def test_user_isolation_and_cascade_delete(store: TraceStore) -> None:
    store.record_trace(_trace("u1", user_id=1), [{"stage": "llm", "name": "x", "duration_ms": 1}])
    store.record_trace(_trace("u2", user_id=2), [])
    now = int(time.time())
    rows, total = store.list_traces(now - 3600, now + 3600, user_id=1)
    assert total == 1 and rows[0]["trace_id"] == "u1"
    removed = store.delete_all_for_user(1)
    assert removed == 1
    assert store.get_trace("u1") is None
    assert store.get_trace("u2") is not None  # 别人不受影响


# ── TraceCollector：从事件流重建 ───────────────────────────────────────────────


def _ev(etype: str, payload: dict, ts: float) -> AgentEvent:
    return AgentEvent(type=etype, payload=payload, ts=ts)


def test_collector_builds_spans_from_events() -> None:
    c = TraceCollector()
    c.on_event(_ev("info", {"message": "agent.run.start", "session_id": "s"}, 1000.0))
    c.on_event(_ev("tool_call_start", {"name": "search_knowledge", "call_id": "c1"}, 1000.6))
    c.on_event(_ev("tool_call_end", {"call_id": "c1", "status": "ok"}, 1001.1))
    # LLM 轮次随 final_answer 的 trace 字段透传（end_ts 1000.5，耗时 500ms）
    c.on_event(_ev("final_answer", {
        "usage": _Usage(100, 50, 150),
        "trace": {"llm_rounds": [{"round": 1, "duration_ms": 500.0, "end_ts": 1000.5}]},
    }, 1002.0))

    trace, spans = c.build("tid", user_id=7, session_id="s", model_id="m", thinking=True)
    assert trace["user_id"] == 7
    assert round(trace["total_ms"]) == 2000  # 1002 - 1000 s
    assert trace["llm_calls"] == 1
    assert trace["tool_calls"] == 1  # search_knowledge 归 retrieval 但也计入 tool_calls
    assert trace["total_tokens"] == 150
    assert trace["status"] == "ok"
    stages = {s["stage"] for s in spans}
    assert stages == {"llm", "retrieval"}
    llm_span = next(s for s in spans if s["stage"] == "llm")
    assert round(llm_span["start_ms"]) == 0
    assert round(llm_span["duration_ms"]) == 500
    retr = next(s for s in spans if s["stage"] == "retrieval")
    assert round(retr["start_ms"]) == 600
    assert round(retr["duration_ms"]) == 500


def test_collector_marks_error() -> None:
    c = TraceCollector()
    c.on_event(_ev("info", {"message": "agent.run.start"}, 0.0))
    c.on_event(_ev("error", {"phase": "llm_call", "message": "boom"}, 0.1))
    c.on_event(_ev("final_answer", {"usage": None}, 0.2))
    trace, _ = c.build("t", 1, None, "m", False)
    assert trace["status"] == "error"
    assert trace["error_phase"] == "llm_call"


# ── record_trace_safe：旁路软失败 + 开关 ──────────────────────────────────────


def test_record_trace_safe_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACE_ENABLED", True)
    bad = MagicMock()
    bad.record_trace.side_effect = RuntimeError("db down")
    reset_shared_store_for_testing(bad)
    try:
        c = TraceCollector()
        c.on_event(_ev("info", {"message": "agent.run.start"}, 0.0))
        c.on_event(_ev("final_answer", {
            "usage": None,
            "trace": {"llm_rounds": [{"round": 1, "duration_ms": 10.0, "end_ts": 0.1}]},
        }, 0.2))
        # 不应抛
        record_trace_safe(c, "t", 1, "s", "m", False)
        assert bad.record_trace.called
    finally:
        reset_shared_store_for_testing(None)


def test_record_trace_safe_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACE_ENABLED", False)
    bad = MagicMock()
    reset_shared_store_for_testing(bad)
    try:
        c = TraceCollector()
        c.on_event(_ev("info", {"message": "agent.run.start"}, 0.0))
        c.on_event(_ev("final_answer", {"usage": None}, 1.0))
        record_trace_safe(c, "t", 1, "s", "m", False)
        bad.record_trace.assert_not_called()  # 开关关闭不落库
    finally:
        reset_shared_store_for_testing(None)


def test_record_trace_safe_skips_empty(store: TraceStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACE_ENABLED", True)
    reset_shared_store_for_testing(store)
    try:
        c = TraceCollector()  # 没有任何事件 → 空 trace，不落库
        record_trace_safe(c, "empty", 1, "s", "m", False)
        assert store.get_trace("empty") is None
    finally:
        reset_shared_store_for_testing(None)
