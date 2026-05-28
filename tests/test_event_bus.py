"""
测试：`Agent.events` (EventBus) 行为契约

§4.5 落地的 `src.agent.core.event_bus.EventBus` 通过 `Agent.events` 暴露给上层。
§4.5.4 进一步收口：删除 set_thinking_callback / set_token_callback 双方法，
统一为 `set_event_callback(cb: Callable[[AgentEvent], None] | None)`。

本文件覆盖：
- 初始状态：默认无订阅者；ctor 的 `on_thinking_chunk` 参数自动注册为订阅者
- 统一 API：`set_event_callback(fn)` 一次性接所有事件类型（覆盖语义 last-wins）
- `_on_thinking_chunk` 把 chunk 派发到 EventBus；无订阅者时降级 stdout
- 多订阅者扇出（直接 `events.subscribe` 多次）
- 订阅者抛异常被 EventBus 隔离，单订阅 / 多订阅均不影响其他流程
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.agent import Agent
from src.agent.core.event_bus import (
    ALL_EVENT_TYPES,
    EVENT_PLAN_CREATED,
    EVENT_PLAN_STEP_END,
    EVENT_PLAN_STEP_START,
    EVENT_THINKING_CHUNK,
    EVENT_TOKEN_CHUNK,
    AgentEvent,
    EventBus,
)


def _mk_agent(on_thinking_chunk=None) -> Agent:
    """构造最小 Agent，绕开 SQLite / user_memory。"""
    mock_history = MagicMock()
    mock_history.load_last_n_messages.return_value = []
    return Agent(
        verbose=False,
        chat_history=mock_history,
        user_memory=None,
        on_thinking_chunk=on_thinking_chunk,
    )


# ── EventBus 初始状态 ───────────────────────────────────────────────────────

class TestDefaultEventBusState:

    def test_no_subscribers_by_default(self) -> None:
        agent = _mk_agent()
        for evt in ALL_EVENT_TYPES:
            assert agent.events.subscribers(evt) == []

    def test_ctor_on_thinking_chunk_registered_as_subscriber(self) -> None:
        """ctor 的 on_thinking_chunk 参数应自动注册到 EVENT_THINKING_CHUNK。"""
        fn = MagicMock()
        agent = _mk_agent(on_thinking_chunk=fn)
        assert agent.events.subscribers(EVENT_THINKING_CHUNK) == [fn]


# ── set_event_callback：统一入口（覆盖语义） ────────────────────────────────

class TestSetEventCallback:
    """`set_event_callback` 是 AgentAPI 定义的唯一事件入口。"""

    def test_installs_subscriber_for_every_event_type(self) -> None:
        """一次调用应为 ALL_EVENT_TYPES 中每类事件各注册一个 wrapper handler。"""
        agent = _mk_agent()
        agent.set_event_callback(lambda evt: None)
        for evt in ALL_EVENT_TYPES:
            assert len(agent.events.subscribers(evt)) == 1

    def test_none_clears_all_subscribers(self) -> None:
        """传 None 应清空所有事件类型的订阅者（含 ctor 时的 on_thinking_chunk）。"""
        agent = _mk_agent(on_thinking_chunk=lambda c: None)
        agent.set_event_callback(lambda evt: None)
        agent.set_event_callback(None)
        for evt in ALL_EVENT_TYPES:
            assert agent.events.subscribers(evt) == []

    def test_replacement_last_wins(self) -> None:
        """连续调用：覆盖语义，最后一个 callback 生效。"""
        agent = _mk_agent()
        agent.set_event_callback(lambda evt: None)
        agent.set_event_callback(lambda evt: None)
        # 每类事件应仍只有一个订阅者（旧 wrapper 已被 clear 掉）
        for evt in ALL_EVENT_TYPES:
            assert len(agent.events.subscribers(evt)) == 1

    def test_callback_receives_typed_agent_event(self) -> None:
        """回调收到的是带 type / payload / ts 的完整 AgentEvent 实例。"""
        captured: list[AgentEvent] = []
        agent = _mk_agent()
        agent.set_event_callback(captured.append)
        agent._on_thinking_chunk("hello")
        agent._on_token_chunk("world")
        types = [e.type for e in captured]
        assert types == [EVENT_THINKING_CHUNK, EVENT_TOKEN_CHUNK]
        assert captured[0].payload == {"text": "hello"}
        assert captured[1].payload == {"text": "world"}
        assert all(isinstance(e.ts, float) for e in captured)


# ── _on_thinking_chunk 派发与降级 ───────────────────────────────────────────

class TestOnThinkingChunkDispatch:

    def test_chunk_published_to_subscribers(self) -> None:
        """直接 subscribe 收到的是 payload dict（保持 EventBus 兼容签名）。"""
        captured: list[dict] = []
        agent = _mk_agent()
        agent.events.subscribe(EVENT_THINKING_CHUNK, captured.append)
        agent._on_thinking_chunk("hello ")
        agent._on_thinking_chunk("world")
        assert captured == [{"text": "hello "}, {"text": "world"}]
        assert agent._thinking_started is True

    def test_no_subscriber_falls_back_to_stdout(self, capsys) -> None:
        """无订阅者时走 CLI stdout 分支：首 chunk 打印头部 + chunk 内容本身。"""
        agent = _mk_agent()
        agent._on_thinking_chunk("第一段")
        agent._on_thinking_chunk("第二段")
        out = capsys.readouterr().out
        assert out.count("思考中") == 1
        assert "第一段" in out
        assert "第二段" in out
        assert agent._thinking_started is True


# ── EventBus 多订阅扇出 ────────────────────────────────────────────────────

class TestEventBusFanOut:
    """直接通过 `agent.events.subscribe(...)` 注册多个订阅者。"""

    def test_multiple_subscribers_fan_out(self) -> None:
        agent = _mk_agent()
        sub1: list[dict] = []
        sub2: list[dict] = []
        agent.events.subscribe(EVENT_THINKING_CHUNK, sub1.append)
        agent.events.subscribe(EVENT_THINKING_CHUNK, sub2.append)
        agent._on_thinking_chunk("x")
        assert sub1 == [{"text": "x"}]
        assert sub2 == [{"text": "x"}]

    def test_unsubscribe_removes_handler(self) -> None:
        agent = _mk_agent()
        sub1: list[dict] = []
        sub2: list[dict] = []
        agent.events.subscribe(EVENT_THINKING_CHUNK, sub1.append)
        agent.events.subscribe(EVENT_THINKING_CHUNK, sub2.append)
        assert agent.events.unsubscribe(EVENT_THINKING_CHUNK, sub1.append) is True
        agent._on_thinking_chunk("x")
        assert sub1 == []
        assert sub2 == [{"text": "x"}]

    def test_unsubscribe_unknown_handler_returns_false(self) -> None:
        agent = _mk_agent()
        assert agent.events.unsubscribe(EVENT_THINKING_CHUNK, lambda c: None) is False


# ── EventBus 异常隔离 ──────────────────────────────────────────────────────

class TestEventBusExceptionIsolation:

    def test_single_subscriber_exception_swallowed(self) -> None:
        """单订阅者抛异常时，事件分发吞掉异常，不影响 Agent 主流程。"""
        agent = _mk_agent()

        def bad(_payload) -> None:
            raise RuntimeError("订阅者炸了")

        agent.events.subscribe(EVENT_THINKING_CHUNK, bad)
        # 不应抛 RuntimeError
        agent._on_thinking_chunk("any")

    def test_one_subscriber_exception_does_not_block_others(self) -> None:
        """多订阅时，某个订阅者抛异常不影响其他订阅者收到事件。"""
        agent = _mk_agent()
        good: list[dict] = []

        def bad(_payload) -> None:
            raise RuntimeError("订阅者炸了")

        agent.events.subscribe(EVENT_THINKING_CHUNK, bad)
        agent.events.subscribe(EVENT_THINKING_CHUNK, good.append)
        agent._on_thinking_chunk("x")
        assert good == [{"text": "x"}]


# ── Phase 2.1 — Plan 事件类型（plan_created / plan_step_start / plan_step_end） ───

class TestPlanEventTypes:
    """三类 plan 事件必须满足 ALL_EVENT_TYPES 注册 + publish/subscribe 流向无差错。"""

    def test_plan_events_are_in_all_event_types(self) -> None:
        for evt in (EVENT_PLAN_CREATED, EVENT_PLAN_STEP_START, EVENT_PLAN_STEP_END):
            assert evt in ALL_EVENT_TYPES

    def test_plan_event_publish_and_subscribe_roundtrip(self) -> None:
        """直接走 EventBus 发 3 类 plan 事件，订阅者按类型收到对应 payload。"""
        bus = EventBus()
        captured: dict[str, list[dict]] = {
            EVENT_PLAN_CREATED: [],
            EVENT_PLAN_STEP_START: [],
            EVENT_PLAN_STEP_END: [],
        }
        for evt_type, sink in captured.items():
            bus.subscribe(evt_type, sink.append)

        bus.publish(AgentEvent(
            type=EVENT_PLAN_CREATED,
            payload={"steps": [{"id": 1, "text": "列项目"}, {"id": 2, "text": "对比"}]},
        ))
        bus.publish(AgentEvent(type=EVENT_PLAN_STEP_START, payload={"step_id": 1}))
        bus.publish(AgentEvent(
            type=EVENT_PLAN_STEP_END,
            payload={"step_id": 1, "status": "success", "note": "找到 3 个项目"},
        ))

        assert len(captured[EVENT_PLAN_CREATED]) == 1
        assert captured[EVENT_PLAN_CREATED][0]["steps"][0]["text"] == "列项目"
        assert captured[EVENT_PLAN_STEP_START] == [{"step_id": 1}]
        assert captured[EVENT_PLAN_STEP_END][0]["status"] == "success"

    def test_plan_event_subscriber_isolation(self) -> None:
        """plan 事件订阅者抛异常被 EventBus 吞掉，不影响其他订阅者。"""
        bus = EventBus()
        good: list[dict] = []

        def bad(_payload) -> None:
            raise RuntimeError("plan 订阅者炸了")

        bus.subscribe(EVENT_PLAN_CREATED, bad)
        bus.subscribe(EVENT_PLAN_CREATED, good.append)
        bus.publish(AgentEvent(type=EVENT_PLAN_CREATED, payload={"steps": []}))
        assert good == [{"steps": []}]
