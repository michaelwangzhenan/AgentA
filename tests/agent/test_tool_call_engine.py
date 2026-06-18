"""
测试：ToolCallEngine 同轮多工具并行执行（iter_10_perf §3 编号 2）

锁住两条行为：
- 同一轮 ≥2 个非 plan 工具会**真正并行**执行（用 threading.Barrier 证明，串行则超时）。
- 并行后结果、tool 消息、写历史顺序仍**按 tool_call 原顺序**对齐。

plan tool 轮仍串行（由现有 test_agent_events / test_agent 覆盖），此处不重复。
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from src.agent.core.event_bus import EVENT_TOOL_CALL_END, EVENT_TOOL_CALL_START, EventBus
from src.agent.core.tool_call_engine import ToolCallEngine
from src.agent.tools import ToolResult


def _tc(call_id: str, name: str, args: dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def _message(*tool_calls: Any) -> Any:
    return SimpleNamespace(content="", tool_calls=list(tool_calls))


def _mk_engine(events: EventBus | None = None) -> ToolCallEngine:
    return ToolCallEngine(
        chat_history=MagicMock(),
        session_id="s1",
        skill_bodies={},
        verbose=False,
        events=events,
    )


class TestParallelToolExecution:
    def test_two_non_plan_tools_run_concurrently(self) -> None:
        """两个非 plan 工具应并行：用 Barrier(2) 证明两线程同时在跑，串行会 BrokenBarrier。"""
        engine = _mk_engine()
        barrier = threading.Barrier(2, timeout=5)

        def fake_exec(name: str, args: dict[str, Any], skill_bodies: dict, **kw: Any) -> ToolResult:
            barrier.wait()  # 串行时第一个会在此卡到超时 → BrokenBarrierError
            return ToolResult(status="ok", content=f"r:{args['q']}")

        messages: list[dict[str, Any]] = []
        msg = _message(
            _tc("c1", "search_knowledge", {"q": "a"}),
            _tc("c2", "search_knowledge", {"q": "b"}),
        )
        with patch("src.agent.core.tool_call_engine.execute_tool", side_effect=fake_exec):
            engine.process(msg, messages)

        # 两个 tool 结果都落地，且顺序按 tool_call（c1 在前 c2 在后）
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
        assert tool_msgs[0]["content"] == "r:a"
        assert tool_msgs[1]["content"] == "r:b"

    def test_parallel_preserves_order_regardless_of_finish_time(self) -> None:
        """先完成的工具不能抢占顺序：结果严格按 tool_call 原序对齐。"""
        engine = _mk_engine()

        def fake_exec(name: str, args: dict[str, Any], skill_bodies: dict, **kw: Any) -> ToolResult:
            # 让 c1 慢、c2 快，验证落地顺序仍是 c1→c2
            import time
            if args["q"] == "slow":
                time.sleep(0.05)
            return ToolResult(status="ok", content=f"r:{args['q']}")

        messages: list[dict[str, Any]] = []
        msg = _message(
            _tc("c1", "web_search", {"q": "slow"}),
            _tc("c2", "web_search", {"q": "fast"}),
        )
        with patch("src.agent.core.tool_call_engine.execute_tool", side_effect=fake_exec):
            engine.process(msg, messages)

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
        assert [m["content"] for m in tool_msgs] == ["r:slow", "r:fast"]

    def test_parallel_emits_start_before_end_for_all(self) -> None:
        """并行路径仍发齐 tool_call_start / tool_call_end，且所有 start 在所有 end 之前。"""
        captured: list[Any] = []
        bus = EventBus()
        for et in (EVENT_TOOL_CALL_START, EVENT_TOOL_CALL_END):
            bus.subscribe(et, lambda payload, _et=et: captured.append(_et))
        engine = _mk_engine(events=bus)

        def fake_exec(name: str, args: dict[str, Any], skill_bodies: dict, **kw: Any) -> ToolResult:
            return ToolResult(status="ok", content="ok")

        messages: list[dict[str, Any]] = []
        msg = _message(
            _tc("c1", "search_knowledge", {"q": "a"}),
            _tc("c2", "fetch_url", {"q": "b"}),
        )
        with patch("src.agent.core.tool_call_engine.execute_tool", side_effect=fake_exec):
            engine.process(msg, messages)

        assert captured.count(EVENT_TOOL_CALL_START) == 2
        assert captured.count(EVENT_TOOL_CALL_END) == 2
        last_start = max(i for i, e in enumerate(captured) if e == EVENT_TOOL_CALL_START)
        first_end = min(i for i, e in enumerate(captured) if e == EVENT_TOOL_CALL_END)
        assert last_start < first_end

    def test_parallel_workers_inherit_logging_context(self) -> None:
        """并行 worker 线程应继承父 context：工具内读到的 session_id = 父线程所设值。

        锁住 iter_8_13 验证报告 §2.1 的修复（copy_context）：不修时子线程取默认 '-'，
        并行工具的日志会丢成 `s:-`，无法按 session 串链路。
        """
        from src.services.log_setup import get_session_id, set_session_id

        engine = _mk_engine()
        seen: dict[str, str] = {}

        def fake_exec(name: str, args: dict[str, Any], skill_bodies: dict, **kw: Any) -> ToolResult:
            seen[args["q"]] = get_session_id()
            return ToolResult(status="ok", content="ok")

        set_session_id("sess-xyz")
        try:
            messages: list[dict[str, Any]] = []
            msg = _message(
                _tc("c1", "web_search", {"q": "a"}),
                _tc("c2", "web_search", {"q": "b"}),
            )
            with patch("src.agent.core.tool_call_engine.execute_tool", side_effect=fake_exec):
                engine.process(msg, messages)
        finally:
            set_session_id(None)

        assert seen == {"a": "sess-xyz", "b": "sess-xyz"}

    def test_single_tool_still_works(self) -> None:
        """单工具走串行路径，行为不变。"""
        engine = _mk_engine()

        def fake_exec(name: str, args: dict[str, Any], skill_bodies: dict, **kw: Any) -> ToolResult:
            return ToolResult(status="ok", content="single")

        messages: list[dict[str, Any]] = []
        msg = _message(_tc("c1", "search_knowledge", {"q": "a"}))
        with patch("src.agent.core.tool_call_engine.execute_tool", side_effect=fake_exec):
            engine.process(msg, messages)

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "single"


class TestCitationBuilderThreadSafe:
    def test_concurrent_register_assigns_unique_numbers(self) -> None:
        """并发 register 不丢编号：N 线程各注册一个独立 source，编号应是 1..N 无重复。"""
        from src.agent.core.citation_builder import CitationBuilder
        from src.rag.retriever import Hit

        cb = CitationBuilder()
        n = 20
        start = threading.Barrier(n)

        def reg(i: int) -> None:
            start.wait()
            cb.register([Hit(
                source=f"doc{i}.md", document="t", distance=0.0,
                collection="kb", id=f"id{i}", metadata={},
            )])

        threads = [threading.Thread(target=reg, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        nums = sorted(c.num for c in cb.citations)
        assert nums == list(range(1, n + 1))
        assert len(cb) == n
