"""input_filter 安全事件写入与 runtime API 展示 UT。"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_security_event_store
from src.api.main import app
from src.stores.security_event_store import EVENT_INPUT_FILTER, SecurityEventStore


@pytest.fixture
def store(tmp_path) -> Iterator[SecurityEventStore]:
    s = SecurityEventStore(str(tmp_path / "usage.db"))
    yield s
    s.close()


@pytest.fixture
def client(store: SecurityEventStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_security_event_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_input_filter_event_in_summary(client: TestClient, store: SecurityEventStore) -> None:
    detail = json.dumps(
        {
            "word_list_version": "1.0.0",
            "word": "testblock",
            "category": "test",
            "action": "blocked",
        },
        ensure_ascii=False,
    )
    store.record(EVENT_INPUT_FILTER, detail, user_id=1)

    r = client.get("/api/eval/security/runtime/summary?range=30d")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["by_type"]["input_filter"] == 1
    assert body["recent"][0]["event_type"] == EVENT_INPUT_FILTER


def test_input_filter_event_list_filter(client: TestClient, store: SecurityEventStore) -> None:
    store.record(EVENT_INPUT_FILTER, '{"action":"blocked"}', user_id=2)
    store.record("scrub", "知识库", user_id=1)
    end = int(time.time()) + 10

    page = store.list_events(0, end, event_type=EVENT_INPUT_FILTER)
    assert page["total"] == 1
    assert page["items"][0]["event_type"] == EVENT_INPUT_FILTER

    r = client.get("/api/eval/security/runtime/events?event_type=input_filter")
    assert r.status_code == 200
    assert r.json()["total"] == 1
