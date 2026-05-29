"""
测试 [QuizStore](../src/memory/quiz_store.py)（Phase 2.3 G1 / D1 / D9 / D10）。

覆盖：
    - 表结构幂等初始化、独立 db 文件
    - create_quiz_set / add_questions / get_quiz_with_questions 基本 CRUD
    - add_questions 非法 row 静默跳过 + options JSON 序列化 / 反序列化
    - get_quiz_with_questions 题目按 order_idx 升序
    - update_grading：批量更新 + 状态切 graded + total_score 边界裁剪 + archived 拒改 + 跨 set qid 拒改
    - list_quiz_sets：按 plan_id 过滤 + 排除 archived + limit
    - archive_quiz_set：状态切 archived + 重复 archive 返回 False
    - delete_quiz_set：硬删除 + 级联删 questions
    - 资源管理：context manager
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.memory.quiz_store import QuizStore


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> Iterator[QuizStore]:
    db = QuizStore(str(tmp_path / "quiz.db"))
    yield db
    db.close()


def _sample_questions() -> list[dict[str, object]]:
    """5 题：3 MCQ（2 单 + 1 多）+ 2 简答，模拟 60/40 比例。"""
    return [
        {
            "order_idx": 1, "q_type": "mcq_single",
            "stem": "Python 列表与字典的主要区别？",
            "options": ["顺序 vs 无序", "可变 vs 不可变", "都不是", "我不知道"],
            "correct_answer": "A", "explanation": "list 有序，dict 无序",
        },
        {
            "order_idx": 2, "q_type": "mcq_single",
            "stem": "RAG 的全称是？",
            "options": ["RA Gen", "Retrieval-Augmented Generation", "Random Ag", "R.A.G"],
            "correct_answer": "B",
        },
        {
            "order_idx": 3, "q_type": "mcq_multi",
            "stem": "下列属于检索流程组件的有？",
            "options": ["embedding", "BM25", "tokenizer", "transformer"],
            "correct_answer": "AB",
        },
        {
            "order_idx": 4, "q_type": "short_answer",
            "stem": "用一句话解释 cosine similarity",
            "options": [],
            "correct_answer": "向量夹角余弦衡量两向量方向相似度",
        },
        {
            "order_idx": 5, "q_type": "short_answer",
            "stem": "RRF 中 k 通常取多少",
            "correct_answer": "60",
        },
    ]


# ── 基本 CRUD ────────────────────────────────────────────────────────────────

class TestBasicCRUD:

    def test_create_quiz_set_basic(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="RAG 检索基础", num_questions=5)
        quiz = store.get_quiz_set(qid)
        assert quiz is not None
        assert quiz["topic"] == "RAG 检索基础"
        assert quiz["num_questions"] == 5
        assert quiz["status"] == "created"
        assert quiz["plan_id"] is None
        assert quiz["total_score"] is None

    def test_create_quiz_set_with_plan_link(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(
            topic="x", num_questions=3, plan_id=7, stage_idx=2,
        )
        quiz = store.get_quiz_set(qid)
        assert quiz["plan_id"] == 7
        assert quiz["stage_idx"] == 2

    def test_create_quiz_set_rejects_empty_topic(self, store: QuizStore) -> None:
        with pytest.raises(ValueError, match="topic"):
            store.create_quiz_set(topic="", num_questions=3)

    def test_create_quiz_set_rejects_zero_num(self, store: QuizStore) -> None:
        with pytest.raises(ValueError, match="num_questions"):
            store.create_quiz_set(topic="x", num_questions=0)

    def test_create_quiz_set_rejects_bad_plan_id(self, store: QuizStore) -> None:
        with pytest.raises(ValueError, match="plan_id"):
            store.create_quiz_set(topic="x", num_questions=1, plan_id=0)

    def test_add_questions_basic(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=5)
        n = store.add_questions(qid, _sample_questions())
        assert n == 5
        quiz = store.get_quiz_with_questions(qid)
        assert quiz is not None
        assert len(quiz["questions"]) == 5
        # 按 order_idx 升序
        orders = [q["order_idx"] for q in quiz["questions"]]
        assert orders == sorted(orders)

    def test_add_questions_options_roundtrip(self, store: QuizStore) -> None:
        """options list 序列化 → JSON 串 → 读回为 list，顺序保留。"""
        qid = store.create_quiz_set(topic="x", num_questions=1)
        store.add_questions(qid, [_sample_questions()[0]])
        q = store.get_quiz_with_questions(qid)["questions"][0]
        assert q["options"] == _sample_questions()[0]["options"]

    def test_add_questions_short_answer_empty_options(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=1)
        store.add_questions(qid, [_sample_questions()[3]])
        q = store.get_quiz_with_questions(qid)["questions"][0]
        assert q["options"] == []

    def test_add_questions_skips_illegal(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=5)
        bad = [
            {"order_idx": 0, "q_type": "mcq_single", "stem": "x", "correct_answer": "A"},  # order_idx=0
            {"order_idx": 1, "q_type": "unknown", "stem": "x", "correct_answer": "A"},  # q_type 非法
            {"order_idx": 2, "q_type": "mcq_single", "stem": "", "correct_answer": "A"},  # stem 空
            {"order_idx": 3, "q_type": "mcq_single", "stem": "x", "correct_answer": ""},  # 标答空
            {"order_idx": 4, "q_type": "short_answer", "stem": "x", "correct_answer": "ok"},  # 合法
        ]
        assert store.add_questions(qid, bad) == 1

    def test_add_questions_rejects_unknown_set(self, store: QuizStore) -> None:
        with pytest.raises(ValueError, match="不存在"):
            store.add_questions(999, _sample_questions())

    def test_get_returns_none_for_missing(self, store: QuizStore) -> None:
        assert store.get_quiz_set(999) is None
        assert store.get_quiz_with_questions(999) is None


# ── update_grading ─────────────────────────────────────────────────────────

class TestUpdateGrading:

    @pytest.fixture
    def loaded(self, store: QuizStore) -> tuple[int, list[int]]:
        qid = store.create_quiz_set(topic="x", num_questions=5)
        store.add_questions(qid, _sample_questions())
        quiz = store.get_quiz_with_questions(qid)
        return qid, [q["id"] for q in quiz["questions"]]

    def test_basic_grading_updates_state(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, qids = loaded
        gradings = [
            {"question_id": qids[0], "user_answer": "A", "score": 1.0, "feedback": "正确"},
            {"question_id": qids[1], "user_answer": "C", "score": 0.0, "feedback": "错"},
        ]
        assert store.update_grading(qid, gradings, total_score=80.0) is True

        quiz = store.get_quiz_with_questions(qid)
        assert quiz["status"] == "graded"
        assert quiz["total_score"] == 80.0
        assert quiz["graded_at"]
        q_by_id = {q["id"]: q for q in quiz["questions"]}
        assert q_by_id[qids[0]]["score"] == 1.0
        assert q_by_id[qids[0]]["user_answer"] == "A"
        assert q_by_id[qids[1]]["score"] == 0.0

    def test_total_score_clamped_to_0_100(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, _ = loaded
        store.update_grading(qid, [], total_score=150.0)
        assert store.get_quiz_set(qid)["total_score"] == 100.0
        store.update_grading(qid, [], total_score=-5.0)
        assert store.get_quiz_set(qid)["total_score"] == 0.0

    def test_question_score_clamped_to_0_1(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, qids = loaded
        store.update_grading(qid, [
            {"question_id": qids[0], "user_answer": "A", "score": 2.0, "feedback": ""},
            {"question_id": qids[1], "user_answer": "B", "score": -1.0, "feedback": ""},
        ], total_score=0.0)
        quiz = store.get_quiz_with_questions(qid)
        q_by_id = {q["id"]: q for q in quiz["questions"]}
        assert q_by_id[qids[0]]["score"] == 1.0
        assert q_by_id[qids[1]]["score"] == 0.0

    def test_skips_cross_set_qid(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, qids = loaded
        # 另建一个 set，拿到它的 qid
        qid2 = store.create_quiz_set(topic="other", num_questions=1)
        store.add_questions(qid2, [_sample_questions()[0]])
        other_qid = store.get_quiz_with_questions(qid2)["questions"][0]["id"]
        store.update_grading(qid, [
            {"question_id": qids[0], "user_answer": "A", "score": 1.0, "feedback": ""},
            {"question_id": other_qid, "user_answer": "A", "score": 1.0, "feedback": ""},
        ], total_score=50.0)
        # other_qid 不应被改
        other_q = store.get_quiz_with_questions(qid2)["questions"][0]
        assert other_q["user_answer"] == ""
        assert other_q["score"] == 0.0

    def test_archived_set_rejects_grading(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, _ = loaded
        store.archive_quiz_set(qid)
        assert store.update_grading(qid, [], 50.0) is False

    def test_unknown_set_returns_false(self, store: QuizStore) -> None:
        assert store.update_grading(999, [], 50.0) is False

    def test_regrade_allowed_after_graded(
        self, store: QuizStore, loaded: tuple[int, list[int]],
    ) -> None:
        qid, qids = loaded
        store.update_grading(qid, [
            {"question_id": qids[0], "user_answer": "A", "score": 1.0, "feedback": ""},
        ], total_score=60.0)
        # 再批改一次
        assert store.update_grading(qid, [
            {"question_id": qids[0], "user_answer": "B", "score": 0.0, "feedback": "改了"},
        ], total_score=20.0) is True
        quiz = store.get_quiz_with_questions(qid)
        assert quiz["total_score"] == 20.0


# ── archive / delete ────────────────────────────────────────────────────────

class TestLifecycle:

    def test_archive_quiz_set(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=1)
        assert store.archive_quiz_set(qid) is True
        assert store.get_quiz_set(qid)["status"] == "archived"

    def test_archive_unknown_returns_false(self, store: QuizStore) -> None:
        assert store.archive_quiz_set(999) is False

    def test_archive_twice_returns_false(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=1)
        store.archive_quiz_set(qid)
        assert store.archive_quiz_set(qid) is False

    def test_delete_cascades_questions(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=5)
        store.add_questions(qid, _sample_questions())
        assert store.delete_quiz_set(qid) is True
        # 重新建一个 set，验证 question 表已被 cascade（不会有残留）
        qid2 = store.create_quiz_set(topic="y", num_questions=1)
        quiz2 = store.get_quiz_with_questions(qid2)
        assert quiz2["questions"] == []

    def test_delete_unknown_returns_false(self, store: QuizStore) -> None:
        assert store.delete_quiz_set(999) is False


# ── list_quiz_sets ──────────────────────────────────────────────────────────

class TestListQuizSets:

    def test_orders_by_created_desc(self, store: QuizStore) -> None:
        q1 = store.create_quiz_set(topic="t1", num_questions=1)
        q2 = store.create_quiz_set(topic="t2", num_questions=1)
        q3 = store.create_quiz_set(topic="t3", num_questions=1)
        ids = [q["id"] for q in store.list_quiz_sets()]
        # 同秒 tie-breaker：id DESC
        assert ids == [q3, q2, q1]

    def test_filters_archived_by_default(self, store: QuizStore) -> None:
        q1 = store.create_quiz_set(topic="t1", num_questions=1)
        q2 = store.create_quiz_set(topic="t2", num_questions=1)
        store.archive_quiz_set(q2)
        ids = [q["id"] for q in store.list_quiz_sets()]
        assert ids == [q1]

    def test_include_archived(self, store: QuizStore) -> None:
        q1 = store.create_quiz_set(topic="t1", num_questions=1)
        q2 = store.create_quiz_set(topic="t2", num_questions=1)
        store.archive_quiz_set(q2)
        ids = {q["id"] for q in store.list_quiz_sets(include_archived=True)}
        assert ids == {q1, q2}

    def test_filter_by_plan_id(self, store: QuizStore) -> None:
        q1 = store.create_quiz_set(topic="t1", num_questions=1, plan_id=5)
        q2 = store.create_quiz_set(topic="t2", num_questions=1, plan_id=6)
        q3 = store.create_quiz_set(topic="t3", num_questions=1, plan_id=5)
        ids = {q["id"] for q in store.list_quiz_sets(plan_id=5)}
        assert ids == {q1, q3}
        assert q2 not in ids

    def test_limit(self, store: QuizStore) -> None:
        for i in range(5):
            store.create_quiz_set(topic=f"t{i}", num_questions=1)
        assert len(store.list_quiz_sets(limit=3)) == 3


# ── Phase 2.5 Harness：harness_flagged 列 + mark + fail-fast ─────────────────

class TestHarnessSchema:

    def test_new_question_default_harness_flagged_false(
        self, store: QuizStore,
    ) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=1)
        store.add_questions(qid, [_sample_questions()[3]])
        q = store.get_quiz_with_questions(qid)["questions"][0]
        assert q["harness_flagged"] is False

    def test_mark_question_harness_flagged(self, store: QuizStore) -> None:
        qid = store.create_quiz_set(topic="x", num_questions=1)
        store.add_questions(qid, [_sample_questions()[3]])
        q_id = store.get_quiz_with_questions(qid)["questions"][0]["id"]
        assert store.mark_question_harness_flagged(q_id) is True
        q = store.get_quiz_with_questions(qid)["questions"][0]
        assert q["harness_flagged"] is True

    def test_mark_unknown_question_returns_false(self, store: QuizStore) -> None:
        assert store.mark_question_harness_flagged(99999) is False

    def test_mark_idempotent(self, store: QuizStore) -> None:
        """重复 mark 同题多次都返 True 且字段保持 1。"""
        qid = store.create_quiz_set(topic="x", num_questions=1)
        store.add_questions(qid, [_sample_questions()[3]])
        q_id = store.get_quiz_with_questions(qid)["questions"][0]["id"]
        assert store.mark_question_harness_flagged(q_id) is True
        assert store.mark_question_harness_flagged(q_id) is True
        q = store.get_quiz_with_questions(qid)["questions"][0]
        assert q["harness_flagged"] is True

    def test_fail_fast_when_old_schema_missing_column(self, tmp_path: Path) -> None:
        """模拟旧 quiz.db（quiz_questions 缺 harness_flagged 列）→ QuizStore 初始化 raise。

        建表 schema 必须跟 Phase 2.3 现状一致（quiz_sets 完整 + quiz_questions 缺 harness_flagged）
        否则 CREATE TABLE IF NOT EXISTS 会跳过建表、然后业务 SQL 撞别的列错。
        """
        import sqlite3
        old_db = tmp_path / "old.db"
        conn = sqlite3.connect(str(old_db))
        conn.executescript("""
            CREATE TABLE quiz_sets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                topic           TEXT    NOT NULL,
                plan_id         INTEGER,
                stage_idx       INTEGER,
                num_questions   INTEGER NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'created',
                total_score     REAL,
                created_at      TEXT    NOT NULL,
                graded_at       TEXT    NOT NULL DEFAULT '',
                updated_at      TEXT    NOT NULL
            );
            CREATE TABLE quiz_questions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_set_id     INTEGER NOT NULL REFERENCES quiz_sets(id) ON DELETE CASCADE,
                order_idx       INTEGER NOT NULL,
                q_type          TEXT    NOT NULL,
                stem            TEXT    NOT NULL,
                options         TEXT    NOT NULL DEFAULT '',
                correct_answer  TEXT    NOT NULL,
                explanation     TEXT    NOT NULL DEFAULT '',
                user_answer     TEXT    NOT NULL DEFAULT '',
                score           REAL    NOT NULL DEFAULT 0.0,
                feedback        TEXT    NOT NULL DEFAULT ''
            );
        """)
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="schema 已过期"):
            QuizStore(str(old_db))


# ── 资源管理 ──────────────────────────────────────────────────────────────────

def test_context_manager(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ctx.db")
    with QuizStore(db_path) as store:
        qid = store.create_quiz_set(topic="x", num_questions=1)
        assert qid > 0
    # 重新打开能查到
    store2 = QuizStore(db_path)
    assert store2.get_quiz_set(qid) is not None
    store2.close()
