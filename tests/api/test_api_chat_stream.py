"""POST /api/chat/stream 端点 UT（mock Agent，不走真 LLM）

Step 2 SSE 流式端点。用 FakeAgent 在 run 内同步连发几个事件再返回，
模拟真 Agent 的事件流时序；通过 TestClient.stream 读 SSE 帧并解析。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.agent.core.event_bus import AgentEvent, EventBus
from src.api.deps import get_agent
from src.api.main import app


# ─── 测试用 FakeAgent ────────────────────────────────────────────────────

class FakeAgent:
    """满足 AgentAPI 协议的最小 Fake。run 内同步连发预设事件再返回 reply。"""

    session_id: str = "test-stream-session"
    last_usage: Any = None
    thinking_cfg: Any = None

    def __init__(
        self,
        events_to_emit: list[AgentEvent] | None = None,
        run_raises: Exception | None = None,
        final_reply: str = "fake-reply",
    ) -> None:
        self.events: EventBus = EventBus()
        self._callback = None
        self._events_to_emit = events_to_emit or []
        self._run_raises = run_raises
        self._final_reply = final_reply
        self.last_session_id: str | None = None

    def run(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
        event_callback=None,
    ) -> str:
        # 新契约：session_id / event_callback 作为 per-run 入参传入，不再走实例字段
        self.last_session_id = session_id
        cb = event_callback if event_callback is not None else self._callback
        if self._run_raises is not None:
            raise self._run_raises
        for ev in self._events_to_emit:
            if cb is not None:
                cb(ev)
        return self._final_reply

    def activate_skill(self, name: str, body: str) -> bool:
        return False

    def set_event_callback(self, callback) -> None:
        self._callback = callback


# ─── SSE 解析 helper ────────────────────────────────────────────────────

def _parse_sse(text: str) -> list[dict[str, Any]]:
    """把 SSE 文本流解析成 [{"event": ..., "data": <parsed json>}, ...]"""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith(":"):  # SSE 注释 / ping —— 忽略
            continue
        if line.startswith("data:"):
            data_str = line[5:].lstrip()
            try:
                current["data"] = json.loads(data_str)
            except json.JSONDecodeError:
                current["data"] = data_str
        elif line.startswith("event:"):
            current["event"] = line[6:].strip()
    if current:
        events.append(current)
    return events


# ─── fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


# ─── tests ──────────────────────────────────────────────────────────────

def test_stream_emits_token_and_final_answer():
    fake = FakeAgent(events_to_emit=[
        AgentEvent(type="token_chunk", payload={"text": "hi "}),
        AgentEvent(type="token_chunk", payload={"text": "world"}),
        AgentEvent(type="final_answer", payload={"text": "hi world", "usage": None}),
    ])
    app.dependency_overrides[get_agent] = lambda: fake

    with client.stream("POST", "/api/chat/stream", json={"message": "ping"}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        body = "".join(chunk for chunk in r.iter_text())

    frames = _parse_sse(body)
    types = [f["data"]["type"] for f in frames if "data" in f and isinstance(f["data"], dict)]
    assert types[-1] == "final_answer"
    token_frames = [
        f["data"] for f in frames
        if f.get("data", {}).get("type") == "token_chunk"
    ]
    merged = "".join(f["payload"]["text"] for f in token_frames)
    assert merged == "hi world"
    assert frames[-1]["data"]["payload"]["text"] == "hi world"


def test_stream_emits_thinking_plan_and_tool_events():
    fake = FakeAgent(events_to_emit=[
        AgentEvent(type="thinking_chunk", payload={"text": "let me think..."}),
        AgentEvent(type="plan_created", payload={"steps": [{"id": 1, "text": "step a"}]}),
        AgentEvent(type="plan_step_start", payload={"step_id": 1, "text": "step a"}),
        AgentEvent(type="tool_call_start", payload={"name": "fake_tool", "args": {"q": "x"}, "call_id": "c1"}),
        AgentEvent(type="tool_call_end", payload={"call_id": "c1", "status": "ok", "preview": "result"}),
        AgentEvent(type="plan_step_end", payload={"step_id": 1, "status": "ok", "note": ""}),
        AgentEvent(type="final_answer", payload={"text": "done", "usage": None}),
    ])
    app.dependency_overrides[get_agent] = lambda: fake

    with client.stream("POST", "/api/chat/stream", json={"message": "go"}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    frames = _parse_sse(body)
    types = [f["data"]["type"] for f in frames]
    assert types == [
        "thinking_chunk", "plan_created", "plan_step_start",
        "tool_call_start", "tool_call_end", "plan_step_end", "final_answer",
    ]


def test_stream_agent_exception_emits_error_frame():
    fake = FakeAgent(run_raises=RuntimeError("provider down"))
    app.dependency_overrides[get_agent] = lambda: fake

    with client.stream("POST", "/api/chat/stream", json={"message": "x"}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    frames = _parse_sse(body)
    assert len(frames) >= 1
    error_frame = frames[-1]["data"]
    assert error_frame["type"] == "error"
    assert "provider down" in error_frame["payload"]["message"]
    assert error_frame["payload"]["recoverable"] is False


def test_stream_missing_message_returns_422():
    fake = FakeAgent()
    app.dependency_overrides[get_agent] = lambda: fake

    r = client.post("/api/chat/stream", json={})
    assert r.status_code == 422


def test_stream_empty_message_returns_422():
    fake = FakeAgent()
    app.dependency_overrides[get_agent] = lambda: fake

    r = client.post("/api/chat/stream", json={"message": ""})
    assert r.status_code == 422


def test_stream_with_session_id_passes_it_to_run():
    """带 session_id 时，路由把它作为 per-run 入参传给 run，不写脏单例实例字段。"""
    fake = FakeAgent(events_to_emit=[
        AgentEvent(type="final_answer", payload={"text": "ok", "usage": None}),
    ])
    fake.session_id = "default-uuid"
    app.dependency_overrides[get_agent] = lambda: fake

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hi", "session_id": "target-uuid"},
    ) as r:
        # consume body 以触发 generator 执行
        for _ in r.iter_text():
            pass

    # 不再 mutate 实例字段；session_id 作为 per-run 入参传入
    assert fake.session_id == "default-uuid"
    assert fake.last_session_id == "target-uuid"


def test_stream_deep_research_mode_dispatches_to_research_engine(monkeypatch):
    """mode=deep_research → 走 ResearchEngine，不调 agent.run；研究事件透传到流。"""
    import src.agent.core.research_engine as re_mod

    fake = FakeAgent(events_to_emit=[
        AgentEvent(type="final_answer", payload={"text": "should-not-be-used", "usage": None}),
    ])
    app.dependency_overrides[get_agent] = lambda: fake

    class FakeResearchEngine:
        def __init__(self, history, user_id=None):
            self.history = history

        def run(self, message, *, session_id, event_callback=None):
            event_callback(AgentEvent(type="research_started", payload={"query": message}))
            event_callback(AgentEvent(type="research_plan", payload={
                "subquestions": [{"id": 0, "text": "子问题"}],
            }))
            event_callback(AgentEvent(type="final_answer", payload={
                "text": "研究报告", "usage": None, "used_tools": True, "personalized": False,
            }))
            return "研究报告"

    monkeypatch.setattr(re_mod, "ResearchEngine", FakeResearchEngine)

    with client.stream(
        "POST", "/api/chat/stream",
        json={"message": "深度问题", "mode": "deep_research"},
    ) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())

    frames = _parse_sse(body)
    types = [f["data"]["type"] for f in frames]
    assert "research_started" in types
    assert "research_plan" in types
    assert types[-1] == "final_answer"
    # 深度研究分支不应调用普通 agent.run
    assert fake.last_session_id is None


def test_stream_default_mode_uses_agent_not_research_engine():
    """缺省 / mode=chat → 走普通 agent.run（回归保护）。"""
    fake = FakeAgent(events_to_emit=[
        AgentEvent(type="final_answer", payload={"text": "ok", "usage": None}),
    ])
    app.dependency_overrides[get_agent] = lambda: fake

    with client.stream(
        "POST", "/api/chat/stream", json={"message": "hi", "mode": "chat"},
    ) as r:
        assert r.status_code == 200
        for _ in r.iter_text():
            pass

    assert fake.last_session_id is not None


def test_stream_sanitizes_namedtuple_payload():
    """final_answer.payload 的 usage 可能是 TokenUsage NamedTuple；
    确认 sanitize 后变成 dict 而不是 JSON 里的 list。"""
    from src.agent.agent import TokenUsage
    usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    fake = FakeAgent(events_to_emit=[
        AgentEvent(type="final_answer", payload={"text": "ok", "usage": usage}),
    ])
    app.dependency_overrides[get_agent] = lambda: fake

    with client.stream("POST", "/api/chat/stream", json={"message": "x"}) as r:
        body = "".join(chunk for chunk in r.iter_text())

    frames = _parse_sse(body)
    payload = frames[-1]["data"]["payload"]
    assert payload["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    }


def test_stream_final_answer_not_dropped_when_queue_small(monkeypatch):
    """队列很小时 progress 可丢，final_answer 仍到达客户端。"""
    import src.config as cfg

    monkeypatch.setattr(cfg, "SSE_QUEUE_MAXSIZE", 2)
    monkeypatch.setattr(cfg, "SSE_TOKEN_MERGE_INTERVAL_MS", 0)
    monkeypatch.setattr(cfg, "SSE_TOKEN_MERGE_MAX_CHARS", 0)

    fake = FakeAgent(events_to_emit=[
        AgentEvent(type="progress", payload={"n": 1}),
        AgentEvent(type="progress", payload={"n": 2}),
        AgentEvent(type="progress", payload={"n": 3}),
        AgentEvent(type="final_answer", payload={"text": "ok", "usage": None}),
    ])
    app.dependency_overrides[get_agent] = lambda: fake

    with client.stream("POST", "/api/chat/stream", json={"message": "x"}) as r:
        body = "".join(chunk for chunk in r.iter_text())

    frames = _parse_sse(body)
    types = [f["data"]["type"] for f in frames]
    assert types[-1] == "final_answer"
    assert frames[-1]["data"]["payload"]["text"] == "ok"
