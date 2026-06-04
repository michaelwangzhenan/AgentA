"""POST /api/chat 端点 UT（mock Agent.run，不走真 LLM）"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_agent
from src.api.main import app


def _mock_agent(reply: str = "test reply", raises: Exception | None = None) -> MagicMock:
    agent = MagicMock()
    agent.session_id = "test-session-id"
    if raises is not None:
        agent.run.side_effect = raises
    else:
        agent.run.return_value = reply
    return agent


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


def test_chat_returns_reply_and_session_id():
    mock = _mock_agent("hello back")
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hi"})

    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "hello back"
    assert body["session_id"] == "test-session-id"
    mock.run.assert_called_once_with("hi")


def test_chat_missing_message_returns_422():
    r = client.post("/api/chat", json={})
    assert r.status_code == 422


def test_chat_empty_message_returns_422():
    r = client.post("/api/chat", json={"message": ""})
    assert r.status_code == 422


def test_chat_agent_exception_returns_500():
    mock = _mock_agent(raises=RuntimeError("LLM provider down"))
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hi"})

    assert r.status_code == 500
    assert "agent error" in r.json()["detail"]
    assert "LLM provider down" in r.json()["detail"]


def test_chat_with_session_id_switches_agent_session():
    """带 session_id 时，路由先把 agent.session_id 改成请求里的值再 run。"""
    mock = _mock_agent("ok")
    mock.session_id = "default-uuid"
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hi", "session_id": "target-uuid"})

    assert r.status_code == 200
    assert mock.session_id == "target-uuid"
    assert r.json()["session_id"] == "target-uuid"


def test_chat_without_session_id_keeps_agent_default():
    """不传 session_id 时不动 agent.session_id（保留 Step 2 兼容行为）。"""
    mock = _mock_agent("ok")
    mock.session_id = "default-uuid"
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hi"})

    assert r.status_code == 200
    assert mock.session_id == "default-uuid"
