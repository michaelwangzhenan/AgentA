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


# ── 离线评估：触发 / 状态 / 取消 / 摘要 ───────────────────────────────────────


def test_eval_run_unknown_task_400(client: TestClient) -> None:
    r = client.post("/api/eval/run", json={"task": "nope"})
    assert r.status_code == 400


def test_eval_run_busy_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(task, args, model=None):
        raise RuntimeError("已有评估在运行")

    monkeypatch.setattr("src.eval_runner.start", boom)
    r = client.post("/api/eval/run", json={"task": "security"})
    assert r.status_code == 409


def test_eval_run_security_builds_args(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def fake_start(task, args, model=None):
        seen["task"] = task
        seen["args"] = args
        seen["model"] = model
        return {"state": "running", "task": task, "model": model, "args": args,
                "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}

    monkeypatch.setattr("src.eval_runner.start", fake_start)
    r = client.post(
        "/api/eval/run",
        json={
            "task": "security", "no_llm": True,
            "options": {"kind": "direct"}, "model": "kimi-k2.5",
        },
    )
    assert r.status_code == 200
    assert seen["args"] == ["--no-llm", "--kind", "direct"]
    assert seen["model"] == "kimi-k2.5"


def test_eval_run_rejects_bad_kind_400(client: TestClient) -> None:
    r = client.post("/api/eval/run", json={"task": "security", "options": {"kind": "evil"}})
    assert r.status_code == 400


def test_eval_run_rag_retrieval_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RAG + None（no_llm）：含消融开关、不带 --llm、自动补 -o。"""
    seen: dict = {}

    def fake_start(task, args, model=None):
        seen["args"] = args
        return {"state": "running", "task": task, "model": model, "args": args,
                "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}

    monkeypatch.setattr("src.eval_runner.start", fake_start)
    # 复选框正向：rewriter/rerank=False → 关闭 → 传 --no-*
    r = client.post(
        "/api/eval/run",
        json={"task": "rag", "no_llm": True, "options": {"rewriter": False, "rerank": False}},
    )
    assert r.status_code == 200
    assert "--no-rewriter" in seen["args"]
    assert "--no-rerank" in seen["args"]
    assert "--llm" not in seen["args"]
    assert "-o" in seen["args"]
    out = seen["args"][seen["args"].index("-o") + 1]
    assert out.startswith("tools/reports/rag/")


def test_eval_run_rag_rewriter_on_no_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rewriter/rerank=True（默认开）→ 不传 --no-*。"""
    seen: dict = {}
    monkeypatch.setattr(
        "src.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={"task": "rag", "no_llm": True, "options": {"rewriter": True, "rerank": True}},
    )
    assert r.status_code == 200
    assert "--no-rewriter" not in seen["args"]
    assert "--no-rerank" not in seen["args"]


def test_eval_run_rag_with_model_adds_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def fake_start(task, args, model=None):
        seen["args"] = args
        return {"state": "running", "task": task, "model": model, "args": args,
                "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}

    monkeypatch.setattr("src.eval_runner.start", fake_start)
    r = client.post(
        "/api/eval/run",
        json={
            "task": "rag", "model": "kimi-k2.5",
            "options": {"llm_count": 5, "judge_model": "kimi-k2.5"},
        },
    )
    assert r.status_code == 200
    assert "--llm" in seen["args"]
    assert "5" in seen["args"]
    assert "--judge-model" in seen["args"]
    assert "kimi-k2.5" in seen["args"]


def test_eval_run_thresholds_to_args(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def fake_start(task, args, model=None):
        seen["args"] = args
        return {"state": "running", "task": task, "model": model, "args": args,
                "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}

    monkeypatch.setattr("src.eval_runner.start", fake_start)
    r = client.post(
        "/api/eval/run",
        json={"task": "security", "thresholds": {"recall": 0.8, "fpr": 0.2}},
    )
    assert r.status_code == 200
    assert "--recall-threshold" in seen["args"]
    assert "0.8" in seen["args"]
    assert "--fpr-threshold" in seen["args"]
    assert "0.2" in seen["args"]


def test_eval_run_rejects_bad_threshold_400(client: TestClient) -> None:
    r = client.post(
        "/api/eval/run",
        json={"task": "security", "thresholds": {"recall": 1.5}},
    )
    assert r.status_code == 400


def test_eval_run_memory_pass_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={"task": "memory", "model": "kimi-k2.5", "thresholds": {"pass": 0.7}},
    )
    assert r.status_code == 200
    assert "--pass-threshold" in seen["args"]
    assert "0.7" in seen["args"]


def test_eval_run_skills_pass_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={"task": "skills", "model": "kimi-k2.5", "thresholds": {"pass": 0.75}},
    )
    assert r.status_code == 200
    assert "--pass-threshold" in seen["args"]
    assert "0.75" in seen["args"]


def test_eval_run_mcp_no_model_skips_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args, model=model) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    # 不选模型（None）→ 只跑 structural
    r = client.post("/api/eval/run", json={"task": "mcp", "no_llm": True})
    assert r.status_code == 200
    assert "--no-llm" in seen["args"]


def test_eval_run_mcp_with_model_full(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args, model=model) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post("/api/eval/run", json={"task": "mcp", "model": "kimi-k2.5"})
    assert r.status_code == 200
    assert "--no-llm" not in seen["args"]
    assert seen["model"] == "kimi-k2.5"


def test_eval_run_status_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.eval_runner.status",
        lambda: {"state": "idle", "args": [], "tail": ""},
    )
    r = client.get("/api/eval/run/status")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_eval_summary_unknown_task(client: TestClient) -> None:
    r = client.get("/api/eval/summary?task=nope")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["task"] == "nope"


def test_eval_summary_by_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """带 report 名 → 读该报告配对的 .json sidecar 映射成卡片。"""
    import json

    import src.api.routes.eval as evalmod

    root = tmp_path / "reports"
    (root / "security").mkdir(parents=True)
    sidecar = {
        "timestamp": "2026-06-14T10:00:00", "git": "abc",
        "passed": True, "partial": False,
        "recall": 0.95, "fpr": 0.05, "recall_threshold": 0.9, "fpr_threshold": 0.1,
        "attack_blocked": 19, "attacks": 20, "benign_blocked": 1, "benigns": 20,
    }
    name = "security/security-adversarial-20260614-100000"
    (root / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (root / f"{name}.md").write_text("# r", encoding="utf-8")
    monkeypatch.setattr(evalmod, "_reports_root", lambda: root)

    r = client.get(f"/api/eval/summary?task=security&report={name}.md")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["passed"] is True
    assert len(body["metrics"]) == 2


def test_eval_summary_mcp_by_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MCP sidecar：有失败即不通过；skipped 不算失败。"""
    import json

    import src.api.routes.eval as evalmod

    root = tmp_path / "reports"
    (root / "mcp").mkdir(parents=True)
    sidecar = {
        "timestamp": "2026-06-14 10:00:00",
        "total": 10, "passed": 7, "skipped": 2, "failed": 1, "ok": False,
    }
    name = "mcp/mcp-20260614-100000"
    (root / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (root / f"{name}.md").write_text("# r", encoding="utf-8")
    monkeypatch.setattr(evalmod, "_reports_root", lambda: root)

    r = client.get(f"/api/eval/summary?task=mcp&report={name}.md")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["passed"] is False
    assert body["partial"] is True
    assert len(body["metrics"]) == 1
    assert "跳过 2" in body["metrics"][0]["value"]
