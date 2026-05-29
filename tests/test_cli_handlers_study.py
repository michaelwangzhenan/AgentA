"""
测试 CLI `/study` 命令组（[`src/cli/handlers.py::handle_study`](../src/cli/handlers.py) G5 / D2）。

覆盖：
    - 无参 / `list` 子命令：空 / 多 plan / active 标记
    - `show`：无 active / active / 指定 plan_id / 不存在
    - `switch`：成功 / 不存在 / 已 abandoned
    - `load`：无参（取 active）/ 指定 id / 无 active / 不存在 / 已 abandoned / session 隔离
    - `abandon`：confirm 流程（yes / no）
    - 错误处理：非整数 plan_id / 负数 / 空参
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cli.handlers import handle_study
from src.memory.learning_plan_store import LearningPlanStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[LearningPlanStore]:
    s = LearningPlanStore(str(tmp_path / "lp.db"))
    yield s
    s.close()


def _make_collector() -> tuple[list[str], "callable"]:
    """返回 (lines, out_fn)；out_fn 把每行写入 lines list 便于 assert。"""
    lines: list[str] = []
    def out(msg: str) -> None:
        lines.append(msg)
    return lines, out


def _seed_plans(store: LearningPlanStore) -> tuple[int, int]:
    """种 2 个 plan（p2 active）+ 给 p2 加 3 task。"""
    p1 = store.create_plan(goal="第一个计划", weeks=2)
    p2 = store.create_plan(goal="第二个计划 ML 面试", weeks=8)
    store.add_tasks(p2, [
        {"stage_idx": 1, "order_idx": 1, "title": "T1"},
        {"stage_idx": 1, "order_idx": 2, "title": "T2"},
        {"stage_idx": 2, "order_idx": 1, "title": "T3"},
    ])
    return p1, p2


# ── /study / /study list ────────────────────────────────────────────────────

class TestStudyList:

    def test_list_empty(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study"], out=out)
        joined = "\n".join(lines)
        assert "暂无学习计划" in joined

    def test_list_shows_active_marker(self, store: LearningPlanStore) -> None:
        _, p2 = _seed_plans(store)
        lines, out = _make_collector()
        handle_study(store, ["/study", "list"], out=out)
        joined = "\n".join(lines)
        assert "学习计划列表（共 2 个）" in joined
        # p2 是 active，应以 ▶ 标记
        active_line = next(
            ln for ln in lines if f"[{p2:>3d}]" in ln
        )
        assert "▶" in active_line

    def test_list_default_excludes_abandoned(self, store: LearningPlanStore) -> None:
        p1, p2 = _seed_plans(store)
        store.abandon_plan(p1)
        lines, out = _make_collector()
        handle_study(store, ["/study"], out=out)
        joined = "\n".join(lines)
        assert "共 1 个" in joined


# ── /study show ─────────────────────────────────────────────────────────────

class TestStudyShow:

    def test_show_no_active(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "show"], out=out)
        assert any("没有 active" in ln for ln in lines)

    def test_show_active_includes_tasks(self, store: LearningPlanStore) -> None:
        _seed_plans(store)
        lines, out = _make_collector()
        handle_study(store, ["/study", "show"], out=out)
        joined = "\n".join(lines)
        assert "Stage 1" in joined and "Stage 2" in joined
        assert "T1" in joined and "T3" in joined

    def test_show_specific_plan_id(self, store: LearningPlanStore) -> None:
        p1, _ = _seed_plans(store)
        lines, out = _make_collector()
        handle_study(store, ["/study", f"show {p1}"], out=out)
        joined = "\n".join(lines)
        assert f"plan_id={p1}" in joined
        # p1 无 task → 应显示「暂无任务」
        assert "暂无任务" in joined

    def test_show_unknown_plan_id(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "show 999"], out=out)
        assert any("999 不存在" in ln for ln in lines)

    def test_show_invalid_plan_id(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "show abc"], out=out)
        assert any("无效 plan_id" in ln for ln in lines)


# ── /study switch ───────────────────────────────────────────────────────────

class TestStudySwitch:

    def test_switch_success(self, store: LearningPlanStore) -> None:
        p1, p2 = _seed_plans(store)
        # 当前 active=p2，切回 p1
        lines, out = _make_collector()
        handle_study(store, ["/study", f"switch {p1}"], out=out)
        joined = "\n".join(lines)
        assert "✅" in joined and f"plan_id={p1}" in joined
        assert store.get_active()["id"] == p1

    def test_switch_unknown_returns_error(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "switch 999"], out=out)
        assert any("切换失败" in ln for ln in lines)

    def test_switch_abandoned_returns_error(self, store: LearningPlanStore) -> None:
        p1, _ = _seed_plans(store)
        store.abandon_plan(p1)
        lines, out = _make_collector()
        handle_study(store, ["/study", f"switch {p1}"], out=out)
        assert any("切换失败" in ln for ln in lines)

    def test_switch_missing_id(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "switch"], out=out)
        assert any("请提供 plan_id" in ln for ln in lines)


# ── /study load ─────────────────────────────────────────────────────────────

class TestStudyLoad:
    """`/study load [plan_id]`：手动激活 plan 注入到当前 session prompt（路线 C）。"""

    def test_load_without_id_uses_active(self, store: LearningPlanStore) -> None:
        _, p2 = _seed_plans(store)
        lines, out = _make_collector()
        handle_study(store, ["/study", "load"], session_id="sess-A", out=out)
        joined = "\n".join(lines)
        assert "已加载" in joined and f"plan_id={p2}" in joined
        assert store.get_loaded("sess-A") == p2

    def test_load_without_id_no_active_prints_hint(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "load"], session_id="sess-A", out=out)
        assert any("没有 active" in ln for ln in lines)
        assert store.get_loaded("sess-A") is None

    def test_load_with_id_succeeds(self, store: LearningPlanStore) -> None:
        p1, _ = _seed_plans(store)
        lines, out = _make_collector()
        handle_study(store, ["/study", f"load {p1}"], session_id="sess-A", out=out)
        joined = "\n".join(lines)
        assert "已加载" in joined and f"plan_id={p1}" in joined
        assert store.get_loaded("sess-A") == p1

    def test_load_unknown_returns_error(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "load 999"], session_id="sess-A", out=out)
        assert any("加载失败" in ln for ln in lines)
        assert store.get_loaded("sess-A") is None

    def test_load_abandoned_returns_error(self, store: LearningPlanStore) -> None:
        p1, _ = _seed_plans(store)
        store.abandon_plan(p1)
        lines, out = _make_collector()
        handle_study(store, ["/study", f"load {p1}"], session_id="sess-A", out=out)
        assert any("加载失败" in ln for ln in lines)
        assert store.get_loaded("sess-A") is None

    def test_load_invalid_id_prints_hint(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "load abc"], session_id="sess-A", out=out)
        assert any("无效 plan_id" in ln for ln in lines)

    def test_load_sessions_isolated(self, store: LearningPlanStore) -> None:
        """A session load → B session 仍未 load。"""
        p1, _ = _seed_plans(store)
        lines, out = _make_collector()
        handle_study(store, ["/study", f"load {p1}"], session_id="sess-A", out=out)
        assert store.get_loaded("sess-A") == p1
        assert store.get_loaded("sess-B") is None

    def test_load_replaces_previous(self, store: LearningPlanStore) -> None:
        """同 session 多次 load 替换上次的目标 plan。"""
        p1, p2 = _seed_plans(store)
        _, out = _make_collector()
        handle_study(store, ["/study", f"load {p1}"], session_id="sess-A", out=out)
        handle_study(store, ["/study", f"load {p2}"], session_id="sess-A", out=out)
        assert store.get_loaded("sess-A") == p2


# ── /study abandon ──────────────────────────────────────────────────────────

class TestStudyAbandon:

    def test_abandon_yes_confirms(self, store: LearningPlanStore) -> None:
        _, p2 = _seed_plans(store)
        lines, out = _make_collector()
        with patch("builtins.input", return_value="yes"):
            handle_study(store, ["/study", f"abandon {p2}"], out=out)
        joined = "\n".join(lines)
        assert "abandoned" in joined
        assert store.get_plan(p2)["status"] == "abandoned"

    def test_abandon_no_cancels(self, store: LearningPlanStore) -> None:
        _, p2 = _seed_plans(store)
        lines, out = _make_collector()
        with patch("builtins.input", return_value="no"):
            handle_study(store, ["/study", f"abandon {p2}"], out=out)
        assert any("已取消" in ln for ln in lines)
        assert store.get_plan(p2)["status"] == "active"

    def test_abandon_unknown(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "abandon 999"], out=out)
        assert any("999 不存在" in ln for ln in lines)

    def test_abandon_already_abandoned(self, store: LearningPlanStore) -> None:
        p1, _ = _seed_plans(store)
        store.abandon_plan(p1)
        lines, out = _make_collector()
        handle_study(store, ["/study", f"abandon {p1}"], out=out)
        assert any("已是 abandoned" in ln for ln in lines)


# ── 未知子命令 / 错误处理 ────────────────────────────────────────────────────

class TestUnknownSubCmd:

    def test_unknown_sub_cmd_prints_usage(self, store: LearningPlanStore) -> None:
        lines, out = _make_collector()
        handle_study(store, ["/study", "foobar"], out=out)
        assert any("未知子命令" in ln for ln in lines)
