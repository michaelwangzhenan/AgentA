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
from src.stores.golden_store import GoldenStore, STATUS_PENDING
from src.stores.trace_store import TraceStore


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


def test_golden_doc_id_filter(client: TestClient, golden: GoldenStore) -> None:
    golden.create("q-doc-a", doc_id="aaa")
    golden.create("q-doc-b", doc_id="bbb")
    golden.create("q-no-doc")
    r = client.get("/api/eval/golden?doc_id=aaa")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["query"] == "q-doc-a"


def test_golden_source_contains_filter(client: TestClient, golden: GoldenStore) -> None:
    golden.create("q-readme", expected_source_contains="docs/readme.md")
    golden.create("q-guide", expected_source_contains="docs/guide.md")
    r = client.get("/api/eval/golden?source_contains=readme")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["query"] == "q-readme"


def test_golden_export(client: TestClient, golden: GoldenStore) -> None:
    golden.create("q1")
    golden.create("q2")
    r = client.get("/api/eval/golden/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    rows = r.json()
    assert isinstance(rows, list) and len(rows) == 2


def test_golden_generate_missing_file_404(client: TestClient) -> None:
    r = client.post(
        "/api/eval/golden/generate",
        json={"model": "en", "source": "nope.md", "doc_id": "x"},
    )
    assert r.status_code == 404


def test_golden_generate_ok(
    client: TestClient, golden: GoldenStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.config as cfg
    # 造一个 web_uploads/<model>/<source> 物理文件
    upload_root = tmp_path / "web_uploads"
    (upload_root / "en").mkdir(parents=True)
    (upload_root / "en" / "doc.md").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(cfg, "WEB_UPLOAD_DIR", str(upload_root))
    # 预置该 doc 的旧 pending 候选（应被重生成清掉）
    golden.create("old-pending", doc_id="d1", status=STATUS_PENDING)
    # mock LLM 出题：直接写两条
    import src.rag.golden_gen as gg

    def fake_run(file_path, source, doc_id="", max_q=None, force=False):
        from src.stores.golden_store import SOURCE_AI, STATUS_PENDING as SP, get_shared_store
        st = get_shared_store()
        st.create(query="gen-1", expected_source_contains=source, source=SOURCE_AI, status=SP, doc_id=doc_id)
        st.create(query="gen-2", expected_source_contains=source, source=SOURCE_AI, status=SP, doc_id=doc_id)
        return 2

    # generate 路由内部用 get_shared_store；让它与注入的 golden 是同一个
    monkeypatch.setattr("src.stores.golden_store.get_shared_store", lambda: golden)
    monkeypatch.setattr(gg, "run_generation_for_file", fake_run)

    r = client.post(
        "/api/eval/golden/generate",
        json={"model": "en", "source": "doc.md", "doc_id": "d1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] == 2
    assert body["removed_pending"] == 1


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

    monkeypatch.setattr("src.services.eval_runner.start", boom)
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

    monkeypatch.setattr("src.services.eval_runner.start", fake_start)
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

    monkeypatch.setattr("src.services.eval_runner.start", fake_start)
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
        "src.services.eval_runner.start",
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

    monkeypatch.setattr("src.services.eval_runner.start", fake_start)
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

    monkeypatch.setattr("src.services.eval_runner.start", fake_start)
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
        "src.services.eval_runner.start",
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
        "src.services.eval_runner.start",
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


def test_eval_run_critic_pass_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={"task": "critic", "model": "kimi-k2.5", "thresholds": {"pass": 0.85}},
    )
    assert r.status_code == 200
    assert "--pass-threshold" in seen["args"]
    assert "0.85" in seen["args"]


def test_eval_run_mcp_no_model_skips_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
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
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args, model=model) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post("/api/eval/run", json={"task": "mcp", "model": "kimi-k2.5"})
    assert r.status_code == 200
    assert "--no-llm" not in seen["args"]
    assert seen["model"] == "kimi-k2.5"


def test_eval_run_plan_thresholds_and_judge(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args, model=model) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={
            "task": "plan", "model": "kimi-k2.5",
            "options": {"judge": False},
            "thresholds": {"recall": 0.7, "struct": 4.0},
        },
    )
    assert r.status_code == 200
    assert "--no-judge" in seen["args"]
    assert "--recall-threshold" in seen["args"]
    assert "0.7" in seen["args"]
    assert "--struct-threshold" in seen["args"]
    assert "4.0" in seen["args"]


def test_eval_run_plan_judge_on_no_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post("/api/eval/run", json={"task": "plan", "options": {"judge": True}})
    assert r.status_code == 200
    assert "--no-judge" not in seen["args"]


def test_eval_run_plan_rejects_struct_over_5_400(client: TestClient) -> None:
    r = client.post(
        "/api/eval/run",
        json={"task": "plan", "thresholds": {"struct": 6.0}},
    )
    assert r.status_code == 400


def test_eval_summary_plan_by_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plan sidecar：识别 + 结构两条指标，两项达标判 passed。"""
    import json

    import src.api.routes.eval as evalmod

    root = tmp_path / "reports"
    (root / "plan").mkdir(parents=True)
    sidecar = {
        "timestamp": "2026-06-14 10:00:00", "git": "abc",
        "judge_enabled": True, "partial": False,
        "recall": 0.9, "recall_passed": 9, "total": 10, "recall_threshold": 0.8,
        "struct_score": 4.1, "struct_threshold": 3.5,
        "passed": True,
    }
    name = "plan/plan-eval-20260614-100000"
    (root / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (root / f"{name}.md").write_text("# r", encoding="utf-8")
    monkeypatch.setattr(evalmod, "_reports_root", lambda: root)

    r = client.get(f"/api/eval/summary?task=plan&report={name}.md")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["passed"] is True
    assert len(body["metrics"]) == 2
    assert body["metrics"][1]["label"] == "plan 结构均分"


def test_eval_run_plan_judge_model_passed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    # judge 开 + 指定评委模型 → 带 --judge-model
    r = client.post(
        "/api/eval/run",
        json={"task": "plan", "model": "kimi-k2.5",
              "options": {"judge": True, "judge_model": "deepseek-v4-pro"}},
    )
    assert r.status_code == 200
    assert "--judge-model" in seen["args"]
    assert "deepseek-v4-pro" in seen["args"]


def test_eval_run_plan_no_judge_skips_judge_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    # 关 judge → 即便给了评委模型也不传（评委无意义）
    r = client.post(
        "/api/eval/run",
        json={"task": "plan", "model": "kimi-k2.5",
              "options": {"judge": False, "judge_model": "deepseek-v4-pro"}},
    )
    assert r.status_code == 200
    assert "--no-judge" in seen["args"]
    assert "--judge-model" not in seen["args"]


def test_eval_run_learning_plan_args(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={"task": "learning_plan", "model": "kimi-k2.5",
              "options": {"judge": True, "judge_model": "deepseek-v4-pro"},
              "thresholds": {"recall": 0.75, "quality": 4.2}},
    )
    assert r.status_code == 200
    assert "--recall-threshold" in seen["args"]
    assert "0.75" in seen["args"]
    assert "--quality-threshold" in seen["args"]
    assert "4.2" in seen["args"]
    assert "--judge-model" in seen["args"]
    assert "deepseek-v4-pro" in seen["args"]


def test_eval_summary_learning_plan_by_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    import src.api.routes.eval as evalmod

    root = tmp_path / "reports"
    (root / "learning_plan").mkdir(parents=True)
    sidecar = {
        "timestamp": "2026-06-15 10:00:00", "git": "abc",
        "judge_enabled": True, "partial": False,
        "recall": 0.9, "recall_passed": 9, "total": 10, "recall_threshold": 0.8,
        "struct_score": 4.3, "struct_threshold": 4.0,
        "passed": True,
    }
    name = "learning_plan/learning-plan-eval-20260615-100000"
    (root / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (root / f"{name}.md").write_text("# r", encoding="utf-8")
    monkeypatch.setattr(evalmod, "_reports_root", lambda: root)

    r = client.get(f"/api/eval/summary?task=learning_plan&report={name}.md")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["passed"] is True
    assert len(body["metrics"]) == 2
    assert body["metrics"][1]["label"] == "plan 质量均分"


def test_eval_run_srs_pass_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={"task": "srs", "model": "kimi-k2.5", "thresholds": {"pass": 0.7}},
    )
    assert r.status_code == 200
    assert "--pass-threshold" in seen["args"]
    assert "0.7" in seen["args"]


def test_eval_run_quiz_args(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post(
        "/api/eval/run",
        json={"task": "quiz", "model": "kimi-k2.5",
              "options": {"judge": True, "judge_model": "deepseek-v4-pro"},
              "thresholds": {"recall": 0.8, "quality": 4.0}},
    )
    assert r.status_code == 200
    assert "--recall-threshold" in seen["args"]
    assert "--quality-threshold" in seen["args"]
    assert "--judge-model" in seen["args"]
    assert "deepseek-v4-pro" in seen["args"]


def test_eval_run_perf_target_all(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        "src.services.eval_runner.start",
        lambda task, args, model=None: (seen.update(args=args, model=model) or {
            "state": "running", "task": task, "model": model, "args": args,
            "started_at": 1.0, "finished_at": None, "returncode": None, "tail": ""}),
    )
    r = client.post("/api/eval/run", json={"task": "perf", "options": {"sizes": " 100, 1000 "}})
    assert r.status_code == 200
    assert seen["args"][:2] == ["--target", "all"]
    assert "--sizes" in seen["args"]
    assert "100,1000" in seen["args"]
    assert seen["model"] is None


def test_eval_run_perf_rejects_bad_sizes_400(client: TestClient) -> None:
    r = client.post("/api/eval/run", json={"task": "perf", "options": {"sizes": "abc"}})
    assert r.status_code == 400


def test_eval_summary_perf_by_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """性能 sidecar：各 target 判据展开为 metrics，全过判 passed。"""
    import json

    import src.api.routes.eval as evalmod

    root = tmp_path / "reports"
    (root / "perf").mkdir(parents=True)
    sidecar = {
        "timestamp": "2026-06-14 10:00:00", "git": "abc",
        "passed": False,
        "targets": {
            "session": {"size": 1000, "ok": True, "checks": [
                {"name": "查询类 4 列 < 50 ms", "ok": True, "note": "实测最大 3.2 ms"},
            ]},
            "memory": {"size": 1000, "ok": False, "checks": [
                {"name": "load_all < 20 ms", "ok": False, "note": "实测 33.0 ms"},
            ]},
        },
    }
    name = "perf/perf-20260614-100000"
    (root / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (root / f"{name}.md").write_text("# r", encoding="utf-8")
    monkeypatch.setattr(evalmod, "_reports_root", lambda: root)

    r = client.get(f"/api/eval/summary?task=perf&report={name}.md")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["passed"] is False
    assert body["partial"] is False  # 性能始终跑全量
    assert len(body["metrics"]) == 2
    assert body["metrics"][0]["label"].startswith("会话·")


def test_eval_run_status_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.services.eval_runner.status",
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
