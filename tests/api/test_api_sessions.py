"""Session 管理端点 UT —— list / create / rename / delete / get messages

不走真 Agent；用临时 SQLite 文件构造独立 `ChatHistoryStore`。
也顺便覆盖 `ChatHistoryStore.rename_session` 和 `create_empty_session`。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_chat_history
from src.api.main import app
from src.memory.chat_history import ChatHistoryStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ChatHistoryStore]:
    """每个测试一份独立 SQLite 文件 + Store 实例（互不污染）。"""
    db = tmp_path / "test_chat_history.db"
    s = ChatHistoryStore(str(db))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: ChatHistoryStore) -> TestClient:
    app.dependency_overrides[get_chat_history] = lambda: store
    return TestClient(app)


# ─── GET /api/sessions ───────────────────────────────────────────────────


def test_list_sessions_empty(client: TestClient) -> None:
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_list_sessions_after_create(client: TestClient) -> None:
    r1 = client.post("/api/sessions", json={"title": "first"})
    r2 = client.post("/api/sessions", json={"title": "second"})
    assert r1.status_code == 200 and r2.status_code == 200

    r = client.get("/api/sessions")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    titles = {s["title"] for s in sessions}
    assert titles == {"first", "second"}


# ─── POST /api/sessions ──────────────────────────────────────────────────


def test_create_session_without_title_fallbacks_to_new_chat(client: TestClient) -> None:
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    body = r.json()
    assert len(body["id"]) > 0
    # 无 title 时显示层兜底为 "New Chat"（首条 user 消息进来时由 append() 回填真实标题）
    assert body["title"] == "New Chat"
    assert body["msg_count"] == 0


def test_create_session_with_title(client: TestClient) -> None:
    r = client.post("/api/sessions", json={"title": "我的会话"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "我的会话"


def test_create_session_no_body(client: TestClient) -> None:
    """body 完全不传（None / 空 dict）也应当 OK"""
    r = client.post("/api/sessions")
    assert r.status_code == 200
    assert "id" in r.json()


# ─── PATCH /api/sessions/{id} ────────────────────────────────────────────


def test_rename_session(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"title": "old"}).json()
    sid = created["id"]

    r = client.patch(f"/api/sessions/{sid}", json={"title": "new"})
    assert r.status_code == 200
    assert r.json()["title"] == "new"


def test_rename_nonexistent_session_returns_404(client: TestClient) -> None:
    r = client.patch("/api/sessions/not-exist-uuid", json={"title": "x"})
    assert r.status_code == 404


def test_rename_empty_title_returns_422(client: TestClient) -> None:
    created = client.post("/api/sessions").json()
    r = client.patch(f"/api/sessions/{created['id']}", json={"title": ""})
    assert r.status_code == 422


# ─── DELETE /api/sessions/{id} ───────────────────────────────────────────


def test_delete_session(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"title": "to-delete"}).json()
    sid = created["id"]

    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200
    assert r.json() == {"deleted": True}

    listed = client.get("/api/sessions").json()["sessions"]
    assert all(s["id"] != sid for s in listed)


def test_delete_nonexistent_session_returns_deleted_false(client: TestClient) -> None:
    """幂等：不存在的 session 不报 404，只返回 deleted=False"""
    r = client.delete("/api/sessions/not-exist-uuid")
    assert r.status_code == 200
    assert r.json() == {"deleted": False}


# ─── GET /api/sessions/{id}/messages ─────────────────────────────────────


def test_get_messages_empty(client: TestClient, store: ChatHistoryStore) -> None:
    created = client.post("/api/sessions").json()
    r = client.get(f"/api/sessions/{created['id']}/messages")
    assert r.status_code == 200
    assert r.json() == {"messages": []}


def test_get_messages_returns_appended(client: TestClient, store: ChatHistoryStore) -> None:
    """直接通过 store.append 写两条 message，再用 API 拉出来。"""
    created = client.post("/api/sessions").json()
    sid = created["id"]

    store.append(sid, {"role": "user", "content": "hi"})
    store.append(sid, {"role": "assistant", "content": "hello back"})

    r = client.get(f"/api/sessions/{sid}/messages")
    assert r.status_code == 200
    messages = r.json()["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hi"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "hello back"


def test_get_messages_nonexistent_session_returns_empty(client: TestClient) -> None:
    """语义上 session 不存在 = 没消息；不报 404（前端切到已删的 session 时优雅 fallback）"""
    r = client.get("/api/sessions/not-exist-uuid/messages")
    assert r.status_code == 200
    assert r.json() == {"messages": []}


# ─── POST /api/sessions/{id}/truncate ────────────────────────────────────


def _seed_turns(store: ChatHistoryStore, sid: str, n: int) -> None:
    """写 n 轮对话：u0,a0,u1,a1,...（共 2n 行）"""
    for i in range(n):
        store.append(sid, {"role": "user", "content": f"q{i}"})
        store.append(sid, {"role": "assistant", "content": f"a{i}"})


def test_truncate_drops_from_user_message_and_after(
    client: TestClient, store: ChatHistoryStore
) -> None:
    sid = client.post("/api/sessions").json()["id"]
    _seed_turns(store, sid, 3)  # u0 a0 u1 a1 u2 a2

    r = client.post(f"/api/sessions/{sid}/truncate", json={"user_message_index": 1})
    assert r.status_code == 200
    assert r.json() == {"deleted": 4}  # u1 a1 u2 a2

    msgs = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert [m["content"] for m in msgs] == ["q0", "a0"]


def test_truncate_first_user_message_clears_all_messages(
    client: TestClient, store: ChatHistoryStore
) -> None:
    sid = client.post("/api/sessions").json()["id"]
    _seed_turns(store, sid, 2)  # 4 行

    r = client.post(f"/api/sessions/{sid}/truncate", json={"user_message_index": 0})
    assert r.json() == {"deleted": 4}
    assert client.get(f"/api/sessions/{sid}/messages").json()["messages"] == []


def test_truncate_out_of_range_index_deletes_nothing(
    client: TestClient, store: ChatHistoryStore
) -> None:
    sid = client.post("/api/sessions").json()["id"]
    _seed_turns(store, sid, 1)  # u0 a0

    r = client.post(f"/api/sessions/{sid}/truncate", json={"user_message_index": 5})
    assert r.json() == {"deleted": 0}
    assert len(client.get(f"/api/sessions/{sid}/messages").json()["messages"]) == 2


def test_truncate_negative_index_returns_422(client: TestClient) -> None:
    sid = client.post("/api/sessions").json()["id"]
    r = client.post(f"/api/sessions/{sid}/truncate", json={"user_message_index": -1})
    assert r.status_code == 422  # 请求模型 ge=0 校验


# ─── ChatHistoryStore 方法本身（被上面的端点测覆盖，这里再直接测细节）──


def test_store_truncate_positions_by_user_ordinal_with_interleaved_rows(
    store: ChatHistoryStore,
) -> None:
    """中间夹多条 assistant 行，仍按 user 序号（而非行序号）定位截断点。"""
    sid = "s-trunc"
    store.create_empty_session(sid)
    store.append(sid, {"role": "user", "content": "u0"})
    store.append(sid, {"role": "assistant", "content": "a0a"})
    store.append(sid, {"role": "assistant", "content": "a0b"})
    store.append(sid, {"role": "user", "content": "u1"})
    store.append(sid, {"role": "assistant", "content": "a1"})

    deleted = store.truncate_from_user_message(sid, 1)
    assert deleted == 2  # u1 + a1
    assert [m["content"] for m in store.load(sid)] == ["u0", "a0a", "a0b"]


def test_store_truncate_negative_index_noop(store: ChatHistoryStore) -> None:
    sid = "s-neg"
    store.create_empty_session(sid)
    store.append(sid, {"role": "user", "content": "u0"})
    assert store.truncate_from_user_message(sid, -1) == 0
    assert len(store.load(sid)) == 1


def test_store_rename_returns_false_when_not_exist(store: ChatHistoryStore) -> None:
    assert store.rename_session("not-exist", "new") is False


def test_store_create_empty_idempotent(store: ChatHistoryStore) -> None:
    assert store.create_empty_session("s1") is True
    assert store.create_empty_session("s1") is False
