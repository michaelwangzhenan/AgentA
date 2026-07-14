"""Session 管理端点 UT —— list / create / rename / delete / get messages

不走真 Agent；用临时 SQLite 文件构造独立 `SessionStore`。
也顺便覆盖 `SessionStore.rename_session` 和 `create_empty_session`。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_session_store
from src.api.main import app
from src.stores.session_store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SessionStore]:
    """每个测试一份独立 SQLite 文件 + Store 实例（互不污染）。"""
    db = tmp_path / "test_session.db"
    s = SessionStore(str(db))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: SessionStore) -> TestClient:
    app.dependency_overrides[get_session_store] = lambda: store
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


def test_delete_session_clears_learning_plan_loaded(
    client: TestClient, tmp_path: Path,
) -> None:
    from src.stores.learning_plan_store import LearningPlanStore, reset_shared_store_for_testing

    plan_store = LearningPlanStore(str(tmp_path / "plans.db"))
    reset_shared_store_for_testing(plan_store)
    try:
        sid = client.post("/api/sessions").json()["id"]
        pid = plan_store.create_plan(goal="learn rust")
        plan_store.mark_loaded(sid, pid)
        assert plan_store.get_loaded(sid) == pid

        r = client.delete(f"/api/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        assert plan_store.get_loaded(sid) is None
    finally:
        reset_shared_store_for_testing(None)
        plan_store.close()


# ─── GET /api/sessions/{id}/messages ─────────────────────────────────────


def test_get_messages_empty(client: TestClient, store: SessionStore) -> None:
    created = client.post("/api/sessions").json()
    r = client.get(f"/api/sessions/{created['id']}/messages")
    assert r.status_code == 200
    assert r.json() == {"messages": [], "has_more": False, "oldest_id": None}


def test_get_messages_returns_appended(client: TestClient, store: SessionStore) -> None:
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
    assert r.json() == {"messages": [], "has_more": False, "oldest_id": None}


def test_get_messages_pagination_large_session(
    client: TestClient, store: SessionStore
) -> None:
    """千级消息会话：首屏只返一页，上滚 before_id 可拉更早段。"""
    sid = client.post("/api/sessions").json()["id"]
    total = 120
    for i in range(total):
        store.append(sid, {"role": "user", "content": f"q{i}"})
        store.append(sid, {"role": "assistant", "content": f"a{i}"})

    r1 = client.get(f"/api/sessions/{sid}/messages?limit=60")
    assert r1.status_code == 200
    body1 = r1.json()
    assert len(body1["messages"]) == 60
    assert body1["has_more"] is True
    assert body1["oldest_id"] is not None
    assert body1["messages"][0]["content"] == "q90"
    assert body1["messages"][-1]["content"] == "a119"

    r2 = client.get(
        f"/api/sessions/{sid}/messages?limit=60&before_id={body1['oldest_id']}"
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["messages"]) == 60
    assert body2["has_more"] is True
    assert body2["messages"][0]["content"] == "q60"
    assert body2["messages"][-1]["content"] == "a89"

    r3 = client.get(
        f"/api/sessions/{sid}/messages?limit=60&before_id={body2['oldest_id']}"
    )
    body3 = r3.json()
    assert len(body3["messages"]) == 60
    assert body3["has_more"] is True
    assert body3["messages"][0]["content"] == "q30"

    r4 = client.get(
        f"/api/sessions/{sid}/messages?limit=60&before_id={body3['oldest_id']}"
    )
    body4 = r4.json()
    assert len(body4["messages"]) == 60
    assert body4["has_more"] is False
    assert body4["messages"][0]["content"] == "q0"
    assert body4["messages"][-1]["content"] == "a29"


def test_get_messages_user_index_on_user_rows(
    client: TestClient, store: SessionStore
) -> None:
    sid = client.post("/api/sessions").json()["id"]
    for i in range(3):
        store.append(sid, {"role": "user", "content": f"u{i}"})
        store.append(sid, {"role": "assistant", "content": f"a{i}"})

    msgs = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    users = [m for m in msgs if m["role"] == "user"]
    assert [m["user_index"] for m in users] == [0, 1, 2]


# ─── POST /api/sessions/{id}/truncate ────────────────────────────────────


def _seed_turns(store: SessionStore, sid: str, n: int) -> None:
    """写 n 轮对话：u0,a0,u1,a1,...（共 2n 行）"""
    for i in range(n):
        store.append(sid, {"role": "user", "content": f"q{i}"})
        store.append(sid, {"role": "assistant", "content": f"a{i}"})


def test_truncate_drops_from_user_message_and_after(
    client: TestClient, store: SessionStore
) -> None:
    sid = client.post("/api/sessions").json()["id"]
    _seed_turns(store, sid, 3)  # u0 a0 u1 a1 u2 a2

    r = client.post(f"/api/sessions/{sid}/truncate", json={"user_message_index": 1})
    assert r.status_code == 200
    assert r.json() == {"deleted": 4}  # u1 a1 u2 a2

    msgs = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert [m["content"] for m in msgs] == ["q0", "a0"]


def test_truncate_first_user_message_clears_all_messages(
    client: TestClient, store: SessionStore
) -> None:
    sid = client.post("/api/sessions").json()["id"]
    _seed_turns(store, sid, 2)  # 4 行

    r = client.post(f"/api/sessions/{sid}/truncate", json={"user_message_index": 0})
    assert r.json() == {"deleted": 4}
    assert client.get(f"/api/sessions/{sid}/messages").json()["messages"] == []


def test_truncate_out_of_range_index_deletes_nothing(
    client: TestClient, store: SessionStore
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


# ─── SessionStore 方法本身（被上面的端点测覆盖，这里再直接测细节）──


def test_store_truncate_positions_by_user_ordinal_with_interleaved_rows(
    store: SessionStore,
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


def test_store_truncate_negative_index_noop(store: SessionStore) -> None:
    sid = "s-neg"
    store.create_empty_session(sid)
    store.append(sid, {"role": "user", "content": "u0"})
    assert store.truncate_from_user_message(sid, -1) == 0
    assert len(store.load(sid)) == 1


def test_store_rename_returns_false_when_not_exist(store: SessionStore) -> None:
    assert store.rename_session("not-exist", "new") is False


def test_store_create_empty_idempotent(store: SessionStore) -> None:
    assert store.create_empty_session("s1") is True
    assert store.create_empty_session("s1") is False
