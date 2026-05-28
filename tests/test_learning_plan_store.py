"""
测试 [LearningPlanStore](../src/memory/learning_plan_store.py)（Phase 2.2 G1 / D1 / D2 / D9）。

覆盖：
    - 表结构幂等初始化、独立 db 文件
    - create_plan / add_tasks / get_plan_with_tasks 基本 CRUD
    - is_active 互斥（D9）：create_plan(set_active=True) 自动 archive 旧 active；switch_active 互斥
    - update_task_status 校验：非法 status / 跨 plan task_id
    - abandon_plan / complete_plan：状态变更 + is_active=0
    - list_plans 排序（active 优先 + 创建时间倒序）+ include_abandoned 过滤
    - render_active_for_prompt：active / 无 active / max_chars 截断 / stage 分组渲染
    - delete_plan：级联删除 learning_tasks（ON DELETE CASCADE）
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.memory.learning_plan_store import LearningPlanStore


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> Iterator[LearningPlanStore]:
    db = LearningPlanStore(str(tmp_path / "learning.db"))
    yield db
    db.close()


def _sample_tasks() -> list[dict[str, object]]:
    """两阶段、5 任务，覆盖跨 stage / 跨 order 排序。"""
    return [
        {"stage_idx": 1, "order_idx": 1, "title": "完成 Pandas 10min 教程"},
        {"stage_idx": 1, "order_idx": 2, "title": "做 Kaggle Titanic"},
        {"stage_idx": 1, "order_idx": 3, "title": "写一篇笔记"},
        {"stage_idx": 2, "order_idx": 1, "title": "学习 sklearn 基础"},
        {"stage_idx": 2, "order_idx": 2, "title": "做 2 个分类项目"},
    ]


# ── 基本 CRUD ────────────────────────────────────────────────────────────────

class TestBasicCRUD:

    def test_create_plan_and_get(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="8 周准备 ML 面试", weeks=8)
        plan = store.get_plan(pid)
        assert plan is not None
        assert plan["goal"] == "8 周准备 ML 面试"
        assert plan["weeks"] == 8
        assert plan["status"] == "active"
        assert plan["is_active"] is True

    def test_create_plan_rejects_empty_goal(self, store: LearningPlanStore) -> None:
        with pytest.raises(ValueError, match="goal 不能为空"):
            store.create_plan(goal="")

    def test_create_plan_rejects_negative_weeks(self, store: LearningPlanStore) -> None:
        with pytest.raises(ValueError, match="weeks 必须 ≥ 0"):
            store.create_plan(goal="x", weeks=-1)

    def test_add_tasks_basic(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x", weeks=0)
        n = store.add_tasks(pid, _sample_tasks())
        assert n == 5
        plan = store.get_plan_with_tasks(pid)
        assert plan is not None
        assert len(plan["tasks"]) == 5
        # 按 stage_idx, order_idx 升序
        order = [(t["stage_idx"], t["order_idx"]) for t in plan["tasks"]]
        assert order == sorted(order)

    def test_add_tasks_skips_illegal(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x")
        bad = [
            {"stage_idx": 0, "order_idx": 1, "title": "stage 0 非法"},
            {"stage_idx": 1, "order_idx": 0, "title": "order 0 非法"},
            {"stage_idx": 1, "order_idx": 1, "title": "   "},  # 空 title
            {"stage_idx": 1, "order_idx": 2, "title": "ok"},
        ]
        assert store.add_tasks(pid, bad) == 1

    def test_add_tasks_rejects_unknown_plan(self, store: LearningPlanStore) -> None:
        with pytest.raises(ValueError, match="不存在"):
            store.add_tasks(999, _sample_tasks())

    def test_get_plan_returns_none_for_missing(self, store: LearningPlanStore) -> None:
        assert store.get_plan(999) is None
        assert store.get_plan_with_tasks(999) is None


# ── is_active 互斥（D9） ──────────────────────────────────────────────────────

class TestActiveMutex:

    def test_create_plan_archives_old_active(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="plan1")
        p2 = store.create_plan(goal="plan2")
        active = store.get_active()
        assert active is not None
        assert active["id"] == p2
        # 旧 plan 不再 active
        assert store.get_plan(p1)["is_active"] is False

    def test_create_plan_set_active_false_keeps_old(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="plan1")
        p2 = store.create_plan(goal="plan2", set_active=False)
        active = store.get_active()
        assert active is not None
        assert active["id"] == p1
        assert store.get_plan(p2)["is_active"] is False

    def test_switch_active_mutex(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="plan1")
        p2 = store.create_plan(goal="plan2")
        # 当前 active = p2，切回 p1
        assert store.switch_active(p1) is True
        assert store.get_active()["id"] == p1
        assert store.get_plan(p2)["is_active"] is False

    def test_switch_active_missing_returns_false(self, store: LearningPlanStore) -> None:
        assert store.switch_active(999) is False

    def test_switch_active_abandoned_returns_false(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="x")
        store.abandon_plan(p1)
        assert store.switch_active(p1) is False

    def test_get_active_none_when_all_abandoned(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="x")
        store.abandon_plan(p1)
        assert store.get_active() is None


# ── update_task_status ───────────────────────────────────────────────────────

class TestUpdateTaskStatus:

    @pytest.fixture
    def loaded(self, store: LearningPlanStore) -> tuple[int, list[int]]:
        pid = store.create_plan(goal="x")
        store.add_tasks(pid, _sample_tasks())
        tasks = store.get_plan_with_tasks(pid)["tasks"]
        return pid, [t["id"] for t in tasks]

    def test_success_marks_completed_at(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        assert store.update_task_status(pid, tids[0], "success", note="learned a lot") is True
        plan = store.get_plan_with_tasks(pid)
        t = next(t for t in plan["tasks"] if t["id"] == tids[0])
        assert t["status"] == "success"
        assert t["note"] == "learned a lot"
        assert t["completed_at"]  # 非空

    def test_skipped_marks_completed_at(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        store.update_task_status(pid, tids[1], "skipped")
        t = next(t for t in store.get_plan_with_tasks(pid)["tasks"] if t["id"] == tids[1])
        assert t["status"] == "skipped"
        assert t["completed_at"]

    def test_pending_clears_completed_at(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        store.update_task_status(pid, tids[0], "success")
        store.update_task_status(pid, tids[0], "pending")
        t = next(t for t in store.get_plan_with_tasks(pid)["tasks"] if t["id"] == tids[0])
        assert t["status"] == "pending"
        assert t["completed_at"] == ""

    def test_rejects_invalid_status(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        assert store.update_task_status(pid, tids[0], "done") is False

    def test_rejects_cross_plan_task_id(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid_other = store.create_plan(goal="other", set_active=False)
        pid, tids = loaded
        # 用 other plan id 试更新 main plan 的 task
        assert store.update_task_status(pid_other, tids[0], "success") is False

    def test_truncates_long_note(
        self, store: LearningPlanStore, loaded: tuple[int, list[int]],
    ) -> None:
        pid, tids = loaded
        long_note = "x" * 500
        store.update_task_status(pid, tids[0], "success", note=long_note)
        t = next(t for t in store.get_plan_with_tasks(pid)["tasks"] if t["id"] == tids[0])
        assert len(t["note"]) == 200


# ── abandon / complete / delete ──────────────────────────────────────────────

class TestLifecycle:

    def test_abandon_plan(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x")
        assert store.abandon_plan(pid) is True
        plan = store.get_plan(pid)
        assert plan["status"] == "abandoned"
        assert plan["is_active"] is False

    def test_abandon_unknown(self, store: LearningPlanStore) -> None:
        assert store.abandon_plan(999) is False

    def test_complete_plan(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x")
        assert store.complete_plan(pid) is True
        plan = store.get_plan(pid)
        assert plan["status"] == "completed"
        assert plan["is_active"] is False

    def test_delete_plan_cascades_tasks(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x")
        store.add_tasks(pid, _sample_tasks())
        assert store.delete_plan(pid) is True
        # 重新建一个 plan，验证 task 表已被 cascade
        pid2 = store.create_plan(goal="y")
        plan2 = store.get_plan_with_tasks(pid2)
        assert plan2["tasks"] == []

    def test_delete_unknown_returns_false(self, store: LearningPlanStore) -> None:
        assert store.delete_plan(999) is False


# ── list_plans ───────────────────────────────────────────────────────────────

class TestListPlans:

    def test_active_first_then_created_desc(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="p1")
        p2 = store.create_plan(goal="p2")  # 现在 p2 active
        p3 = store.create_plan(goal="p3", set_active=False)
        plans = store.list_plans()
        ids = [p["id"] for p in plans]
        # active=p2 应排第一；剩余两个按创建时间倒序 → p3, p1
        assert ids[0] == p2
        assert ids[1:] == [p3, p1]

    def test_filters_abandoned_by_default(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="keep")
        p2 = store.create_plan(goal="drop", set_active=False)
        store.abandon_plan(p2)
        plans = store.list_plans()
        assert [p["id"] for p in plans] == [p1]

    def test_include_abandoned(self, store: LearningPlanStore) -> None:
        p1 = store.create_plan(goal="keep")
        p2 = store.create_plan(goal="drop", set_active=False)
        store.abandon_plan(p2)
        plans = store.list_plans(include_abandoned=True)
        assert {p["id"] for p in plans} == {p1, p2}

    def test_task_count_and_done_count(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x")
        store.add_tasks(pid, _sample_tasks())
        tasks = store.get_plan_with_tasks(pid)["tasks"]
        # 标 2 个 success / 1 个 skipped
        store.update_task_status(pid, tasks[0]["id"], "success")
        store.update_task_status(pid, tasks[1]["id"], "success")
        store.update_task_status(pid, tasks[2]["id"], "skipped")
        plans = store.list_plans()
        p = next(p for p in plans if p["id"] == pid)
        assert p["task_count"] == 5
        assert p["done_count"] == 2  # 仅 success 计入 done


# ── render_active_for_prompt ────────────────────────────────────────────────

class TestRenderActiveForPrompt:

    def test_empty_when_no_active(self, store: LearningPlanStore) -> None:
        assert store.render_active_for_prompt() == ""

    def test_basic_render_has_id_goal_progress(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="8 周准备 ML 面试", weeks=8)
        store.add_tasks(pid, _sample_tasks())
        out = store.render_active_for_prompt()
        assert f"plan_id={pid}" in out
        assert "8 周准备 ML 面试" in out
        assert "8 周" in out
        assert "0/5 完成" in out

    def test_stage_grouping(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x")
        store.add_tasks(pid, _sample_tasks())
        out = store.render_active_for_prompt()
        assert "Stage 1" in out
        assert "Stage 2" in out

    def test_status_icons(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x")
        store.add_tasks(pid, _sample_tasks())
        tasks = store.get_plan_with_tasks(pid)["tasks"]
        store.update_task_status(pid, tasks[0]["id"], "success")
        store.update_task_status(pid, tasks[1]["id"], "skipped")
        out = store.render_active_for_prompt()
        # 至少含三种 icon
        assert "✓" in out and "⏭" in out and "☐" in out

    def test_truncates_when_exceeds_max_chars(self, store: LearningPlanStore) -> None:
        pid = store.create_plan(goal="x" * 100)
        # 制造一个很长 task 列表
        tasks = [
            {"stage_idx": 1, "order_idx": i, "title": f"task {i} " * 20}
            for i in range(1, 30)
        ]
        store.add_tasks(pid, tasks)
        out = store.render_active_for_prompt(max_chars=200)
        assert len(out) <= 200
        assert "截断" in out


# ── 资源管理 ──────────────────────────────────────────────────────────────────

def test_context_manager(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ctx.db")
    with LearningPlanStore(db_path) as store:
        pid = store.create_plan(goal="x")
        assert pid > 0
    # 退出后能重新打开（连接已正常关闭）
    store2 = LearningPlanStore(db_path)
    assert store2.get_plan(pid) is not None
    store2.close()
