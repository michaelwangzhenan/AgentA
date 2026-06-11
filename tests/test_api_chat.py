"""POST /api/chat 端点 UT（mock Agent.run，不走真 LLM）"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_agent
from src.api.main import app
from src.api.routes import chat as chat_mod


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
    # 未传 session_id → 路由生成新 uuid 并回传（不再回读 agent.session_id）
    assert isinstance(body["session_id"], str) and body["session_id"]
    # run 收到 message + 本次 session_id（per-run 入参）
    args, kwargs = mock.run.call_args
    assert args == ("hi",)
    assert kwargs["session_id"] == body["session_id"]


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


def test_chat_with_session_id_passes_it_to_run():
    """带 session_id 时，路由把它作为 per-run 入参传给 run，不写脏单例实例字段。"""
    mock = _mock_agent("ok")
    mock.session_id = "default-uuid"
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hi", "session_id": "target-uuid"})

    assert r.status_code == 200
    # 不再 mutate 实例字段
    assert mock.session_id == "default-uuid"
    # session_id 作为 per-run 入参传入，响应回传同值
    assert mock.run.call_args.kwargs["session_id"] == "target-uuid"
    assert r.json()["session_id"] == "target-uuid"


def test_chat_without_session_id_keeps_agent_default():
    """不传 session_id 时不动 agent.session_id（per-run 入参，不碰实例字段）。"""
    mock = _mock_agent("ok")
    mock.session_id = "default-uuid"
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hi"})

    assert r.status_code == 200
    assert mock.session_id == "default-uuid"


# ── 语义缓存：skip_cache 绕过 + 命中率采集 ─────────────────────────────────────


def test_skip_cache_bypasses_lookup_and_telemetry(monkeypatch):
    """skip_cache=true（重新生成）：不查缓存、不记命中率分母，直接跑 agent。"""
    mock = _mock_agent("fresh answer")
    app.dependency_overrides[get_agent] = lambda: mock
    lookup = MagicMock(return_value=None)
    rec = MagicMock()
    monkeypatch.setattr(chat_mod.semantic_cache, "lookup_cached", lookup)
    monkeypatch.setattr(chat_mod, "record_cache_lookup", rec)

    r = client.post("/api/chat", json={"message": "hi", "skip_cache": True})

    assert r.status_code == 200
    assert r.json()["reply"] == "fresh answer"
    lookup.assert_not_called()
    rec.assert_not_called()


def test_fresh_query_records_cache_lookup(monkeypatch):
    """默认（单轮起步）会查缓存并记一次命中率分母。"""
    mock = _mock_agent("answer")
    app.dependency_overrides[get_agent] = lambda: mock
    lookup = MagicMock(return_value=None)
    rec = MagicMock()
    monkeypatch.setattr(chat_mod._cfg, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(chat_mod.semantic_cache, "lookup_cached", lookup)
    monkeypatch.setattr(chat_mod, "record_cache_lookup", rec)

    r = client.post("/api/chat", json={"message": "hi"})

    assert r.status_code == 200
    lookup.assert_called_once()
    rec.assert_called_once()


def test_cache_hit_returns_cached_flag(monkeypatch):
    """缓存命中：直接返回缓存答案，cached=True 且不跑 agent。"""
    mock = _mock_agent("should-not-run")
    app.dependency_overrides[get_agent] = lambda: mock
    monkeypatch.setattr(chat_mod._cfg, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(chat_mod.semantic_cache, "lookup_cached", lambda *a, **k: "cached answer")
    monkeypatch.setattr(chat_mod, "record_cache_lookup", MagicMock())

    r = client.post("/api/chat", json={"message": "hi"})

    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "cached answer"
    assert body["cached"] is True
    mock.run.assert_not_called()
