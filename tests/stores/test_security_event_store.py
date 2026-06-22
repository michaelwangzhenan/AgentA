"""实时安全拦截事件存储 + 软失败埋点 + runtime API UT（iter_14 §4.3）。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_security_event_store
from src.api.main import app
from src.stores.user_context import use_user
from src.stores import security_event_store as ses
from src.stores.security_event_store import (
    EVENT_SCRUB,
    EVENT_SSRF,
    EVENT_TOOL,
    SecurityEventStore,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SecurityEventStore]:
    s = SecurityEventStore(str(tmp_path / "usage.db"))
    yield s
    s.close()


# ── 存储读写 ─────────────────────────────────────────────────────────────────


class TestStore:
    def test_record_and_summary(self, store: SecurityEventStore) -> None:
        store.record(EVENT_SCRUB, "知识库检索", user_id=1)
        store.record(EVENT_SCRUB, "web 搜索", user_id=1)
        store.record(EVENT_TOOL, "fetch_url", user_id=1)
        store.record(EVENT_SSRF, "http://10.0.0.1", user_id=2)

        s = store.summary(0, int(time.time()) + 10)
        assert s["total"] == 4
        assert s["by_type"] == {"scrub": 2, "tool": 1, "ssrf": 1}

    def test_summary_filters_user(self, store: SecurityEventStore) -> None:
        store.record(EVENT_TOOL, "a", user_id=1)
        store.record(EVENT_SSRF, "b", user_id=2)
        s = store.summary(0, int(time.time()) + 10, user_id=2)
        assert s["total"] == 1 and s["by_type"]["ssrf"] == 1

    def test_recent_desc_and_limit(self, store: SecurityEventStore) -> None:
        for i in range(5):
            store.record(EVENT_TOOL, f"tool{i}", user_id=1)
        rows = store.recent(0, int(time.time()) + 10, limit=3)
        assert len(rows) == 3
        # 时间倒序：最后写入的在最前（同秒时按 id 倒序）
        assert rows[0]["detail"] == "tool4"

    def test_summary_empty_by_type_keys(self, store: SecurityEventStore) -> None:
        s = store.summary(0, int(time.time()) + 10)
        assert s["total"] == 0
        assert set(s["by_type"].keys()) == {"scrub", "tool", "ssrf"}

    def test_list_events_filter_sort_paginate(self, store: SecurityEventStore) -> None:
        for i in range(6):
            store.record(EVENT_TOOL, f"t{i}", user_id=1)
        store.record(EVENT_SSRF, "s0", user_id=2)
        end = int(time.time()) + 10

        # 按类型筛选
        r = store.list_events(0, end, event_type=EVENT_SSRF)
        assert r["total"] == 1 and r["items"][0]["detail"] == "s0"

        # 按用户筛选
        r = store.list_events(0, end, user_id=1)
        assert r["total"] == 6

        # 分页：total 不变，单页只取 limit 条
        r = store.list_events(0, end, user_id=1, limit=2, offset=0)
        assert r["total"] == 6 and len(r["items"]) == 2

        # 排序：按 user_id 升序，第一条应是 user_id 最小的
        r = store.list_events(0, end, sort_by="user_id", desc=False, limit=1)
        assert r["items"][0]["user_id"] == 1

        # 非法 sort_by 回落 created_at（不报错）
        r = store.list_events(0, end, sort_by="detail; DROP TABLE")
        assert r["total"] == 7

    def test_delete_all_for_user(self, store: SecurityEventStore) -> None:
        store.record(EVENT_TOOL, "a", user_id=1)
        store.record(EVENT_TOOL, "b", user_id=1)
        store.record(EVENT_TOOL, "c", user_id=2)
        n = store.delete_all_for_user(1)
        assert n == 2
        assert store.summary(0, int(time.time()) + 10)["total"] == 1


# ── 软失败埋点 ───────────────────────────────────────────────────────────────


class TestRecordSoftFail:
    def test_record_uses_current_user(self, store: SecurityEventStore) -> None:
        ses.reset_shared_store_for_testing(store)
        try:
            with use_user(7):
                ses.record_security_event(EVENT_SSRF, "http://169.254.169.254")
            rows = store.recent(0, int(time.time()) + 10)
            assert rows[0]["user_id"] == 7 and rows[0]["event_type"] == "ssrf"
        finally:
            ses.reset_shared_store_for_testing(None)

    def test_record_swallows_store_error(self) -> None:
        """store 抛异常时 record_security_event 不得向上抛（绝不阻断对话）。"""
        with patch.object(ses, "get_shared_store", side_effect=RuntimeError("boom")):
            ses.record_security_event(EVENT_TOOL, "x")  # 不抛即通过


class TestMcpFetchSsrfGuard:
    """MCP fetch.fetch 带 url 参数必须先过 SSRF 防御（与内置 fetch_url 同一道防线）。"""

    def test_blocked_url_not_forwarded_and_recorded(self, store: SecurityEventStore) -> None:
        import src.agent.tools as tools

        ses.reset_shared_store_for_testing(store)
        mgr = MagicMock()  # call_tool 不应被调用
        try:
            with patch("src.agent.core.mcp_manager.get_shared_manager", return_value=mgr):
                res = tools._execute_mcp_tool("fetch.fetch", {"url": "http://127.0.0.1:8000"})
            assert res.status == "error" and "安全策略" in res.content
            mgr.call_tool.assert_not_called()
            assert store.summary(0, int(time.time()) + 10)["by_type"]["ssrf"] == 1
        finally:
            ses.reset_shared_store_for_testing(None)

    def test_safe_url_forwarded(self, store: SecurityEventStore) -> None:
        import src.agent.tools as tools

        ses.reset_shared_store_for_testing(store)
        mgr = MagicMock()
        mgr.call_tool.return_value = "正常网页正文"
        try:
            with patch("src.agent.core.mcp_manager.get_shared_manager", return_value=mgr):
                res = tools._execute_mcp_tool("fetch.fetch", {"url": "https://8.8.8.8/"})
            assert res.status == "ok"
            mgr.call_tool.assert_called_once()
            assert store.summary(0, int(time.time()) + 10)["by_type"]["ssrf"] == 0
        finally:
            ses.reset_shared_store_for_testing(None)


# ── runtime API ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(store: SecurityEventStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_security_event_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_runtime_summary_api(client: TestClient, store: SecurityEventStore) -> None:
    store.record(EVENT_SCRUB, "知识库检索", user_id=1)
    store.record(EVENT_TOOL, "fetch_url", user_id=1)
    r = client.get("/api/eval/security/runtime/summary?range=30d")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["by_type"]["scrub"] == 1 and body["by_type"]["tool"] == 1
    assert len(body["recent"]) == 2
    assert body["recent"][0]["event_type"] in ("scrub", "tool")


def test_runtime_summary_empty(client: TestClient) -> None:
    r = client.get("/api/eval/security/runtime/summary")
    assert r.status_code == 200
    assert r.json()["total"] == 0
