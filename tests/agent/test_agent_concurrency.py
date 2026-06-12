"""
测试：单例 Agent 的多请求并发隔离（iter_10_async §1.4）

进程级单例 Agent 现在把 session_id / 事件回调 / usage 都改成 `run()` 的 per-run 入参，
不再写共享实例字段，因此可以被多请求并发调用而互不串台。本文件验证：

- run(session_id=...) 只作用于本次调用，不改 agent.session_id；事件 payload 带本次 sid。
- run(event_callback=...) 用独立局部 bus，不污染 agent.events（实例 bus）。
- 传 per-request 入参时不回写 agent.last_usage（避免并发竞争）。
- 两线程并发跑同一个 agent 实例：各自的事件 / 落库 session_id 不互窜。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from src.agent.agent import Agent
from src.agent.core.event_bus import EVENT_FINAL_ANSWER, EVENT_INFO, AgentEvent


def _text_response(content: str) -> Any:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _mk_agent() -> Agent:
    mock_history = MagicMock()
    mock_history.load_last_n_messages.return_value = []
    return Agent(verbose=False, chat_history=mock_history, user_memory=None)


# ── per-run 入参不污染实例状态 ───────────────────────────────────────────────

class TestPerRunDoesNotMutateInstance:
    def test_session_id_param_does_not_touch_instance(self) -> None:
        agent = _mk_agent()
        original_sid = agent.session_id
        captured: list[AgentEvent] = []

        with patch("src.agent.agent.chat", return_value=_text_response("ok")):
            answer = agent.run(
                "hi", session_id="run-sid", event_callback=captured.append
            )

        assert answer == "ok"
        # 实例字段不被本次运行写脏
        assert agent.session_id == original_sid
        # 事件用的是本次传入的 sid
        info = next(e for e in captured if e.type == EVENT_INFO)
        assert info.payload.get("session_id") == "run-sid"

    def test_event_callback_param_does_not_touch_instance_bus(self) -> None:
        agent = _mk_agent()
        captured: list[AgentEvent] = []

        with patch("src.agent.agent.chat", return_value=_text_response("ok")):
            agent.run("hi", session_id="s1", event_callback=captured.append)

        # 本次用局部 bus；实例 bus 不应被注册任何订阅者
        assert agent.events.subscribers(EVENT_INFO) == []
        assert agent.events.subscribers(EVENT_FINAL_ANSWER) == []
        # 回调确实收到了事件
        assert {e.type for e in captured} >= {EVENT_INFO, EVENT_FINAL_ANSWER}

    def test_per_run_does_not_write_last_usage(self) -> None:
        agent = _mk_agent()
        assert agent.last_usage is None
        with patch("src.agent.agent.chat", return_value=_text_response("ok")):
            agent.run("hi", session_id="s1", event_callback=lambda e: None)
        # 传了 per-request 上下文 → 不回写实例 last_usage（留给并发各自局部持有）
        assert agent.last_usage is None

    def test_no_param_keeps_legacy_instance_behavior(self) -> None:
        """不传 kwargs = 老行为：写实例 session_id 的事件 + 回写 last_usage（CLI 路径）。"""
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)
        with patch("src.agent.agent.chat", return_value=_text_response("ok")):
            agent.run("hi")
        info = next(e for e in captured if e.type == EVENT_INFO)
        assert info.payload.get("session_id") == agent.session_id


# ── 两线程并发跑同一个实例：互不串台 ────────────────────────────────────────

class TestConcurrentRunsIsolated:
    def test_two_threads_do_not_cross(self) -> None:
        agent = _mk_agent()
        original_sid = agent.session_id
        # 强制两次 run 同时在 chat() 内，证明它们真正并发且不互相覆盖共享态
        barrier = threading.Barrier(2, timeout=5)

        def fake_chat(messages: list[dict], tools=None, **kwargs):
            barrier.wait()
            user_msg = messages[-1]["content"]
            return _text_response(f"answer:{user_msg}")

        events: dict[str, list[AgentEvent]] = {"A": [], "B": []}
        results: dict[str, str] = {}
        errors: list[Exception] = []

        def worker(tag: str, sid: str, msg: str) -> None:
            try:
                results[tag] = agent.run(
                    msg, session_id=sid, event_callback=events[tag].append
                )
            except Exception as exc:  # pragma: no cover - 仅在回归时触发
                errors.append(exc)

        with patch("src.agent.agent.chat", side_effect=fake_chat):
            t_a = threading.Thread(target=worker, args=("A", "sid-A", "msg-A"))
            t_b = threading.Thread(target=worker, args=("B", "sid-B", "msg-B"))
            t_a.start()
            t_b.start()
            t_a.join(timeout=10)
            t_b.join(timeout=10)

        assert not errors, f"并发 run 抛异常: {errors}"
        # 各自拿到自己的答案
        assert results["A"] == "answer:msg-A"
        assert results["B"] == "answer:msg-B"
        # 各自的事件只带自己的 session_id，没有串到对方
        sids_a = {
            e.payload.get("session_id")
            for e in events["A"]
            if e.type == EVENT_INFO
        }
        sids_b = {
            e.payload.get("session_id")
            for e in events["B"]
            if e.type == EVENT_INFO
        }
        assert sids_a == {"sid-A"}
        assert sids_b == {"sid-B"}
        # 实例字段没被任一线程写脏
        assert agent.session_id == original_sid
        assert agent.last_usage is None

        # 落库的 user 消息与各自 session_id 配对正确
        appended = {
            (call.args[0], call.args[1]["content"])
            for call in agent._chat_history.append.call_args_list
            if call.args[1].get("role") == "user"
        }
        assert ("sid-A", "msg-A") in appended
        assert ("sid-B", "msg-B") in appended
