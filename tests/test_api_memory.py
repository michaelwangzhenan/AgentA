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


def test_list_memories_after_add(client: TestClient, store: UserMemoryStore) -> None:
    store.add("用户喜欢蓝色", source="manual")
    store.add("用户名叫 alice", source="explicit")
    r = client.get("/api/memory")
    assert r.status_code == 200
    items = r.json()["memories"]
    assert len(items) == 2
    texts = {m["text"] for m in items}
    assert texts == {"用户喜欢蓝色", "用户名叫 alice"}
    # 扁平模型：不再有 category / key 字段
    assert "category" not in items[0]
    assert "updated_at" in items[0]


# ─── POST /api/memory（create） ─────────────────────────────────────────


def test_create_memory_ok(client: TestClient) -> None:
    r = client.post(
        "/api/memory",
        json={"text": "用户希望用中文回答", "source": "manual"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "用户希望用中文回答"
    assert body["source"] == "manual"
    assert body["id"] > 0


def test_create_memory_missing_text_422(client: TestClient) -> None:
    r = client.post("/api/memory", json={"source": "manual"})
    assert r.status_code == 422


def test_create_memory_appends_each_time(client: TestClient) -> None:
    client.post("/api/memory", json={"text": "记忆一"})
    client.post("/api/memory", json={"text": "记忆二"})
    items = client.get("/api/memory").json()["memories"]
    assert len(items) == 2


# ─── PATCH /api/memory/{id} ──────────────────────────────────────────────


def test_patch_memory_ok(client: TestClient, store: UserMemoryStore) -> None:
    store.add("原内容")
    item_id = store.load_all()[0]["id"]
    r = client.patch(f"/api/memory/{item_id}", json={"text": "新内容"})
    assert r.status_code == 200
    assert r.json() == {"updated": True}

    items = store.load_all()
    assert items[0]["text"] == "新内容"


def test_patch_memory_404(client: TestClient) -> None:
    r = client.patch("/api/memory/9999", json={"text": "x"})
    assert r.status_code == 404


# ─── DELETE /api/memory/{id} ─────────────────────────────────────────────


def test_delete_memory_ok(client: TestClient, store: UserMemoryStore) -> None:
    store.add("一条记忆")
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
    store.add("记忆一")
    store.add("记忆二")
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
