"""测试 Phase 2.5 Harness 在 grade_quiz / search_knowledge 主流程上的集成。

覆盖：
    - `_run_quiz_critic` Q1 集成：MCQ 跳过 / short_answer 复审 / 多题混合 flag
    - HARNESS_QUIZ_ENABLED 开关：关时完全跳过 / 开时调 manager
    - HarnessManager 初始化失败 → grade_quiz 不阻塞（软返回空 warning 段）
    - critic verdict 持久化到 quiz_questions.harness_flagged 列
    - `_tool_search_knowledge` R1 集成：HARNESS_RAG_ENABLED 开关 / manager 异常软放行
    - 全部不相关 → 返 ToolResult(empty)（不重召回）

UT 全部 mock LLM / manager，零真实外部调用。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.agent.core import harness_manager as hm
from src.agent.core.harness_manager import HarnessVerdict
from src.agent.tools import ToolResult, execute_tool
from src.memory import quiz_store as quiz_store_module
from src.memory.quiz_store import QuizStore


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> Iterator[QuizStore]:
    s = QuizStore(str(tmp_path / "quiz.db"))
    quiz_store_module.reset_shared_store_for_testing(s)
    yield s
    quiz_store_module.reset_shared_store_for_testing(None)
    s.close()


@pytest.fixture(autouse=True)
def _reset_harness_singleton() -> Iterator[None]:
    hm.reset_for_test()
    yield
    hm.reset_for_test()


def _seed_quiz(store: QuizStore) -> tuple[int, list[int]]:
    """种一个 3 题 quiz：1 单选 + 1 多选 + 1 简答。"""
    qid = store.create_quiz_set(topic="x", num_questions=3)
    store.add_questions(qid, [
        {"order_idx": 1, "q_type": "mcq_single", "stem": "1+1=?",
         "options": ["1", "2", "3", "4"], "correct_answer": "B"},
        {"order_idx": 2, "q_type": "mcq_multi", "stem": "选质数",
         "options": ["2", "3", "4", "6"], "correct_answer": "AB"},
        {"order_idx": 3, "q_type": "short_answer", "stem": "解释 RAG",
         "correct_answer": "Retrieval-Augmented Generation"},
    ])
    qids = [q["id"] for q in store.get_quiz_with_questions(qid)["questions"]]
    return qid, qids


def _all_correct_answers(qids: list[int]) -> dict[str, str]:
    return {str(qids[0]): "B", str(qids[1]): "AB", str(qids[2]): "RAG"}


# ── Q1 集成：grade_quiz + critic ─────────────────────────────────────────────

class TestGradeQuizCriticIntegration:

    def test_disabled_skips_critic_entirely(
        self, store: QuizStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HARNESS_QUIZ_ENABLED=False：grade_quiz 完全跳过 critic，不调 manager。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_QUIZ_ENABLED", False)
        qid, qids = _seed_quiz(store)
        with patch("src.agent.tools._grade_one_short_answer", return_value=(1.0, "完全正确")), \
             patch("src.agent.tools._run_quiz_critic") as critic_call:
            res = execute_tool("grade_quiz", {
                "quiz_set_id": qid, "user_answers": _all_correct_answers(qids),
            })
        assert res.status == "ok"
        # critic 入口函数应未被调用（开关关）
        critic_call.assert_not_called()
        assert "自检" not in res.content

    def test_critic_skips_mcq_only_short_answer(
        self, store: QuizStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """开自检后，critic 只对 short_answer 题调 review_grading（MCQ 字符串比对跳过）。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_QUIZ_ENABLED", True)
        qid, qids = _seed_quiz(store)

        mock_manager = MagicMock()
        mock_manager.review_grading.return_value = HarnessVerdict(
            passed=True, score=4.5, reason="批改合理", raw="{}",
        )
        with patch("src.agent.tools._grade_one_short_answer", return_value=(1.0, "对")), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   return_value=mock_manager):
            res = execute_tool("grade_quiz", {
                "quiz_set_id": qid, "user_answers": _all_correct_answers(qids),
            })
        assert res.status == "ok"
        # 3 题中只有 1 题 short_answer → 只调 1 次 review_grading
        assert mock_manager.review_grading.call_count == 1
        # critic 全过 → 输出无 ⚠️
        assert "自检" not in res.content

    def test_critic_flags_short_answer_when_below_threshold(
        self, store: QuizStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """critic 给 simple short_answer 评低分 → mark harness_flagged + 输出 ⚠️ 块。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_QUIZ_ENABLED", True)
        qid, qids = _seed_quiz(store)

        mock_manager = MagicMock()
        mock_manager.review_grading.return_value = HarnessVerdict(
            passed=False, score=2.0, reason="给分过高", raw="{}",
        )
        with patch("src.agent.tools._grade_one_short_answer", return_value=(1.0, "完美")), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   return_value=mock_manager):
            res = execute_tool("grade_quiz", {
                "quiz_set_id": qid, "user_answers": _all_correct_answers(qids),
            })
        assert res.status == "ok"
        assert "⚠️" in res.content
        assert "Agent 自检" in res.content
        assert "给分过高" in res.content
        # DB 落地：第 3 题（short_answer）harness_flagged=True
        questions = store.get_quiz_with_questions(qid)["questions"]
        assert questions[0]["harness_flagged"] is False  # MCQ 不跑 critic
        assert questions[1]["harness_flagged"] is False
        assert questions[2]["harness_flagged"] is True

    def test_critic_failure_softly_passes(
        self, store: QuizStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """critic 自身失败（failure=True）→ 不 mark + 输出无 warning。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_QUIZ_ENABLED", True)
        qid, qids = _seed_quiz(store)

        mock_manager = MagicMock()
        mock_manager.review_grading.return_value = HarnessVerdict(
            passed=True, score=None, reason="critic 超时", raw="", failure=True,
        )
        with patch("src.agent.tools._grade_one_short_answer", return_value=(1.0, "ok")), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   return_value=mock_manager):
            res = execute_tool("grade_quiz", {
                "quiz_set_id": qid, "user_answers": _all_correct_answers(qids),
            })
        assert "⚠️" not in res.content
        # DB：第 3 题不应被 flag（failure 软放行）
        q3 = store.get_quiz_with_questions(qid)["questions"][2]
        assert q3["harness_flagged"] is False

    def test_manager_init_failure_does_not_break_grade(
        self, store: QuizStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """get_harness_manager() 抛异常（如 critic prompt 文件缺失）→ grade_quiz 软返回空段。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_QUIZ_ENABLED", True)
        qid, qids = _seed_quiz(store)
        with patch("src.agent.tools._grade_one_short_answer", return_value=(1.0, "ok")), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   side_effect=RuntimeError("prompt 文件缺失")):
            res = execute_tool("grade_quiz", {
                "quiz_set_id": qid, "user_answers": _all_correct_answers(qids),
            })
        assert res.status == "ok"
        assert "⚠️" not in res.content


# ── R1 集成：search_knowledge + filter ───────────────────────────────────────

class _FakeHit:
    def __init__(self, doc: str) -> None:
        self.document = doc
        self.source = "fake.md"
        self.distance = 0.1
        self.collection = "kb_test"
        self.score = 0.9
        self.id = doc[:8]
        self.retrievers = ["dense"]
        self.metadata = {"source": "fake.md"}


class TestSearchKnowledgeFilterIntegration:

    def test_disabled_skips_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_RAG_ENABLED", False)

        hits = [_FakeHit("doc 1"), _FakeHit("doc 2")]
        with patch("src.agent.tools.search", return_value=hits), \
             patch("src.agent.tools.format_search_results",
                   return_value="formatted output"), \
             patch("src.agent.core.harness_manager.get_harness_manager") as mgr_call, \
             patch("src.rag.query_rewriter.expand_queries", return_value=["q"]):
            res = execute_tool("search_knowledge", {"query": "q"})
        assert res.status == "ok"
        mgr_call.assert_not_called()

    def test_enabled_filters_via_manager(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_RAG_ENABLED", True)

        hits = [_FakeHit("relevant"), _FakeHit("noise"), _FakeHit("relevant 2")]
        kept = [hits[0], hits[2]]
        mock_manager = MagicMock()
        mock_manager.filter_chunks.return_value = kept
        captured: dict[str, Any] = {}

        def _fmt(returned_hits: list[Any], **_: Any) -> str:
            captured["hits"] = returned_hits
            return "fmt"

        with patch("src.agent.tools.search", return_value=hits), \
             patch("src.agent.tools.format_search_results", side_effect=_fmt), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   return_value=mock_manager), \
             patch("src.rag.query_rewriter.expand_queries", return_value=["q"]):
            res = execute_tool("search_knowledge", {"query": "q"})
        assert res.status == "ok"
        # format_search_results 收到的是 filter 后的 2 条
        assert captured["hits"] == kept
        mock_manager.filter_chunks.assert_called_once()

    def test_filter_returns_empty_yields_tool_result_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全部 chunk 被 critic 判 not_relevant → 返 ToolResult(empty)，不重召回。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_RAG_ENABLED", True)

        hits = [_FakeHit("noise 1"), _FakeHit("noise 2")]
        mock_manager = MagicMock()
        mock_manager.filter_chunks.return_value = []

        with patch("src.agent.tools.search", return_value=hits), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   return_value=mock_manager), \
             patch("src.rag.query_rewriter.expand_queries", return_value=["q"]):
            res = execute_tool("search_knowledge", {"query": "q"})
        assert res.status == "empty"
        assert "未找到" in res.content

    def test_manager_exception_falls_back_to_unfiltered(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """get_harness_manager 抛异常 → 保留原始 hits，仍走 format_search_results。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_RAG_ENABLED", True)

        hits = [_FakeHit("a"), _FakeHit("b")]
        captured: dict[str, Any] = {}

        def _fmt(returned_hits: list[Any], **_: Any) -> str:
            captured["hits"] = returned_hits
            return "fmt"

        with patch("src.agent.tools.search", return_value=hits), \
             patch("src.agent.tools.format_search_results", side_effect=_fmt), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   side_effect=RuntimeError("manager init failed")), \
             patch("src.rag.query_rewriter.expand_queries", return_value=["q"]):
            res = execute_tool("search_knowledge", {"query": "q"})
        assert res.status == "ok"
        assert captured["hits"] == hits  # 软放行：原始 2 条

    def test_empty_hits_skips_filter_call(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """retriever.search 返 0 条 → filter 不调，直接 ToolResult(empty)。"""
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "HARNESS_RAG_ENABLED", True)

        mock_manager = MagicMock()
        with patch("src.agent.tools.search", return_value=[]), \
             patch("src.agent.core.harness_manager.get_harness_manager",
                   return_value=mock_manager), \
             patch("src.rag.query_rewriter.expand_queries", return_value=["q"]):
            res = execute_tool("search_knowledge", {"query": "q"})
        assert res.status == "empty"
        mock_manager.filter_chunks.assert_not_called()
