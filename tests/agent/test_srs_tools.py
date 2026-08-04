"""
测试 Phase 2.4 四 SRS 业务 tool（[`src/agent/tools.py`](../src/agent/tools.py) G3 / D5 / D11 / D13-D15）。

覆盖：
    - JSON Schema 完整性：`_SRS_TOOLS` 四 tool 名 / required 字段
    - `get_tools()` 含四 SRS tool（常驻，无需 skill 加载）
    - `_tool_add_to_srs`：
        * manual 路径（front+back 必填 / note 截断）
        * quiz_question 批量路径（反查 QuizStore + 防重复 + 部分跳过）
        * 非法 source_type / 缺 question_ids
        * 归属校验：别人 quiz 里的题不能进当前用户的队列
    - `_tool_query_srs_due`：摘要 / detail / empty / limit
    - `_tool_review_srs_card`：4 档 mapping + 写库 + 非法 rating / card_id
    - `_tool_query_srs_stats`：空队列 empty / 有卡 ok
    - `execute_tool` 路由覆盖四 tool

测试隔离：tmp_path 注入独立 SRSStore / QuizStore，替换全局共享单例。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.agent.tools import ToolResult, execute_tool, get_tools
from src.stores import quiz_store as quiz_store_module
from src.stores import srs_store as srs_store_module
from src.stores.quiz_store import QuizStore
from src.stores.srs_store import SRSStore
from src.stores.user_context import use_user


@pytest.fixture
def srs(tmp_path: Path) -> Iterator[SRSStore]:
    s = SRSStore(str(tmp_path / "srs.db"))
    srs_store_module.reset_shared_store_for_testing(s)
    yield s
    srs_store_module.reset_shared_store_for_testing(None)
    s.close()


@pytest.fixture
def quiz(tmp_path: Path) -> Iterator[QuizStore]:
    q = QuizStore(str(tmp_path / "quiz.db"))
    quiz_store_module.reset_shared_store_for_testing(q)
    yield q
    quiz_store_module.reset_shared_store_for_testing(None)
    q.close()


def _make_quiz_set_with_questions(store: QuizStore) -> tuple[int, list[int]]:
    """建一个 quiz_set + 3 题，返回 (quiz_set_id, [question_id]*3)。"""
    qs_id = store.create_quiz_set(topic="测试主题", num_questions=3, plan_id=None, stage_idx=None)
    store.add_questions(qs_id, [
        {
            "order_idx": 1, "q_type": "mcq_single",
            "stem": "Python 列表是什么？",
            "options": ["有序集合", "字典", "集合", "无"],
            "correct_answer": "A", "explanation": "list 是有序可变序列",
        },
        {
            "order_idx": 2, "q_type": "short_answer",
            "stem": "解释 RAG", "options": [],
            "correct_answer": "Retrieval-Augmented Generation", "explanation": "",
        },
        {
            "order_idx": 3, "q_type": "mcq_single",
            "stem": "1+1=?",
            "options": ["1", "2", "3", "4"],
            "correct_answer": "B", "explanation": "",
        },
    ])
    full = store.get_quiz_with_questions(qs_id)
    qids = [q["id"] for q in full["questions"]]
    return qs_id, qids


# ── JSON Schema / get_tools ─────────────────────────────────────────────────


class TestSchema:
    def test_get_tools_contains_four_srs(self) -> None:
        names = {t["function"]["name"] for t in get_tools()}
        assert {"add_to_srs", "query_srs_due", "review_srs_card", "query_srs_stats"}.issubset(names)

    def test_add_to_srs_schema(self) -> None:
        tool = next(t for t in get_tools() if t["function"]["name"] == "add_to_srs")
        params = tool["function"]["parameters"]
        assert "source_type" in params["required"]
        assert params["properties"]["source_type"]["enum"] == ["quiz_question", "manual"]

    def test_review_srs_card_schema_4_ratings(self) -> None:
        tool = next(t for t in get_tools() if t["function"]["name"] == "review_srs_card")
        params = tool["function"]["parameters"]
        assert set(params["required"]) == {"card_id", "rating"}
        assert params["properties"]["rating"]["enum"] == ["again", "hard", "good", "easy"]


# ── add_to_srs ─────────────────────────────────────────────────────────────


class TestAddToSrs:
    def test_manual_basic(self, srs: SRSStore) -> None:
        result = execute_tool("add_to_srs", {
            "source_type": "manual",
            "front": "Python 装饰器",
            "back": "闭包 + __call__",
        })
        assert result.status == "ok"
        assert "card_id=" in result.content
        cards = srs.list_cards()
        assert len(cards) == 1
        assert cards[0]["source_type"] == "manual"
        assert cards[0]["front"] == "Python 装饰器"

    def test_manual_with_note(self, srs: SRSStore) -> None:
        execute_tool("add_to_srs", {
            "source_type": "manual",
            "front": "Q", "back": "A",
            "note": "复习重点",
        })
        assert srs.list_cards()[0]["note"] == "复习重点"

    def test_manual_missing_front(self, srs: SRSStore) -> None:
        result = execute_tool("add_to_srs", {
            "source_type": "manual", "back": "A",
        })
        assert result.status == "error"
        assert "front" in result.content.lower()

    def test_manual_missing_back(self, srs: SRSStore) -> None:
        result = execute_tool("add_to_srs", {
            "source_type": "manual", "front": "Q",
        })
        assert result.status == "error"
        assert "back" in result.content.lower()

    def test_quiz_question_batch(self, srs: SRSStore, quiz: QuizStore) -> None:
        _, qids = _make_quiz_set_with_questions(quiz)
        # 把全部 3 道题加入 SRS
        result = execute_tool("add_to_srs", {
            "source_type": "quiz_question",
            "question_ids": qids,
        })
        assert result.status == "ok"
        cards = srs.list_cards()
        assert len(cards) == 3
        for c in cards:
            assert c["source_type"] == "quiz_question"
            assert c["source_ref"] in qids
        # 答案应包含 explanation（有的话）
        first = next(c for c in cards if c["source_ref"] == qids[0])
        assert "考点：list 是有序可变序列" in first["back"]

    def test_mcq_card_front_contains_options(self, srs: SRSStore, quiz: QuizStore) -> None:
        """MCQ 卡的 front 必须含 ABCD 选项 + 题型标签（否则复习时无法核对答案）。"""
        _, qids = _make_quiz_set_with_questions(quiz)
        execute_tool("add_to_srs", {"source_type": "quiz_question", "question_ids": [qids[0]]})
        card = srs.list_cards()[0]
        assert "[单选]" in card["front"]
        assert "Python 列表是什么？" in card["front"]
        # 4 个选项都应该按 A./B./C./D. 编号出现
        assert "A. 有序集合" in card["front"]
        assert "B. 字典" in card["front"]
        assert "C. 集合" in card["front"]
        assert "D. 无" in card["front"]

    def test_mcq_card_back_maps_letter_to_option_text(self, srs: SRSStore, quiz: QuizStore) -> None:
        """MCQ 卡的 back 必须把标答字母翻成选项文本，否则用户只看到孤零零的 'A'。"""
        _, qids = _make_quiz_set_with_questions(quiz)
        execute_tool("add_to_srs", {"source_type": "quiz_question", "question_ids": [qids[0]]})
        card = srs.list_cards()[0]
        # correct_answer="A"，对应 options[0]="有序集合"
        assert "A — 有序集合" in card["back"]
        assert "考点：list 是有序可变序列" in card["back"]

    def test_short_answer_card_format_unchanged(self, srs: SRSStore, quiz: QuizStore) -> None:
        """简答题的 front 应带 [简答] 标签但不拼选项；back 为标答文本。"""
        _, qids = _make_quiz_set_with_questions(quiz)
        # qids[1] 是简答题（解释 RAG）
        execute_tool("add_to_srs", {"source_type": "quiz_question", "question_ids": [qids[1]]})
        card = next(c for c in srs.list_cards() if c["source_ref"] == qids[1])
        assert "[简答]" in card["front"]
        assert "解释 RAG" in card["front"]
        # 简答题 back 直接是标答文本，不该带 "— ..."（那是 MCQ 的格式）
        assert card["back"].startswith("Retrieval-Augmented Generation")

    def test_mcq_multi_back_concatenates_option_texts(self, srs: SRSStore, quiz: QuizStore) -> None:
        """多选 MCQ：back 把每个字母对应的选项文本用 ` / ` 拼起来。"""
        # 新增一道多选题
        qs_id = quiz.create_quiz_set(topic="多选测试", num_questions=1)
        quiz.add_questions(qs_id, [{
            "order_idx": 1, "q_type": "mcq_multi",
            "stem": "下列属于检索流程的有？",
            "options": ["embedding", "BM25", "tokenizer", "transformer"],
            "correct_answer": "AB",
            "explanation": "embedding 与 BM25 都是检索器",
        }])
        new_qid = quiz.get_quiz_with_questions(qs_id)["questions"][0]["id"]
        execute_tool("add_to_srs", {"source_type": "quiz_question", "question_ids": [new_qid]})
        card = next(c for c in srs.list_cards() if c["source_ref"] == new_qid)
        assert "[多选]" in card["front"]
        # back：AB → "embedding / BM25"
        assert "AB — embedding / BM25" in card["back"]

    def test_quiz_question_skips_duplicates(self, srs: SRSStore, quiz: QuizStore) -> None:
        _, qids = _make_quiz_set_with_questions(quiz)
        execute_tool("add_to_srs", {"source_type": "quiz_question", "question_ids": qids})
        # 重新调一次：全部已存在 → 全部跳过 → empty
        result = execute_tool("add_to_srs", {"source_type": "quiz_question", "question_ids": qids})
        assert result.status == "empty"
        assert "跳过 3" in result.content

    def test_quiz_question_unknown_id_skipped(self, srs: SRSStore, quiz: QuizStore) -> None:
        _, qids = _make_quiz_set_with_questions(quiz)
        result = execute_tool("add_to_srs", {
            "source_type": "quiz_question",
            "question_ids": [qids[0], 99999],  # 一个真实 + 一个不存在
        })
        assert result.status == "ok"
        assert "新增 1 张" in result.content
        assert "跳过 1 张" in result.content

    def test_quiz_question_empty_ids(self, srs: SRSStore) -> None:
        result = execute_tool("add_to_srs", {
            "source_type": "quiz_question",
            "question_ids": [],
        })
        assert result.status == "error"

    def test_invalid_source_type(self, srs: SRSStore) -> None:
        result = execute_tool("add_to_srs", {"source_type": "bogus"})
        assert result.status == "error"


class TestAddToSrsOwnership:
    """
    锁住归属校验：quiz_questions 主键全库递增，把题号当主键传进来会正好命中
    别人 quiz 里的同号题，卡片内容因此串到其他用户。反查必须 join quiz_sets 校验。
    """

    def test_other_user_question_skipped(self, srs: SRSStore, quiz: QuizStore) -> None:
        with use_user(777):
            _, qids = _make_quiz_set_with_questions(quiz)
        with use_user(888):
            result = execute_tool("add_to_srs", {
                "source_type": "quiz_question",
                "question_ids": qids,
            })
        assert result.status == "empty"
        assert "不属于当前用户" in result.content
        assert srs.list_cards(user_id=888) == []

    def test_own_question_still_added(self, srs: SRSStore, quiz: QuizStore) -> None:
        with use_user(777):
            _, qids = _make_quiz_set_with_questions(quiz)
            result = execute_tool("add_to_srs", {
                "source_type": "quiz_question",
                "question_ids": qids,
            })
        assert result.status == "ok"
        assert len(srs.list_cards(user_id=777)) == 3

    def test_mixed_own_and_other_user(self, srs: SRSStore, quiz: QuizStore) -> None:
        with use_user(777):
            _, other_qids = _make_quiz_set_with_questions(quiz)
        with use_user(888):
            _, own_qids = _make_quiz_set_with_questions(quiz)
            result = execute_tool("add_to_srs", {
                "source_type": "quiz_question",
                "question_ids": [own_qids[0], other_qids[0]],
            })
        assert result.status == "ok"
        assert "新增 1 张" in result.content
        assert "跳过 1 张" in result.content
        cards = srs.list_cards(user_id=888)
        assert [c["source_ref"] for c in cards] == [own_qids[0]]


# ── query_srs_due ──────────────────────────────────────────────────────────


class TestQueryDue:
    def test_empty_due(self, srs: SRSStore) -> None:
        result = execute_tool("query_srs_due", {})
        assert result.status == "empty"

    def test_due_summary(self, srs: SRSStore) -> None:
        srs.add_card("manual", "A", "a")
        srs.add_card("manual", "B", "b")
        result = execute_tool("query_srs_due", {})
        assert result.status == "ok"
        assert "2 张" in result.content
        # 摘要模式不应含完整 back 文本
        assert "back" not in result.content.lower() or "→ 用户开始复习时" in result.content

    def test_due_summary_strips_mcq_options(self, srs: SRSStore, quiz: QuizStore) -> None:
        """MCQ 卡的 front 含 ABCD 多行选项 — 摘要必须只取题干第一行，
        否则 query_srs_due 默认摘要被多行选项撑乱，LLM context 也被污染。"""
        _, qids = _make_quiz_set_with_questions(quiz)
        execute_tool("add_to_srs", {"source_type": "quiz_question", "question_ids": [qids[0]]})
        result = execute_tool("query_srs_due", {})  # 摘要模式
        assert result.status == "ok"
        # 题干本身应出现在摘要里
        assert "Python 列表是什么？" in result.content
        # 但完整选项不应出现在摘要里（它们应只在 detail=true 时出现）
        for opt_text in ("A. 有序集合", "B. 字典", "C. 集合", "D. 无"):
            assert opt_text not in result.content, (
                f"摘要不应含 MCQ 选项 {opt_text!r}（应只在 detail=true 时出现）"
            )

    def test_due_detail_includes_back(self, srs: SRSStore) -> None:
        srs.add_card("manual", "题目甲", "答案乙")
        result = execute_tool("query_srs_due", {"detail": True})
        assert result.status == "ok"
        assert "答案乙" in result.content

    def test_due_limit(self, srs: SRSStore) -> None:
        for i in range(5):
            srs.add_card("manual", f"q{i}", "a")
        result = execute_tool("query_srs_due", {"limit": 2})
        assert result.status == "ok"
        assert "2 张" in result.content

    def test_due_invalid_limit(self, srs: SRSStore) -> None:
        result = execute_tool("query_srs_due", {"limit": 0})
        assert result.status == "error"


# ── review_srs_card ────────────────────────────────────────────────────────


class TestReviewCard:
    def test_good_updates_state(self, srs: SRSStore) -> None:
        cid = srs.add_card("manual", "Q", "A")
        result = execute_tool("review_srs_card", {"card_id": cid, "rating": "good"})
        assert result.status == "ok"
        card = srs.get_card(cid)
        assert card["repetitions"] == 1
        assert card["interval_days"] == 1
        assert card["last_reviewed_at"] != ""

    def test_again_resets_and_increments_lapses(self, srs: SRSStore) -> None:
        cid = srs.add_card("manual", "Q", "A")
        srs.update_review_state(
            cid, ease_factor=2.5, interval_days=10,
            repetitions=3, lapses=0,
            next_review_at="2026-01-01T00:00:00",
        )
        result = execute_tool("review_srs_card", {"card_id": cid, "rating": "again"})
        assert result.status == "ok"
        card = srs.get_card(cid)
        assert card["repetitions"] == 0
        assert card["interval_days"] == 1
        assert card["lapses"] == 1

    def test_easy_bonus(self, srs: SRSStore) -> None:
        cid = srs.add_card("manual", "Q", "A")
        srs.update_review_state(
            cid, ease_factor=2.5, interval_days=10,
            repetitions=2, lapses=0,
            next_review_at="2026-01-01T00:00:00",
        )
        execute_tool("review_srs_card", {"card_id": cid, "rating": "easy"})
        card = srs.get_card(cid)
        assert card["ease_factor"] > 2.5  # easy → ease 上调
        assert card["interval_days"] > 10  # easy bonus

    def test_invalid_rating(self, srs: SRSStore) -> None:
        cid = srs.add_card("manual", "Q", "A")
        result = execute_tool("review_srs_card", {"card_id": cid, "rating": "bogus"})
        assert result.status == "error"

    def test_unknown_card_id(self, srs: SRSStore) -> None:
        result = execute_tool("review_srs_card", {"card_id": 999, "rating": "good"})
        assert result.status == "error"
        assert "不存在" in result.content

    def test_invalid_card_id(self, srs: SRSStore) -> None:
        result = execute_tool("review_srs_card", {"card_id": 0, "rating": "good"})
        assert result.status == "error"

    def test_review_suspended_rejected(self, srs: SRSStore) -> None:
        cid = srs.add_card("manual", "Q", "A")
        srs.suspend(cid)
        result = execute_tool("review_srs_card", {"card_id": cid, "rating": "good"})
        assert result.status == "error"
        assert "suspended" in result.content


# ── query_srs_stats ────────────────────────────────────────────────────────


class TestStats:
    def test_empty_queue(self, srs: SRSStore) -> None:
        result = execute_tool("query_srs_stats", {})
        assert result.status == "empty"

    def test_with_cards(self, srs: SRSStore) -> None:
        srs.add_card("manual", "A", "a")
        srs.add_card("manual", "B", "b")
        result = execute_tool("query_srs_stats", {})
        assert result.status == "ok"
        assert "总 active：2" in result.content
        assert "平均 ease" in result.content


# ── execute_tool 路由 ──────────────────────────────────────────────────────


class TestRouting:
    def test_all_four_routed(self, srs: SRSStore) -> None:
        r1 = execute_tool("add_to_srs", {"source_type": "manual", "front": "Q", "back": "A"})
        assert r1.status == "ok"
        r2 = execute_tool("query_srs_due", {})
        assert r2.status == "ok"
        # 拿到 cid 后 review
        cid = srs.list_cards()[0]["id"]
        r3 = execute_tool("review_srs_card", {"card_id": cid, "rating": "good"})
        assert r3.status == "ok"
        r4 = execute_tool("query_srs_stats", {})
        assert r4.status == "ok"

    def test_unknown_tool_name(self) -> None:
        result = execute_tool("query_srs_xyz", {})
        assert result.status == "error"
        assert "未知工具" in result.content
