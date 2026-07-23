"""聊天接口学习范围限制 UT。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.deps import get_agent
from src.api.main import app
from src.services import sensitive_word_filter as swf_mod
from src.services.learning_scope import ScopeResult, out_of_scope_reply
from src.stores.security_event_store import EVENT_LEARNING_SCOPE, get_shared_store


def _mock_agent(reply: str = "ok") -> MagicMock:
    agent = MagicMock()
    agent.run.return_value = reply
    return agent


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    current: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("data:"):
            current["data"] = json.loads(line[5:].strip())
        elif line.startswith("event:"):
            current["event"] = line[6:].strip()
    if current:
        events.append(current)
    return events


@pytest.fixture(autouse=True)
def _load_filter(tmp_path: Path):
    base = tmp_path / "words"
    base.mkdir()
    (base / "metadata.json").write_text('{"version":"api-test"}', encoding="utf-8")
    (base / "deny.tsv").write_text("testblock\ttest\n", encoding="utf-8")
    (base / "allow.txt").write_text("", encoding="utf-8")
    swf_mod.ensure_loaded_for_testing(base)
    yield
    swf_mod.reset_shared_filter_for_testing(None)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def scope_enabled():
    orig = _cfg.LEARNING_SCOPE_ONLY
    _cfg.LEARNING_SCOPE_ONLY = True
    yield
    _cfg.LEARNING_SCOPE_ONLY = orig


client = TestClient(app)


def test_chat_skips_scope_check_when_disabled():
    mock = _mock_agent("hello")
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.classify_learning_scope",
        return_value=ScopeResult(in_scope=False),
    ) as classify_mock:
        r = client.post("/api/chat", json={"message": "今天天气怎么样"})

    assert r.status_code == 200
    classify_mock.assert_not_called()
    mock.run.assert_called_once()


def test_chat_blocks_out_of_scope(scope_enabled):
    mock = _mock_agent()
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.classify_learning_scope",
        return_value=ScopeResult(in_scope=False, reason="天气"),
    ):
        r = client.post("/api/chat", json={"message": "今天天气怎么样"})

    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == out_of_scope_reply("天气")
    assert body["learning_scope_filtered"] is True
    assert "个人学习助手" in body["reply"]
    mock.run.assert_not_called()


def test_chat_passes_in_scope(scope_enabled):
    mock = _mock_agent("解释完毕")
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.classify_learning_scope",
        return_value=ScopeResult(in_scope=True),
    ):
        r = client.post("/api/chat", json={"message": "解释这段 Python 报错"})

    assert r.status_code == 200
    mock.run.assert_called_once()


def test_chat_records_learning_scope_event(scope_enabled):
    mock = _mock_agent()
    app.dependency_overrides[get_agent] = lambda: mock
    store = get_shared_store()

    with patch(
        "src.api.routes.chat.classify_learning_scope",
        return_value=ScopeResult(in_scope=False, reason="天气"),
    ):
        client.post("/api/chat", json={"message": "今天天气怎么样"})

    rows = store.recent(0, int(time.time()) + 10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_LEARNING_SCOPE
    detail = json.loads(rows[0]["detail"])
    assert detail["in_scope"] is False
    assert detail["action"] == "blocked"


def test_stream_blocks_out_of_scope(scope_enabled):
    mock = _mock_agent()
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.classify_learning_scope",
        return_value=ScopeResult(in_scope=False, reason="天气"),
    ):
        with client.stream(
            "POST", "/api/chat/stream", json={"message": "今天天气怎么样"}
        ) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())

    frames = [ev["data"] for ev in _parse_sse(text) if "data" in ev]
    assert frames[-1]["type"] == "final_answer"
    assert frames[-1]["payload"]["text"] == out_of_scope_reply("天气")
    assert frames[-1]["payload"].get("learning_scope_filtered") is True
    mock.run.assert_not_called()
