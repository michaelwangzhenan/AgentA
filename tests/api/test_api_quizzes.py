"""Quiz list / detail 端点 UT。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_quiz_store
from src.api.main import app
from src.memory.quiz_store import QuizStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[QuizStore]:
    db = tmp_path / "test_quiz.db"
    s = QuizStore(str(db))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: QuizStore) -> TestClient:
    app.dependency_overrides[get_quiz_store] = lambda: store
    return TestClient(app)


# ─── GET /api/quizzes ────────────────────────────────────────────────────


def test_list_quizzes_empty(client: TestClient) -> None:
    r = client.get("/api/quizzes")
    assert r.status_code == 200
    assert r.json() == {"quizzes": []}


def test_list_quizzes_with_data(client: TestClient, store: QuizStore) -> None:
    qid1 = store.create_quiz_set(topic="A", num_questions=2)
    qid2 = store.create_quiz_set(topic="B", num_questions=3)
    assert qid1 > 0 and qid2 > 0

    r = client.get("/api/quizzes")
    assert r.status_code == 200
    items = r.json()["quizzes"]
    assert len(items) == 2
    # 倒序：新的（B）在前
    topics = [it["topic"] for it in items]
    assert topics == ["B", "A"]


# ─── GET /api/quizzes/{id} ──────────────────────────────────────────────


def test_get_quiz_404(client: TestClient) -> None:
    r = client.get("/api/quizzes/9999")
    assert r.status_code == 404


def test_get_quiz_with_questions(client: TestClient, store: QuizStore) -> None:
    qid = store.create_quiz_set(topic="attention", num_questions=2)
    store.add_questions(
        qid,
        [
            {
                "order_idx": 1,
                "q_type": "mcq_single",
                "stem": "What is Q?",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
                "explanation": "because A",
            },
            {
                "order_idx": 2,
                "q_type": "short_answer",
                "stem": "Explain K",
                "correct_answer": "Key vector",
                "explanation": "for attention",
            },
        ],
    )

    r = client.get(f"/api/quizzes/{qid}")
    assert r.status_code == 200
    quiz = r.json()
    assert quiz["id"] == qid
    assert quiz["topic"] == "attention"
    assert len(quiz["questions"]) == 2
    assert quiz["questions"][0]["q_type"] == "mcq_single"
    assert quiz["questions"][0]["options"] == ["A", "B", "C", "D"]
    assert quiz["questions"][1]["q_type"] == "short_answer"
    assert quiz["questions"][1]["options"] == []


# ─── POST /api/quizzes/{id}/submit（答题批改）──────────────────────────


def _seed_mcq_quiz(store: QuizStore) -> tuple[int, list[int]]:
    """造一套纯选择题 quiz，返回 (quiz_set_id, [question_id...])。"""
    qid = store.create_quiz_set(topic="t", num_questions=2)
    store.add_questions(
        qid,
        [
            {"order_idx": 1, "q_type": "mcq_single", "stem": "Q1",
             "options": ["x", "y"], "correct_answer": "A"},
            {"order_idx": 2, "q_type": "mcq_single", "stem": "Q2",
             "options": ["x", "y"], "correct_answer": "B"},
        ],
    )
    question_ids = [q["id"] for q in store.get_quiz_with_questions(qid)["questions"]]
    return qid, question_ids


def test_submit_mcq_quiz_grades_locally(client: TestClient, store: QuizStore) -> None:
    qid, qids = _seed_mcq_quiz(store)
    r = client.post(
        f"/api/quizzes/{qid}/submit",
        json={"answers": [
            {"question_id": qids[0], "answer": "A"},  # 对
            {"question_id": qids[1], "answer": "A"},  # 错（应 B）
        ]},
    )
    assert r.status_code == 200
    quiz = r.json()
    assert quiz["status"] == "graded"
    assert quiz["total_score"] == 50.0
    q1, q2 = quiz["questions"]
    assert q1["score"] == 1.0
    assert q2["score"] == 0.0


def test_submit_short_answer_uses_llm_judge(
    client: TestClient, store: QuizStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    qid = store.create_quiz_set(topic="t", num_questions=1)
    store.add_questions(
        qid,
        [{"order_idx": 1, "q_type": "short_answer", "stem": "解释 RAG",
          "correct_answer": "检索增强生成"}],
    )
    question_id = store.get_quiz_with_questions(qid)["questions"][0]["id"]

    import src.agent.tools as tools_mod
    monkeypatch.setattr(
        tools_mod, "_grade_one_short_answer",
        lambda stem, user_ans, correct: (0.8, "基本正确"),
    )

    r = client.post(
        f"/api/quizzes/{qid}/submit",
        json={"answers": [{"question_id": question_id, "answer": "检索+生成"}]},
    )
    assert r.status_code == 200
    quiz = r.json()
    assert quiz["questions"][0]["score"] == 0.8
    assert quiz["questions"][0]["feedback"] == "基本正确"
    assert quiz["total_score"] == 80.0


def test_submit_quiz_404(client: TestClient) -> None:
    r = client.post("/api/quizzes/9999/submit", json={"answers": []})
    assert r.status_code == 404


def test_archive_quiz(client: TestClient, store: QuizStore) -> None:
    qid = store.create_quiz_set(topic="t", num_questions=1)
    r = client.post(f"/api/quizzes/{qid}/archive")
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
    # 归档后不在默认列表
    assert client.get("/api/quizzes").json()["quizzes"] == []
