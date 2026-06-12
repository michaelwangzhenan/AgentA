"""学习计划 list / detail / active 端点 UT。

不走真 Agent；用临时 SQLite 文件构造独立 LearningPlanStore。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_plan_store
from src.api.main import app
from src.memory.learning_plan_store import LearningPlanStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[LearningPlanStore]:
    db = tmp_path / "test_learning.db"
    s = LearningPlanStore(str(db))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: LearningPlanStore) -> TestClient:
    app.dependency_overrides[get_plan_store] = lambda: store
    return TestClient(app)


# ─── GET /api/plans ──────────────────────────────────────────────────────


def test_list_plans_empty(client: TestClient) -> None:
    r = client.get("/api/plans")
    assert r.status_code == 200
    assert r.json() == {"plans": []}


def test_list_plans_with_data(client: TestClient, store: LearningPlanStore) -> None:
    pid1 = store.create_plan("plan A", weeks=4, set_active=False)
    pid2 = store.create_plan("plan B", weeks=8, set_active=True)
    store.add_tasks(pid1, [{"stage_idx": 1, "order_idx": 1, "title": "task 1"}])
    store.add_tasks(
        pid2,
        [
            {"stage_idx": 1, "order_idx": 1, "title": "t1"},
            {"stage_idx": 1, "order_idx": 2, "title": "t2"},
        ],
    )

    r = client.get("/api/plans")
    assert r.status_code == 200
    plans = r.json()["plans"]
    assert len(plans) == 2
    # active 排在最前
    assert plans[0]["id"] == pid2
    assert plans[0]["is_active"] is True
    assert plans[0]["task_count"] == 2
    assert plans[0]["done_count"] == 0


# ─── GET /api/plans/active ───────────────────────────────────────────────


def test_active_plan_none_when_empty(client: TestClient) -> None:
    r = client.get("/api/plans/active")
    assert r.status_code == 200
    assert r.json() is None


def test_active_plan_returns_tasks(client: TestClient, store: LearningPlanStore) -> None:
    pid = store.create_plan("learn ML", weeks=8)
    store.add_tasks(
        pid,
        [
            {"stage_idx": 1, "order_idx": 1, "title": "math"},
            {"stage_idx": 2, "order_idx": 1, "title": "DL"},
        ],
    )

    r = client.get("/api/plans/active")
    assert r.status_code == 200
    plan = r.json()
    assert plan["id"] == pid
    assert plan["is_active"] is True
    assert len(plan["tasks"]) == 2
    assert plan["tasks"][0]["title"] == "math"


# ─── GET /api/plans/{id} ────────────────────────────────────────────────


def test_get_plan_404(client: TestClient) -> None:
    r = client.get("/api/plans/9999")
    assert r.status_code == 404


def test_get_plan_with_tasks(client: TestClient, store: LearningPlanStore) -> None:
    pid = store.create_plan("topic", weeks=2)
    store.add_tasks(pid, [{"stage_idx": 1, "order_idx": 1, "title": "t1"}])

    r = client.get(f"/api/plans/{pid}")
    assert r.status_code == 200
    plan = r.json()
    assert plan["id"] == pid
    assert plan["tasks"][0]["title"] == "t1"


# ─── POST /api/plans（新建）─────────────────────────────────────────────


def test_create_plan_with_tasks(client: TestClient) -> None:
    r = client.post(
        "/api/plans",
        json={
            "goal": "8 周学 ML",
            "weeks": 8,
            "tasks": [
                {"stage_idx": 1, "order_idx": 1, "title": "线性代数"},
                {"stage_idx": 1, "order_idx": 2, "title": "概率"},
            ],
        },
    )
    assert r.status_code == 201
    plan = r.json()
    assert plan["goal"] == "8 周学 ML"
    assert plan["is_active"] is True
    assert len(plan["tasks"]) == 2


def test_create_plan_empty_goal_400(client: TestClient) -> None:
    # goal 必填非空 → pydantic 422
    r = client.post("/api/plans", json={"goal": "", "weeks": 1})
    assert r.status_code == 422


# ─── PATCH /api/plans/{id}/tasks/{task_id}（改任务）─────────────────────


def test_update_task_status(client: TestClient, store: LearningPlanStore) -> None:
    pid = store.create_plan("p", weeks=1)
    store.add_tasks(pid, [{"stage_idx": 1, "order_idx": 1, "title": "t1"}])
    task_id = store.get_plan_with_tasks(pid)["tasks"][0]["id"]

    r = client.patch(
        f"/api/plans/{pid}/tasks/{task_id}",
        json={"status": "success", "note": "done"},
    )
    assert r.status_code == 200
    task = r.json()["tasks"][0]
    assert task["status"] == "success"
    assert task["note"] == "done"


def test_update_task_invalid_status_400(client: TestClient, store: LearningPlanStore) -> None:
    pid = store.create_plan("p", weeks=1)
    store.add_tasks(pid, [{"stage_idx": 1, "order_idx": 1, "title": "t1"}])
    task_id = store.get_plan_with_tasks(pid)["tasks"][0]["id"]

    r = client.patch(
        f"/api/plans/{pid}/tasks/{task_id}", json={"status": "bogus"},
    )
    assert r.status_code == 400


# ─── activate / abandon ─────────────────────────────────────────────────


def test_activate_plan(client: TestClient, store: LearningPlanStore) -> None:
    pid1 = store.create_plan("A", set_active=True)
    pid2 = store.create_plan("B", set_active=True)  # B 现在 active

    r = client.post(f"/api/plans/{pid1}/activate")
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    # B 已被切非 active
    assert store.get_plan(pid2)["is_active"] is False


def test_abandon_plan(client: TestClient, store: LearningPlanStore) -> None:
    pid = store.create_plan("A", set_active=True)
    r = client.post(f"/api/plans/{pid}/abandon")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "abandoned"
    assert body["is_active"] is False


def test_abandon_plan_404(client: TestClient) -> None:
    r = client.post("/api/plans/9999/abandon")
    assert r.status_code == 404
