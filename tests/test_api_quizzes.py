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
