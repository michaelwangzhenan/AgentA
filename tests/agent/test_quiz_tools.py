"""
测试 Phase 2.3 三业务 tool（[`src/agent/tools.py`](../src/agent/tools.py) G2 + D5 + D11 + D13）。

覆盖：
    - JSON Schema 完整性：`_QUIZ_TOOLS` 三 tool 名 / required 字段
    - `get_tools()` 返回包含三新 tool（无 skill 也常驻）
    - `_tool_create_quiz`：入参校验（questions / topic / plan_id / stage_idx）+ 落库 + topic 派生
    - `_tool_grade_quiz`：MCQ 字符串比对 + 简答 LLM-judge（mock）+ 总分计算 + 错题清单
    - `_tool_query_quiz_history`：单查 / plan_id 过滤 / 全局列表 三路径
    - 三 tool 输出都要带 question_id（下游 grade_quiz / add_to_srs 收的是主键，不是题号）
    - `execute_tool` 路由覆盖三 tool

测试隔离：每个测试用 tmp_path 注入独立 QuizStore 替换全局共享。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.tools import ToolResult, execute_tool, get_tools
from src.stores import quiz_store as quiz_store_module
from src.stores.quiz_store import QuizStore


@pytest.fixture(autouse=True)
def _disable_quiz_critic(monkeypatch: pytest.MonkeyPatch) -> None:
    """全文件默认关掉 grade_quiz 的 critic 自检，避免现有 UT 真调 LLM。

    Phase 2.5 自检的集成测试见 [`test_critic_integration.py`](test_critic_integration.py)
    （那里显式打开 + mock manager）。本文件聚焦 Phase 2.3 三业务 tool 自身，无须涉及自检。
    """
    import src.config as _cfg
    monkeypatch.setattr(_cfg, "CRITIC_QUIZ_ENABLED", False)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[QuizStore]:
    """注入独立 store 替换全局共享 + 测试结束清理。"""
    s = QuizStore(str(tmp_path / "quiz.db"))
    quiz_store_module.reset_shared_store_for_testing(s)
    yield s
    quiz_store_module.reset_shared_store_for_testing(None)
    s.close()


def _ok_questions() -> list[dict[str, object]]:
    return [
        {
            "order_idx": 1, "q_type": "mcq_single", "stem": "1+1=?",
            "options": ["1", "2", "3", "4"], "correct_answer": "B",
        },
        {
            "order_idx": 2, "q_type": "mcq_multi", "stem": "选择质数",
            "options": ["2", "4", "6", "7"], "correct_answer": "AD",
        },
        {
            "order_idx": 3, "q_type": "short_answer", "stem": "解释 RAG",
            "correct_answer": "Retrieval-Augmented Generation",
        },
    ]


# ── Schema 完整性 ────────────────────────────────────────────────────────────

class TestSchema:

    def test_get_tools_always_includes_three_quiz_tools(self) -> None:
        names = {t["function"]["name"] for t in get_tools()}
        assert {"create_quiz", "grade_quiz", "query_quiz_history"} <= names

    def test_create_quiz_required_questions(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "create_quiz")
        req = set(t["function"]["parameters"]["required"])
        assert req == {"questions"}

    def test_create_quiz_question_item_required(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "create_quiz")
        item_required = set(
            t["function"]["parameters"]["properties"]["questions"]["items"]["required"]
        )
        assert {"order_idx", "q_type", "stem", "correct_answer"} <= item_required

    def test_grade_quiz_required(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "grade_quiz")
        req = set(t["function"]["parameters"]["required"])
        assert req == {"quiz_set_id", "user_answers"}

    def test_query_quiz_history_no_required(self) -> None:
        t = next(t for t in get_tools() if t["function"]["name"] == "query_quiz_history")
        assert t["function"]["parameters"].get("required", []) == []


# ── _tool_create_quiz ───────────────────────────────────────────────────────

class TestCreateQuiz:

    def test_creates_with_topic_only(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {
            "topic": "RAG 检索基础",
            "questions": _ok_questions(),
        })
        assert res.status == "ok"
        quizzes = store.list_quiz_sets()
        assert len(quizzes) == 1
        assert quizzes[0]["topic"] == "RAG 检索基础"
        assert quizzes[0]["num_questions"] == 3

    def test_persists_question_options(self, store: QuizStore) -> None:
        execute_tool("create_quiz", {
            "topic": "x", "questions": _ok_questions(),
        })
        qid = store.list_quiz_sets()[0]["id"]
        quiz = store.get_quiz_with_questions(qid)
        opts = [q["options"] for q in quiz["questions"] if q["q_type"] != "short_answer"]
        # 两个 MCQ 都有 options
        assert all(len(o) >= 2 for o in opts)
        # 简答题 options 为空
        short = next(q for q in quiz["questions"] if q["q_type"] == "short_answer")
        assert short["options"] == []

    def test_empty_questions_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {"topic": "x", "questions": []})
        assert res.status == "error"

    def test_questions_not_list_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {"topic": "x", "questions": "not a list"})
        assert res.status == "error"

    def test_question_item_invalid_q_type(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {
            "topic": "x", "questions": [
                {"order_idx": 1, "q_type": "essay", "stem": "x", "correct_answer": "y"},
            ],
        })
        assert res.status == "error"
        assert "q_type" in res.content

    def test_mcq_without_options_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {
            "topic": "x", "questions": [
                {"order_idx": 1, "q_type": "mcq_single", "stem": "x", "correct_answer": "A"},
            ],
        })
        assert res.status == "error"
        assert "options" in res.content

    def test_neither_topic_nor_plan_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {"questions": _ok_questions()})
        assert res.status == "error"
        assert "topic" in res.content or "plan_id" in res.content

    def test_stage_idx_without_plan_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {
            "topic": "x", "stage_idx": 2, "questions": _ok_questions(),
        })
        assert res.status == "error"
        assert "plan_id" in res.content

    def test_topic_derived_from_plan(self, store: QuizStore) -> None:
        """topic 缺 + plan_id 给 → 用 plan goal 派生 topic。"""
        # mock LearningPlanStore.get_plan
        from src.stores import learning_plan_store as lp_mod
        mock_lp = MagicMock()
        mock_lp.get_plan.return_value = {"id": 7, "goal": "8 周系统学习机器学习"}
        lp_mod.reset_shared_store_for_testing(mock_lp)
        try:
            res = execute_tool("create_quiz", {
                "plan_id": 7, "stage_idx": 2, "questions": _ok_questions(),
            })
            assert res.status == "ok"
            quizzes = store.list_quiz_sets()
            assert quizzes[0]["topic"].startswith("8 周系统学习机器学习")
            assert "Stage 2" in quizzes[0]["topic"]
        finally:
            lp_mod.reset_shared_store_for_testing(None)

    def test_topic_missing_plan_not_exist_returns_error(self, store: QuizStore) -> None:
        from src.stores import learning_plan_store as lp_mod
        mock_lp = MagicMock()
        mock_lp.get_plan.return_value = None
        lp_mod.reset_shared_store_for_testing(mock_lp)
        try:
            res = execute_tool("create_quiz", {
                "plan_id": 999, "questions": _ok_questions(),
            })
            assert res.status == "error"
            assert "不存在" in res.content
        finally:
            lp_mod.reset_shared_store_for_testing(None)


# ── _tool_grade_quiz ────────────────────────────────────────────────────────

class TestGradeQuiz:

    @pytest.fixture
    def loaded(self, store: QuizStore) -> tuple[int, list[int]]:
        execute_tool("create_quiz", {"topic": "x", "questions": _ok_questions()})
        quiz = store.get_quiz_with_questions(store.list_quiz_sets()[0]["id"])
        return quiz["id"], [q["id"] for q in quiz["questions"]]

    def test_all_correct(self, store: QuizStore, loaded: tuple[int, list[int]]) -> None:
        qid, qids = loaded
        # 用 patch 让简答 LLM-judge 返回满分
        with patch("src.agent.tools._grade_one_short_answer",
                   return_value=(1.0, "完全正确")):
            res = execute_tool("grade_quiz", {
                "quiz_set_id": qid,
                "user_answers": {
                    str(qids[0]): "B",
                    str(qids[1]): "AD",
                    str(qids[2]): "RAG",
                },
            })
        assert res.status == "ok"
        assert "100.0/100" in res.content
        quiz = store.get_quiz_with_questions(qid)
        assert quiz["status"] == "graded"
        assert quiz["total_score"] == 100.0

    def test_mixed_mcq_correctness(self, store: QuizStore, loaded: tuple[int, list[int]]) -> None:
        qid, qids = loaded
        with patch("src.agent.tools._grade_one_short_answer",
                   return_value=(0.0, "未作答")):
            res = execute_tool("grade_quiz", {
                "quiz_set_id": qid,
                "user_answers": {
                    str(qids[0]): "B",   # 正确
                    str(qids[1]): "AC",  # 错（应 AD）
                    str(qids[2]): "",    # 未作答
                },
            })
        assert res.status == "ok"
        # 1 题对 / 3 题 → 100/3 ≈ 33.3
        assert "33.3" in res.content
        assert "错题" in res.content or "薄弱点" in res.content

    def test_mcq_normalization(self, store: QuizStore, loaded: tuple[int, list[int]]) -> None:
        """用户写 'ad' / 'a,d' / 'DA' 都应等价于 'AD'。"""
        qid, qids = loaded
        with patch("src.agent.tools._grade_one_short_answer",
                   return_value=(1.0, "ok")):
            for case in ["ad", "a,d", "DA", " A D "]:
                res = execute_tool("grade_quiz", {
                    "quiz_set_id": qid,
                    "user_answers": {
                        str(qids[0]): "B",
                        str(qids[1]): case,
                        str(qids[2]): "x",
                    },
                })
                assert res.status == "ok"
                assert "100.0" in res.content, f"case {case!r} 应满分"

    def test_invalid_quiz_set_id_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("grade_quiz", {"quiz_set_id": 0, "user_answers": {}})
        assert res.status == "error"

    def test_unknown_quiz_set_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("grade_quiz", {"quiz_set_id": 999, "user_answers": {}})
        assert res.status == "error"
        assert "不存在" in res.content

    def test_user_answers_not_dict_returns_error(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, _ = loaded
        res = execute_tool("grade_quiz", {
            "quiz_set_id": qid, "user_answers": "not a dict",
        })
        assert res.status == "error"

    def test_archived_quiz_rejects_grading(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, qids = loaded
        store.archive_quiz_set(qid)
        res = execute_tool("grade_quiz", {
            "quiz_set_id": qid, "user_answers": {str(qids[0]): "B"},
        })
        assert res.status == "error"
        assert "archived" in res.content


# ── _tool_query_quiz_history ────────────────────────────────────────────────

class TestQueryQuizHistory:

    def test_empty_returns_empty(self, store: QuizStore) -> None:
        res = execute_tool("query_quiz_history", {})
        assert res.status == "empty"

    def test_global_list_basic(self, store: QuizStore) -> None:
        execute_tool("create_quiz", {"topic": "t1", "questions": _ok_questions()})
        execute_tool("create_quiz", {"topic": "t2", "questions": _ok_questions()})
        res = execute_tool("query_quiz_history", {})
        assert res.status == "ok"
        assert "t1" in res.content and "t2" in res.content

    def test_plan_id_filter(self, store: QuizStore) -> None:
        execute_tool("create_quiz", {"topic": "t1", "plan_id": 5, "questions": _ok_questions()})
        execute_tool("create_quiz", {"topic": "t2", "plan_id": 6, "questions": _ok_questions()})
        res = execute_tool("query_quiz_history", {"plan_id": 5})
        assert res.status == "ok"
        assert "t1" in res.content and "t2" not in res.content

    def test_plan_id_no_match_returns_empty(self, store: QuizStore) -> None:
        res = execute_tool("query_quiz_history", {"plan_id": 999})
        assert res.status == "empty"

    def test_quiz_set_id_returns_detail(self, store: QuizStore) -> None:
        execute_tool("create_quiz", {"topic": "RAG", "questions": _ok_questions()})
        qid = store.list_quiz_sets()[0]["id"]
        res = execute_tool("query_quiz_history", {"quiz_set_id": qid})
        assert res.status == "ok"
        assert f"quiz_set_id={qid}" in res.content
        assert "1+1=?" in res.content  # 题干出现
        # detail=False 默认不展示标答
        assert "标答" not in res.content

    def test_quiz_set_id_detail_true_shows_grading(
        self, store: QuizStore,
    ) -> None:
        execute_tool("create_quiz", {"topic": "x", "questions": _ok_questions()})
        qid = store.list_quiz_sets()[0]["id"]
        res = execute_tool("query_quiz_history", {"quiz_set_id": qid, "detail": True})
        assert res.status == "ok"
        assert "标答" in res.content

    def test_unknown_quiz_set_id_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("query_quiz_history", {"quiz_set_id": 999})
        assert res.status == "error"

    def test_invalid_limit_returns_error(self, store: QuizStore) -> None:
        res = execute_tool("query_quiz_history", {"limit": 0})
        assert res.status == "error"


# ── question_id 可见性 ───────────────────────────────────────────────────────

class TestQuestionIdExposed:
    """
    锁住三个 tool 输出里必须带 question_id。grade_quiz 与 add_to_srs 收的都是
    主键，输出只给题号时调用方只能拿题号顶替，会打到别的 quiz 的同号题上。
    """

    def test_create_quiz_returns_id_mapping(self, store: QuizStore) -> None:
        res = execute_tool("create_quiz", {"topic": "x", "questions": _ok_questions()})
        assert res.status == "ok"
        quiz = store.get_quiz_with_questions(store.list_quiz_sets()[0]["id"])
        for q in quiz["questions"]:
            assert f"第{q['order_idx']}题 → question_id={q['id']}" in res.content

    def test_history_detail_shows_question_id(self, store: QuizStore) -> None:
        execute_tool("create_quiz", {"topic": "x", "questions": _ok_questions()})
        quiz_set_id = store.list_quiz_sets()[0]["id"]
        res = execute_tool("query_quiz_history", {"quiz_set_id": quiz_set_id, "detail": True})
        quiz = store.get_quiz_with_questions(quiz_set_id)
        for q in quiz["questions"]:
            assert f"question_id={q['id']}" in res.content

    def test_grade_quiz_wrong_lines_show_question_id(self, store: QuizStore) -> None:
        execute_tool("create_quiz", {"topic": "x", "questions": _ok_questions()})
        quiz_set_id = store.list_quiz_sets()[0]["id"]
        qids = [q["id"] for q in store.get_quiz_with_questions(quiz_set_id)["questions"]]
        with patch("src.agent.tools._grade_one_short_answer", return_value=(0.0, "未作答")):
            res = execute_tool("grade_quiz", {
                "quiz_set_id": quiz_set_id,
                "user_answers": {str(qids[0]): "A", str(qids[1]): "AC", str(qids[2]): ""},
            })
        assert res.status == "ok"
        for qid in qids:
            assert f"question_id={qid}" in res.content


# ── execute_tool 路由 ────────────────────────────────────────────────────────

class TestRouting:

    def test_all_three_tools_route(self, store: QuizStore) -> None:
        r1 = execute_tool("create_quiz", {
            "topic": "x", "questions": _ok_questions(),
        })
        assert isinstance(r1, ToolResult) and r1.status == "ok"
        r2 = execute_tool("query_quiz_history", {})
        assert isinstance(r2, ToolResult) and r2.status == "ok"
        qid = store.list_quiz_sets()[0]["id"]
        qids = [q["id"] for q in store.get_quiz_with_questions(qid)["questions"]]
        with patch("src.agent.tools._grade_one_short_answer",
                   return_value=(1.0, "ok")):
            r3 = execute_tool("grade_quiz", {
                "quiz_set_id": qid,
                "user_answers": {str(qids[0]): "B", str(qids[1]): "AD", str(qids[2]): "x"},
            })
        assert isinstance(r3, ToolResult) and r3.status == "ok"


# ── MCQ 归一化 helper ────────────────────────────────────────────────────────

class TestNormalizeMCQ:

    def test_normalize_basic(self) -> None:
        from src.agent.tools import _normalize_mcq_answer
        assert _normalize_mcq_answer("B") == "B"
        assert _normalize_mcq_answer("ad") == "AD"
        assert _normalize_mcq_answer("a,d") == "AD"
        assert _normalize_mcq_answer("DA") == "AD"
        assert _normalize_mcq_answer(" A D ") == "AD"

    def test_normalize_ignores_non_letters(self) -> None:
        from src.agent.tools import _normalize_mcq_answer
        assert _normalize_mcq_answer("1.B") == "B"
        assert _normalize_mcq_answer("A&C") == "AC"

    def test_normalize_dedupes(self) -> None:
        from src.agent.tools import _normalize_mcq_answer
        assert _normalize_mcq_answer("AAC") == "AC"

    def test_normalize_empty(self) -> None:
        from src.agent.tools import _normalize_mcq_answer
        assert _normalize_mcq_answer("") == ""
        assert _normalize_mcq_answer("xyz") == ""

    def test_grade_mcq_correct(self) -> None:
        from src.agent.tools import _grade_one_mcq
        score, fb = _grade_one_mcq("ad", "AD")
        assert score == 1.0
        assert "正确" in fb

    def test_grade_mcq_wrong(self) -> None:
        from src.agent.tools import _grade_one_mcq
        score, fb = _grade_one_mcq("AB", "AD")
        assert score == 0.0
        assert "你答" in fb

    def test_grade_mcq_empty(self) -> None:
        from src.agent.tools import _grade_one_mcq
        score, fb = _grade_one_mcq("", "A")
        assert score == 0.0
        assert "未作答" in fb


# ── 简答 LLM-judge（用 mock 模拟 chat 返回） ─────────────────────────────────

class TestShortAnswerJudge:

    def test_judge_parses_score_and_feedback(self) -> None:
        from src.agent.tools import _grade_one_short_answer
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(
            content='{"score": 0.8, "feedback": "基本正确，少一个关键点"}',
        ))]
        with patch("src.llm.provider.chat", return_value=mock_resp):
            score, fb = _grade_one_short_answer("stem", "user", "correct")
        assert score == 0.8
        assert "基本正确" in fb

    def test_judge_empty_user_answer_zero(self) -> None:
        from src.agent.tools import _grade_one_short_answer
        score, fb = _grade_one_short_answer("stem", "  ", "correct")
        assert score == 0.0
        assert "未作答" in fb

    def test_judge_llm_failure_returns_zero(self) -> None:
        from src.agent.tools import _grade_one_short_answer
        with patch("src.llm.provider.chat", side_effect=RuntimeError("network")):
            score, fb = _grade_one_short_answer("stem", "user", "correct")
        assert score == 0.0
        assert "批改异常" in fb

    def test_judge_non_json_returns_zero(self) -> None:
        from src.agent.tools import _grade_one_short_answer
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="not a json"))]
        with patch("src.llm.provider.chat", return_value=mock_resp):
            score, fb = _grade_one_short_answer("stem", "user", "correct")
        assert score == 0.0

    def test_judge_score_clamped(self) -> None:
        """LLM 返回 score=1.5 应裁剪到 1.0。"""
        from src.agent.tools import _grade_one_short_answer
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(
            content='{"score": 1.5, "feedback": "x"}',
        ))]
        with patch("src.llm.provider.chat", return_value=mock_resp):
            score, _ = _grade_one_short_answer("stem", "user", "correct")
        assert score == 1.0
