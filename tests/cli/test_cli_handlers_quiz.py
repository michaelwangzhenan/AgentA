"""
测试 CLI `/quiz` 命令组（[`src/cli/handlers.py::handle_quiz`](../src/cli/handlers.py) G5 + D2）。

覆盖：
    - 无参 / `list` 子命令：空 / 多 quiz / plan_id 过滤
    - `show`：存在 / 不存在 / 含批改细节
    - `del`：confirm 流程（yes / no）+ 不存在
    - 错误处理：非整数 quiz_set_id / 负数 / 空参
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cli.handlers import handle_quiz
from src.stores.quiz_store import QuizStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[QuizStore]:
    s = QuizStore(str(tmp_path / "quiz.db"))
    yield s
    s.close()


def _make_collector() -> tuple[list[str], "callable"]:
    lines: list[str] = []
    def out(msg: str) -> None:
        lines.append(msg)
    return lines, out


def _seed_quizzes(store: QuizStore) -> tuple[int, int]:
    """种 2 个 quiz：q1 不绑 plan / q2 绑 plan_id=5。"""
    q1 = store.create_quiz_set(topic="RAG 检索基础", num_questions=3)
    store.add_questions(q1, [
        {"order_idx": 1, "q_type": "mcq_single", "stem": "RAG 全称",
         "options": ["A", "B", "C", "D"], "correct_answer": "B"},
        {"order_idx": 2, "q_type": "mcq_multi", "stem": "检索组件",
         "options": ["x", "y", "z"], "correct_answer": "AB"},
        {"order_idx": 3, "q_type": "short_answer", "stem": "解释 RRF",
         "correct_answer": "倒数排名融合"},
    ])
    q2 = store.create_quiz_set(topic="Python 基础", num_questions=1, plan_id=5)
    return q1, q2


# ── Phase 2.5 Harness：⚠️ 渲染 ──────────────────────────────────────────────

class TestHarnessFlagRender:

    def test_unflagged_quiz_no_warning(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", f"show {q1}"], out=out)
        joined = "\n".join(lines)
        assert "⚠️" not in joined
        assert "自检" not in joined

    def test_flagged_question_renders_warning(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        # 拉第 3 题（short_answer）的 question_id 后 mark
        q_id = store.get_quiz_with_questions(q1)["questions"][2]["id"]
        store.mark_question_harness_flagged(q_id)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", f"show {q1}"], out=out)
        joined = "\n".join(lines)
        assert "⚠️" in joined
        assert "复核" in joined

    def test_flagged_only_target_question(self, store: QuizStore) -> None:
        """3 题中只 mark 第 3 题 → 输出只在第 3 题位置出现 ⚠️。"""
        q1, _ = _seed_quizzes(store)
        q_id = store.get_quiz_with_questions(q1)["questions"][2]["id"]
        store.mark_question_harness_flagged(q_id)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", f"show {q1}"], out=out)
        # 整体只有 1 个 ⚠️
        warning_count = sum(1 for ln in lines if "⚠️" in ln)
        assert warning_count == 1
        # ⚠️ 出现在标题行（含"第 3 题"）
        warn_lines = [ln for ln in lines if "⚠️" in ln]
        assert "第 3 题" in warn_lines[0]


# ── /quiz / /quiz list ──────────────────────────────────────────────────────

class TestQuizList:

    def test_list_empty(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz"], out=out)
        joined = "\n".join(lines)
        assert "暂无 quiz 历史" in joined

    def test_list_shows_topic_and_status(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "list"], out=out)
        joined = "\n".join(lines)
        assert "Quiz 列表" in joined
        assert "RAG 检索基础" in joined
        assert "created" in joined
        assert f"[{q1:>3d}]" in joined

    def test_list_default_excludes_archived(self, store: QuizStore) -> None:
        q1, q2 = _seed_quizzes(store)
        store.archive_quiz_set(q1)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz"], out=out)
        joined = "\n".join(lines)
        assert "共 1 个" in joined
        # q1 不出现
        assert f"[{q1:>3d}]" not in joined

    def test_list_filter_by_plan(self, store: QuizStore) -> None:
        _, q2 = _seed_quizzes(store)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "list plan 5"], out=out)
        joined = "\n".join(lines)
        assert "Python 基础" in joined
        assert "RAG 检索基础" not in joined

    def test_list_filter_plan_empty(self, store: QuizStore) -> None:
        _seed_quizzes(store)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "list plan 999"], out=out)
        joined = "\n".join(lines)
        assert "plan_id=999 暂无关联 quiz" in joined

    def test_list_filter_plan_invalid_id(self, store: QuizStore) -> None:
        _seed_quizzes(store)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "list plan abc"], out=out)
        joined = "\n".join(lines)
        assert "无效" in joined


# ── /quiz show ──────────────────────────────────────────────────────────────

class TestQuizShow:

    def test_show_empty_arg(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "show"], out=out)
        joined = "\n".join(lines)
        assert "请提供" in joined

    def test_show_existing_shows_questions(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", f"show {q1}"], out=out)
        joined = "\n".join(lines)
        assert f"quiz_set_id={q1}" in joined
        assert "RAG 全称" in joined
        assert "RRF" in joined
        # 含标答
        assert "倒数排名融合" in joined

    def test_show_displays_options_letters(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", f"show {q1}"], out=out)
        joined = "\n".join(lines)
        # MCQ 选项 A. B. C. D.
        assert "A." in joined and "B." in joined

    def test_show_after_grading_shows_score(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        quiz = store.get_quiz_with_questions(q1)
        qids = [q["id"] for q in quiz["questions"]]
        store.update_grading(q1, [
            {"question_id": qids[0], "user_answer": "B", "score": 1.0, "feedback": "正确"},
            {"question_id": qids[1], "user_answer": "AB", "score": 1.0, "feedback": "正确"},
            {"question_id": qids[2], "user_answer": "RRF 是融合", "score": 0.5, "feedback": "缺关键词"},
        ], total_score=83.3)
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", f"show {q1}"], out=out)
        joined = "\n".join(lines)
        assert "83.3/100" in joined
        assert "你的答案" in joined
        assert "缺关键词" in joined

    def test_show_unknown(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "show 999"], out=out)
        joined = "\n".join(lines)
        assert "不存在" in joined

    def test_show_invalid_id(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "show abc"], out=out)
        joined = "\n".join(lines)
        assert "无效" in joined

    def test_show_zero_id(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "show 0"], out=out)
        joined = "\n".join(lines)
        assert "≥ 1" in joined


# ── /quiz del ───────────────────────────────────────────────────────────────

class TestQuizDel:

    def test_del_empty_arg(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "del"], out=out)
        joined = "\n".join(lines)
        assert "请提供" in joined

    def test_del_unknown(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "del 999"], out=out)
        joined = "\n".join(lines)
        assert "不存在" in joined

    def test_del_confirm_yes(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        lines, out = _make_collector()
        with patch("builtins.input", return_value="yes"):
            handle_quiz(store, ["/quiz", f"del {q1}"], out=out)
        joined = "\n".join(lines)
        assert "已删除" in joined
        assert store.get_quiz_set(q1) is None

    def test_del_confirm_no(self, store: QuizStore) -> None:
        q1, _ = _seed_quizzes(store)
        lines, out = _make_collector()
        with patch("builtins.input", return_value="no"):
            handle_quiz(store, ["/quiz", f"del {q1}"], out=out)
        joined = "\n".join(lines)
        assert "已取消" in joined
        # 数据仍在
        assert store.get_quiz_set(q1) is not None


# ── unknown sub-command ─────────────────────────────────────────────────────

class TestUnknown:

    def test_unknown_subcmd_shows_usage(self, store: QuizStore) -> None:
        lines, out = _make_collector()
        handle_quiz(store, ["/quiz", "foo"], out=out)
        joined = "\n".join(lines)
        assert "未知子命令" in joined
        assert "/quiz list" in joined
