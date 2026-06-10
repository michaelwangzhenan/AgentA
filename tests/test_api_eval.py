"""评估 + 可观测端点 UT（iter_14）。

认证默认关闭 → 当前用户 id=1、admin 角色，故 golden / reports 的 admin 门直接放行。
用 dependency_overrides 注入临时 GoldenStore / TraceStore。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_golden_store, get_trace_store
from src.api.main import app
from src.memory.golden_store import GoldenStore, STATUS_PENDING
from src.memory.trace_store import TraceStore


@pytest.fixture
def golden(tmp_path: Path) -> Iterator[GoldenStore]:
    s = GoldenStore(str(tmp_path / "golden.db"))
    yield s
    s.close()


@pytest.fixture
def traces(tmp_path: Path) -> Iterator[TraceStore]:
    s = TraceStore(str(tmp_path / "usage.db"))
    yield s
    s.close()


@pytest.fixture
def client(golden: GoldenStore, traces: TraceStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_golden_store] = lambda: golden
    app.dependency_overrides[get_trace_store] = lambda: traces
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── golden CRUD ───────────────────────────────────────────────────────────────


def test_golden_create_list_update_delete(client: TestClient) -> None:
    r = client.post("/api/eval/golden", json={
        "query": "什么是 RAG?", "expected_keywords": ["RAG"],
        "expected_source_contains": "readme.md", "note": "",
    })
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    assert r.json()["status"] == "approved"

    r = client.get("/api/eval/golden")
    body = r.json()
    assert body["total"] == 1
    assert body["counts"]["approved"] == 1

    # 改状态为 rejected
    r = client.put(f"/api/eval/golden/{gid}", json={"status": "rejected"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    r = client.delete(f"/api/eval/golden/{gid}")
    assert r.json()["deleted"] is True


def test_golden_create_empty_query_422(client: TestClient) -> None:
    r = client.post("/api/eval/golden", json={"query": ""})
    assert r.status_code == 422


def test_golden_update_missing_404(client: TestClient) -> None:
    r = client.put("/api/eval/golden/99999", json={"note": "x"})
    assert r.status_code == 404


def test_golden_status_filter(client: TestClient, golden: GoldenStore) -> None:
    golden.create("approved-q")
    golden.create("pending-q", status=STATUS_PENDING)
    r = client.get("/api/eval/golden?status=pending")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["query"] == "pending-q"


# ── trace 可观测 ─────────────────────────────────────────────────────────────


def _seed_trace(store: TraceStore, trace_id: str, **kw) -> None:
    base = dict(trace_id=trace_id, user_id=1, session_id="s1", model_id="kimi-k2.5",
                total_ms=120.0, llm_ms=80.0, tool_ms=20.0, retrieval_ms=20.0,
                llm_calls=1, tool_calls=1, total_tokens=100, status="ok")
    base.update(kw)
    spans = [{"stage": "llm", "name": "LLM 第 1 轮", "start_ms": 0, "duration_ms": 80}]
    store.record_trace(base, spans)


def test_trace_overview(client: TestClient, traces: TraceStore) -> None:
    _seed_trace(traces, "t1")
    _seed_trace(traces, "t2", total_ms=200.0)
    r = client.get("/api/eval/trace/overview?range=30d")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["latency_avg_ms"] == 160.0


def test_trace_list_and_detail(client: TestClient, traces: TraceStore) -> None:
    _seed_trace(traces, "td")
    r = client.get("/api/eval/trace/list?range=30d")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.get("/api/eval/trace/td")
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == "td"
    assert len(body["spans"]) == 1
    assert body["spans"][0]["stage"] == "llm"


def test_trace_detail_404(client: TestClient) -> None:
    r = client.get("/api/eval/trace/nope")
    assert r.status_code == 404


def test_trace_series(client: TestClient, traces: TraceStore) -> None:
    _seed_trace(traces, "ts1")
    r = client.get("/api/eval/trace/series?range=30d")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["count"] == 1


# ── 报告浏览：路径穿越防护 ─────────────────────────────────────────────────────


def test_reports_list_ok(client: TestClient) -> None:
    r = client.get("/api/eval/reports")
    assert r.status_code == 200
    assert "reports" in r.json()


def test_report_content_rejects_bad_name(client: TestClient) -> None:
    for bad in ["../secret.md", "agent_eval/../../x.md", "unknown/foo.md", "agent_eval/sub/dir.md"]:
        r = client.get(f"/api/eval/reports/content?name={bad}")
        assert r.status_code in (400, 404), f"{bad} -> {r.status_code}"
