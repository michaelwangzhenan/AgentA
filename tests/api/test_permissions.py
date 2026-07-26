"""scope 写权限 UT。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.deps import get_user_store
from src.api.main import app
from src.api.permissions import can_write, capabilities_for_user
from src.stores.user_store import ROLE_ADMIN, ROLE_READONLY, ROLE_USER, UserStore


@pytest.fixture
def user_store(tmp_path) -> UserStore:
    s = UserStore(str(tmp_path / "auth.db"))
    s.create_user("guest", "pw", role=ROLE_READONLY)
    s.create_user("alice", "pw", role=ROLE_USER)
    s.create_user("admin", "pw", role=ROLE_ADMIN)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(user_store: UserStore) -> TestClient:
    app.dependency_overrides[get_user_store] = lambda: user_store
    return TestClient(app)


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_cfg, "AUTH_ENABLED", True)
    monkeypatch.setattr(_cfg, "AUTH_ADMIN_USERNAME", "admin")


def test_capabilities_for_roles() -> None:
    ro = capabilities_for_user({"role": ROLE_READONLY, "username": "guest"})
    assert "chat" not in ro
    assert "kb" not in ro
    assert "config" not in ro

    user = capabilities_for_user({"role": ROLE_USER, "username": "alice"})
    assert "chat" in user
    assert "memory" in user
    assert "kb" not in user

    admin = capabilities_for_user({"role": ROLE_ADMIN, "username": "admin"})
    assert "kb" in admin
    assert "skills" in admin


def test_readonly_cannot_write_chat(client: TestClient, auth_on: None) -> None:
    res = client.post("/api/auth/login", json={"username": "guest", "password": "pw"})
    assert res.status_code == 200
    body = res.json()["user"]
    assert body["role"] == ROLE_READONLY
    assert "chat" not in body["capabilities"]

    blocked = client.post("/api/sessions", json={})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "当前账号无修改权限"


def test_user_can_write_chat_but_not_kb(client: TestClient, auth_on: None) -> None:
    client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
    assert client.post("/api/sessions", json={}).status_code == 200

    kb = client.post(
        "/api/kb/upload/cancel",
        data={"ingest_id": "x"},
    )
    assert kb.status_code == 403


def test_readonly_can_read_skills_list(client: TestClient, auth_on: None) -> None:
    client.post("/api/auth/login", json={"username": "guest", "password": "pw"})
    res = client.get("/api/skills")
    assert res.status_code == 200


def test_admin_create_readonly_user(client: TestClient, auth_on: None) -> None:
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    res = client.post(
        "/api/admin/users",
        json={"username": "viewer", "password": "secret", "role": ROLE_READONLY},
    )
    assert res.status_code == 201
    assert res.json()["role"] == ROLE_READONLY
