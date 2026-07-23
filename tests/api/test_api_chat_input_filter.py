"""聊天接口敏感词前置过滤 UT。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_agent
from src.api.main import app
from src.api.routes import chat as chat_mod
from src.services import sensitive_word_filter as swf_mod
from src.stores.security_event_store import EVENT_INPUT_FILTER, get_shared_store


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


client = TestClient(app)


def test_chat_blocks_sensitive_word_as_normal_reply():
    mock = _mock_agent()
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "包含 testblock 词"})

    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == chat_mod._INPUT_FILTER_BLOCKED_DETAIL
    assert body["input_filtered"] is True
    mock.run.assert_not_called()


def test_chat_passes_clean_message():
    mock = _mock_agent("hello")
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "你好"})

    assert r.status_code == 200
    mock.run.assert_called_once()


def test_chat_filter_unavailable_returns_503(tmp_path: Path):
    broken = tmp_path / "broken"
    broken.mkdir()
    swf_mod.ensure_loaded_for_testing(broken)
    mock = _mock_agent()
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "你好"})

    assert r.status_code == 503
    mock.run.assert_not_called()


def test_chat_records_input_filter_event():
    mock = _mock_agent()
    app.dependency_overrides[get_agent] = lambda: mock
    store = get_shared_store()

    client.post("/api/chat", json={"message": "testblock"})

    rows = store.recent(0, int(time.time()) + 10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_INPUT_FILTER
    detail = json.loads(rows[0]["detail"])
    assert detail["word"] == "testblock"
    assert detail["action"] == "blocked"


def test_stream_blocks_sensitive_word_as_normal_reply():
    mock = _mock_agent()
    app.dependency_overrides[get_agent] = lambda: mock

    with client.stream("POST", "/api/chat/stream", json={"message": "testblock"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())

    frames = [ev["data"] for ev in _parse_sse(text) if "data" in ev]
    assert frames[-1]["type"] == "final_answer"
    assert frames[-1]["payload"]["text"] == chat_mod._INPUT_FILTER_BLOCKED_DETAIL
    assert frames[-1]["payload"].get("input_filtered") is True
    mock.run.assert_not_called()
