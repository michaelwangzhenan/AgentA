"""SRS due / list / detail 端点 UT。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_srs_store
from src.api.main import app
from src.stores.srs_store import SRSStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SRSStore]:
    db = tmp_path / "test_srs.db"
    s = SRSStore(str(db))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: SRSStore) -> TestClient:
    app.dependency_overrides[get_srs_store] = lambda: store
    return TestClient(app)


# ─── GET /api/srs/cards ─────────────────────────────────────────────────


def test_list_cards_empty(client: TestClient) -> None:
    r = client.get("/api/srs/cards")
    assert r.status_code == 200
    assert r.json() == {"cards": []}


def test_list_cards_with_data(client: TestClient, store: SRSStore) -> None:
    store.add_card(source_type="manual", front="Q1", back="A1")
    store.add_card(source_type="manual", front="Q2", back="A2")

    r = client.get("/api/srs/cards")
    assert r.status_code == 200
    cards = r.json()["cards"]
    assert len(cards) == 2


# ─── GET /api/srs/due ───────────────────────────────────────────────────


def test_list_due_returns_new_cards(client: TestClient, store: SRSStore) -> None:
    """新加的 card next_review_at = now，立即 due。"""
    store.add_card(source_type="manual", front="Q1", back="A1")
    store.add_card(source_type="manual", front="Q2", back="A2")

    r = client.get("/api/srs/due")
    assert r.status_code == 200
    assert len(r.json()["cards"]) == 2


def test_list_due_respects_limit(client: TestClient, store: SRSStore) -> None:
    for i in range(5):
        store.add_card(source_type="manual", front=f"Q{i}", back=f"A{i}")
    r = client.get("/api/srs/due?limit=3")
    assert r.status_code == 200
    assert len(r.json()["cards"]) == 3


# ─── GET /api/srs/cards/{id} ────────────────────────────────────────────


def test_get_card_404(client: TestClient) -> None:
    r = client.get("/api/srs/cards/9999")
    assert r.status_code == 404


def test_get_card_returns_full_fields(client: TestClient, store: SRSStore) -> None:
    cid = store.add_card(source_type="manual", front="Q", back="A", note="note text")
    r = client.get(f"/api/srs/cards/{cid}")
    assert r.status_code == 200
    card = r.json()
    assert card["id"] == cid
    assert card["front"] == "Q"
    assert card["back"] == "A"
    assert card["note"] == "note text"
    assert card["status"] == "active"
    assert card["ease_factor"] > 0


# ─── POST /api/srs/cards（建卡）─────────────────────────────────────────


def test_create_card(client: TestClient) -> None:
    r = client.post(
        "/api/srs/cards",
        json={"front": "什么是 RAG", "back": "检索增强生成", "note": "n"},
    )
    assert r.status_code == 201
    card = r.json()
    assert card["front"] == "什么是 RAG"
    assert card["source_type"] == "manual"
    assert card["status"] == "active"


def test_create_card_empty_front_422(client: TestClient) -> None:
    r = client.post("/api/srs/cards", json={"front": "", "back": "x"})
    assert r.status_code == 422


# ─── POST /api/srs/cards/{id}/review（4 档评分）──────────────────────────


def test_review_card_good_pushes_next_review(client: TestClient, store: SRSStore) -> None:
    cid = store.add_card(source_type="manual", front="Q", back="A")
    r = client.post(f"/api/srs/cards/{cid}/review", json={"rating": "good"})
    assert r.status_code == 200
    card = r.json()
    # 第一次答对 interval ≥ 1，repetitions=1
    assert card["repetitions"] == 1
    assert card["interval_days"] >= 1
    assert card["last_reviewed_at"] != ""


def test_review_card_again_increments_lapses(client: TestClient, store: SRSStore) -> None:
    cid = store.add_card(source_type="manual", front="Q", back="A")
    r = client.post(f"/api/srs/cards/{cid}/review", json={"rating": "again"})
    assert r.status_code == 200
    assert r.json()["lapses"] == 1


def test_review_card_invalid_rating_400(client: TestClient, store: SRSStore) -> None:
    cid = store.add_card(source_type="manual", front="Q", back="A")
    r = client.post(f"/api/srs/cards/{cid}/review", json={"rating": "ok"})
    assert r.status_code == 400


def test_review_card_404(client: TestClient) -> None:
    r = client.post("/api/srs/cards/9999/review", json={"rating": "good"})
    assert r.status_code == 404


# ─── suspend / resume / archive ─────────────────────────────────────────


def test_suspend_resume_archive(client: TestClient, store: SRSStore) -> None:
    cid = store.add_card(source_type="manual", front="Q", back="A")

    r = client.post(f"/api/srs/cards/{cid}/suspend")
    assert r.status_code == 200
    assert r.json()["status"] == "suspended"

    r = client.post(f"/api/srs/cards/{cid}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "active"

    r = client.post(f"/api/srs/cards/{cid}/archive")
    assert r.status_code == 200
    assert r.json()["status"] == "archived"


def test_review_suspended_card_400(client: TestClient, store: SRSStore) -> None:
    cid = store.add_card(source_type="manual", front="Q", back="A")
    store.suspend(cid)
    r = client.post(f"/api/srs/cards/{cid}/review", json={"rating": "good"})
    assert r.status_code == 400
