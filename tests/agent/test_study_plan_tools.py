"""
测试 Phase 2.2 三业务 tool（[`src/agent/tools.py`](../src/agent/tools.py) G2 / D3 / D4 / D12）。

覆盖：
    - JSON Schema 完整性：`_STUDY_PLAN_TOOLS` 三 tool 名 / required 字段
    - `get_tools()` 返回包含三新 tool（无 skill 也常驻）
    - `_tool_create_study_plan`：入参校验（goal/weeks/tasks 各种非法）+ 落库 + active 切换
    - `_tool_update_study_progress`：入参校验 + plan/task id 配对校验 + 自动 complete_plan
    - `_tool_query_study_status`：active / 指定 plan_id / list_all / detail 各模式
    - `execute_tool` 路由覆盖三 tool 的 case 分发

测试隔离：每个测试用 tmp_path 注入独立 LearningPlanStore 替换全局共享 store。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.agent.tools import ToolResult, execute_tool, get_tools
from src.stores import learning_plan_store as lp_store_module
from src.stores.learning_plan_store import LearningPlanStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[LearningPlanStore]:
    """注入独立 store 替换全局共享 + 测试结束清理。"""
    s = LearningPlanStore(str(tmp_path / "lp.db"))
    lp_store_module.reset_shared_store_for_testing(s)
    yield s
    lp_store_module.reset_shared_store_for_testing(None)
    s.close()


def _ok_tasks() -> list[dict[str, object]]:
    return [
        {"stage_idx": 1, "order_idx": 1, "title": "T1"},
        {"stage_idx": 1, "order_idx": 2, "title": "T2"},
        {"stage_idx": 2, "order_idx": 1, "title": "T3"},
    ]


# ── Schema 完整性 ────────────────────────────────────────────────────────────

class TestSchema:

    def test_get_tools_always_includes_three_study_tools(self) -> None:
        names = {t["function"]["name"] for t in get_tools()}
        assert {"create_study_plan", "update_study_progress", "query_study_status"} <= names

    def test_create_study_plan_required_goal_and_tasks(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "create_study_plan")
        req = set(t["function"]["parameters"]["required"])
        assert {"goal", "tasks"} <= req

    def test_update_study_progress_required_three(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "update_study_progress")
        req = set(t["function"]["parameters"]["required"])
        assert {"plan_id", "task_id", "status"} <= req

    def test_update_study_progress_status_enum(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "update_study_progress")
        enum = t["function"]["parameters"]["properties"]["status"]["enum"]
        assert set(enum) == {"success", "skipped", "pending"}

    def test_query_study_status_no_required(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "query_study_status")
        assert t["function"]["parameters"].get("required", []) == []


# ── _tool_create_study_plan ─────────────────────────────────────────────────

class TestCreateStudyPlan:

    def test_creates_plan_and_sets_active(self, store: LearningPlanStore) -> None:
        res = execute_tool("create_study_plan", {
            "goal": "8 周系统学习机器学习",
            "weeks": 8,
            "tasks": _ok_tasks(),
        })
        assert res.status == "ok"
        active = store.get_active()
        assert active is not None
        assert active["goal"] == "8 周系统学习机器学习"
        assert active["weeks"] == 8
        assert len(active["tasks"]) == 3
        # ack 文本含 plan_id 与任务数
        assert f"plan_id={active['id']}" in res.content
        assert "含 3 个任务" in res.content

    def test_replaces_old_active_on_new_create(self, store: LearningPlanStore) -> None:
        execute_tool("create_study_plan", {
            "goal": "g1", "weeks": 2, "tasks": _ok_tasks(),
        })
        pid_old = store.get_active()["id"]
        execute_tool("create_study_plan", {
            "goal": "g2", "weeks": 4, "tasks": _ok_tasks(),
        })
        active = store.get_active()
        assert active["goal"] == "g2"
        assert store.get_plan(pid_old)["is_active"] is False

    def test_empty_goal_returns_error(self, store: LearningPlanStore) -> None:
        res = execute_tool("create_study_plan", {"goal": "", "tasks": _ok_tasks()})
        assert res.status == "error"
        assert "goal" in res.content

    def test_negative_weeks_returns_error(self, store: LearningPlanStore) -> None:
        res = execute_tool("create_study_plan", {
            "goal": "g", "weeks": -1, "tasks": _ok_tasks(),
        })
        assert res.status == "error"
        assert "weeks" in res.content

    def test_empty_tasks_returns_error(self, store: LearningPlanStore) -> None:
        res = execute_tool("create_study_plan", {"goal": "g", "tasks": []})
        assert res.status == "error"

    def test_task_field_invalid_returns_error(self, store: LearningPlanStore) -> None:
        bad_tasks = [{"stage_idx": 1, "order_idx": 1, "title": ""}]
        res = execute_tool("create_study_plan", {"goal": "g", "tasks": bad_tasks})
        assert res.status == "error"
        assert "tasks[0]" in res.content

    def test_task_not_dict_returns_error(self, store: LearningPlanStore) -> None:
        res = execute_tool("create_study_plan", {
            "goal": "g", "tasks": ["a string instead of dict"],
        })
        assert res.status == "error"


# ── _tool_update_study_progress ─────────────────────────────────────────────

class TestUpdateStudyProgress:

    @pytest.fixture
    def loaded(self, store: LearningPlanStore) -> tuple[int, list[int]]:
        execute_tool("create_study_plan", {
            "goal": "g", "weeks": 2, "tasks": _ok_tasks(),
        })
        active = store.get_active()
        return active["id"], [t["id"] for t in active["tasks"]]

    def test_success_returns_progress_and_next(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        res = execute_tool("update_study_progress", {
            "plan_id": pid, "task_id": tids[0], "status": "success",
        })
        assert res.status == "ok"
        assert "1/3" in res.content
        assert "下一个待办" in res.content

    def test_skipped_does_not_count_done(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        execute_tool("update_study_progress", {
            "plan_id": pid, "task_id": tids[0], "status": "skipped",
        })
        res = execute_tool("update_study_progress", {
            "plan_id": pid, "task_id": tids[1], "status": "success",
        })
        # 仅 1 success → 1/3
        assert "1/3" in res.content

    def test_all_success_marks_plan_completed(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        for tid in tids:
            execute_tool("update_study_progress", {
                "plan_id": pid, "task_id": tid, "status": "success",
            })
        plan = store.get_plan(pid)
        assert plan["status"] == "completed"
        assert plan["is_active"] is False

    def test_invalid_plan_id_returns_error(self, store: LearningPlanStore) -> None:
        res = execute_tool("update_study_progress", {
            "plan_id": 0, "task_id": 1, "status": "success",
        })
        assert res.status == "error"

    def test_invalid_task_id_returns_error(self, store: LearningPlanStore) -> None:
        res = execute_tool("update_study_progress", {
            "plan_id": 1, "task_id": -1, "status": "success",
        })
        assert res.status == "error"

    def test_invalid_status_returns_error(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        res = execute_tool("update_study_progress", {
            "plan_id": pid, "task_id": tids[0], "status": "failed",
        })
        assert res.status == "error"
        assert "success" in res.content

    def test_cross_plan_task_id_returns_error(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        # 用一个不存在的 plan_id 试更新已知 task_id
        res = execute_tool("update_study_progress", {
            "plan_id": pid + 99, "task_id": tids[0], "status": "success",
        })
        assert res.status == "error"


# ── _tool_query_study_status ─────────────────────────────────────────────────

class TestQueryStudyStatus:

    def test_no_active_returns_empty(self, store: LearningPlanStore) -> None:
        res = execute_tool("query_study_status", {})
        assert res.status == "empty"
        assert "没有 active" in res.content

    def test_default_returns_active_summary(self, store: LearningPlanStore) -> None:
        execute_tool("create_study_plan", {
            "goal": "g", "weeks": 4, "tasks": _ok_tasks(),
        })
        res = execute_tool("query_study_status", {})
        assert res.status == "ok"
        assert "[active]" in res.content
        # 默认 detail=False 不展开 stage 标题段（仅给"下一个待办"一行）
        assert "### Stage" not in res.content
        assert "下一个待办" in res.content

    def test_detail_includes_full_tasks(self, store: LearningPlanStore) -> None:
        execute_tool("create_study_plan", {
            "goal": "g", "weeks": 4, "tasks": _ok_tasks(),
        })
        res = execute_tool("query_study_status", {"detail": True})
        assert res.status == "ok"
        assert "**Stage 1**" in res.content
        assert "**Stage 2**" in res.content
        assert "T1" in res.content and "T3" in res.content

    def test_specific_plan_id(self, store: LearningPlanStore) -> None:
        execute_tool("create_study_plan", {"goal": "g1", "tasks": _ok_tasks()})
        pid1 = store.get_active()["id"]
        execute_tool("create_study_plan", {"goal": "g2", "tasks": _ok_tasks()})
        res = execute_tool("query_study_status", {"plan_id": pid1, "detail": True})
        assert res.status == "ok"
        assert "g1" in res.content
        # 注意 pid1 已不是 active；不应带 [active] tag
        assert "[active]" not in res.content

    def test_list_all_returns_all_plans(self, store: LearningPlanStore) -> None:
        execute_tool("create_study_plan", {"goal": "g1", "tasks": _ok_tasks()})
        execute_tool("create_study_plan", {"goal": "g2", "tasks": _ok_tasks()})
        res = execute_tool("query_study_status", {"list_all": True})
        assert res.status == "ok"
        assert "g1" in res.content and "g2" in res.content

    def test_list_all_empty(self, store: LearningPlanStore) -> None:
        res = execute_tool("query_study_status", {"list_all": True})
        assert res.status == "empty"

    def test_missing_plan_id_returns_error(self, store: LearningPlanStore) -> None:
        res = execute_tool("query_study_status", {"plan_id": 999})
        assert res.status == "error"


# ── execute_tool 路由 ────────────────────────────────────────────────────────

class TestRouting:

    def test_all_three_tools_route(self, store: LearningPlanStore) -> None:
        # create
        r1 = execute_tool("create_study_plan", {
            "goal": "g", "tasks": _ok_tasks(),
        })
        assert isinstance(r1, ToolResult) and r1.status == "ok"
        # query
        r2 = execute_tool("query_study_status", {})
        assert isinstance(r2, ToolResult) and r2.status == "ok"
        # update
        tid = store.get_active()["tasks"][0]["id"]
        pid = store.get_active()["id"]
        r3 = execute_tool("update_study_progress", {
            "plan_id": pid, "task_id": tid, "status": "success",
        })
        assert isinstance(r3, ToolResult) and r3.status == "ok"
