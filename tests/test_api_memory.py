"""User Memory 管理端点 UT。

不走真 Agent；用临时 SQLite 文件构造独立 UserMemoryStore。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_user_memory_store
from src.api.main import app
from src.memory.user_memory import UserMemoryStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[UserMemoryStore]:
    db = tmp_path / "test_user_memory.db"
    s = UserMemoryStore(str(db))
    yield s


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: UserMemoryStore) -> TestClient:
    app.dependency_overrides[get_user_memory_store] = lambda: store
    return TestClient(app)


# ─── GET /api/memory ─────────────────────────────────────────────────────


def test_list_memories_empty(client: TestClient) -> None:
    r = client.get("/api/memory")
    assert r.status_code == 200
    assert r.json() == {"memories": []}


def test_list_memories_after_upsert(client: TestClient, store: UserMemoryStore) -> None:
    store.upsert("preference", "favorite_color", "blue", source="manual")
    store.upsert("background", "name", "alice", source="explicit")
    r = client.get("/api/memory")
    assert r.status_code == 200
    items = r.json()["memories"]
    assert len(items) == 2
    cats = {m["category"] for m in items}
    assert cats == {"preference", "background"}


# ─── POST /api/memory（upsert） ─────────────────────────────────────────


def test_upsert_memory_ok(client: TestClient) -> None:
    r = client.post(
        "/api/memory",
        json={"category": "preference", "key": "lang", "value": "中文", "source": "manual"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "preference"
    assert body["key"] == "lang"
    assert body["value"] == "中文"
    assert body["source"] == "manual"
    assert body["id"] > 0


def test_upsert_memory_invalid_category(client: TestClient) -> None:
    r = client.post(
        "/api/memory",
        json={"category": "fake_cat_xyz", "key": "k", "value": "v"},
    )
    assert r.status_code == 422
    assert "fake_cat_xyz" in r.json()["detail"]


def test_upsert_memory_updates_existing(client: TestClient) -> None:
    client.post("/api/memory", json={"category": "preference", "key": "k1", "value": "v1"})
    r = client.post("/api/memory", json={"category": "preference", "key": "k1", "value": "v2"})
    assert r.status_code == 200
    assert r.json()["value"] == "v2"

    items = client.get("/api/memory").json()["memories"]
    assert len([m for m in items if m["key"] == "k1"]) == 1


# ─── PATCH /api/memory/{id} ──────────────────────────────────────────────


def test_patch_memory_ok(client: TestClient, store: UserMemoryStore) -> None:
    store.upsert("preference", "k", "original")
    item_id = store.load_all()[0]["id"]
    r = client.patch(f"/api/memory/{item_id}", json={"value": "updated"})
    assert r.status_code == 200
    assert r.json() == {"deleted": True}

    items = store.load_all()
    assert items[0]["value"] == "updated"


def test_patch_memory_404(client: TestClient) -> None:
    r = client.patch("/api/memory/9999", json={"value": "x"})
    assert r.status_code == 404


# ─── DELETE /api/memory/{id} ─────────────────────────────────────────────


def test_delete_memory_ok(client: TestClient, store: UserMemoryStore) -> None:
    store.upsert("preference", "k", "v")
    item_id = store.load_all()[0]["id"]
    r = client.delete(f"/api/memory/{item_id}")
    assert r.status_code == 200
    assert r.json() == {"deleted": True}
    assert store.load_all() == []


def test_delete_memory_nonexistent_returns_false(client: TestClient) -> None:
    r = client.delete("/api/memory/9999")
    assert r.status_code == 200
    assert r.json() == {"deleted": False}


# ─── DELETE /api/memory（clear） ────────────────────────────────────────


def test_clear_memories(client: TestClient, store: UserMemoryStore) -> None:
    store.upsert("preference", "k1", "v1")
    store.upsert("preference", "k2", "v2")
    r = client.delete("/api/memory")
    assert r.status_code == 200
    assert r.json() == {"cleared": 2}
    assert store.load_all() == []


# ─── 503 when USER_MEMORY_ENABLED=false ───────────────────────────────────


def test_503_when_store_disabled() -> None:
    app.dependency_overrides[get_user_memory_store] = lambda: None
    client = TestClient(app)
    r = client.get("/api/memory")
    assert r.status_code == 503
    assert "USER_MEMORY_ENABLED" in r.json()["detail"]
