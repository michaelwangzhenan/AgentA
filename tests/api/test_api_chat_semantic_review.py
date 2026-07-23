"""聊天接口输出语义复核 UT。"""

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
from src.services.llm_user_message import CONTENT_BLOCKED_REPLY
from src.services.output_semantic_review import ReviewResult
from src.stores.security_event_store import EVENT_SEMANTIC_REVIEW, get_shared_store


def _mock_agent(reply: str = "模型原始回答") -> MagicMock:
    agent = MagicMock()
    agent.run.return_value = reply
    return agent


def _mock_agent_stream(reply: str = "模型原始回答") -> MagicMock:
    from src.agent.core.event_bus import AgentEvent

    agent = MagicMock()

    def _run(message, session_id=None, event_callback=None, **_kwargs):
        if event_callback is not None:
            event_callback(
                AgentEvent(
                    type="final_answer",
                    payload={"text": reply, "usage": None},
                )
            )
        return reply

    agent.run.side_effect = _run
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
def review_enabled():
    orig = _cfg.OUTPUT_SEMANTIC_REVIEW
    _cfg.OUTPUT_SEMANTIC_REVIEW = True
    yield
    _cfg.OUTPUT_SEMANTIC_REVIEW = orig


client = TestClient(app)


def test_chat_skips_semantic_review_when_disabled():
    mock = _mock_agent("hello")
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.review_output_semantic",
        return_value=ReviewResult(safe=False),
    ) as review_mock:
        r = client.post("/api/chat", json={"message": "你好"})

    assert r.status_code == 200
    review_mock.assert_not_called()
    assert r.json()["reply"] == "hello"


def test_chat_blocks_unsafe_output(review_enabled):
    mock = _mock_agent("危险回答")
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.review_output_semantic",
        return_value=ReviewResult(safe=False, category="illegal", reason="危害"),
    ):
        r = client.post("/api/chat", json={"message": "你好"})

    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == CONTENT_BLOCKED_REPLY
    assert body["semantic_review_filtered"] is True
    mock.run.assert_called_once()


def test_chat_passes_safe_output(review_enabled):
    mock = _mock_agent("正常学习回答")
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.review_output_semantic",
        return_value=ReviewResult(safe=True),
    ):
        r = client.post("/api/chat", json={"message": "解释 Python 报错"})

    assert r.status_code == 200
    assert r.json()["reply"] == "正常学习回答"
    assert r.json()["semantic_review_filtered"] is False


def test_chat_records_semantic_review_event(review_enabled):
    mock = _mock_agent("危险回答")
    app.dependency_overrides[get_agent] = lambda: mock
    store = get_shared_store()

    with patch(
        "src.api.routes.chat.review_output_semantic",
        return_value=ReviewResult(safe=False, category="illegal"),
    ):
        client.post("/api/chat", json={"message": "你好"})

    rows = store.recent(0, int(time.time()) + 10)
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_SEMANTIC_REVIEW
    detail = json.loads(rows[0]["detail"])
    assert detail["safe"] is False
    assert detail["action"] == "blocked"


def test_stream_blocks_unsafe_output_after_buffer(review_enabled):
    mock = _mock_agent_stream("危险回答")
    app.dependency_overrides[get_agent] = lambda: mock

    with patch(
        "src.api.routes.chat.review_output_semantic",
        return_value=ReviewResult(safe=False, category="illegal"),
    ):
        with client.stream("POST", "/api/chat/stream", json={"message": "你好"}) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())

    frames = [ev["data"] for ev in _parse_sse(text) if "data" in ev]
    assert frames[-1]["type"] == "final_answer"
    assert frames[-1]["payload"]["text"] == CONTENT_BLOCKED_REPLY
    assert frames[-1]["payload"].get("semantic_review_filtered") is True
    mock.run.assert_called_once()
