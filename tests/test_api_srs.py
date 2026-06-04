"""SRS due / list / detail 端点 UT。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_srs_store
from src.api.main import app
from src.memory.srs_store import SRSStore


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
