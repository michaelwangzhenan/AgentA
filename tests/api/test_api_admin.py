"""主账号用户管理端点 UT。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.deps import get_user_store
from src.api.main import app
from src.stores.user_store import ROLE_ADMIN, UserStore


@pytest.fixture
def user_store(tmp_path: Path) -> Iterator[UserStore]:
    s = UserStore(str(tmp_path / "auth.db"))
    s.create_user("admin", "pw", role=ROLE_ADMIN)
    s.create_user("bob", "pw", role=ROLE_ADMIN)
    s.create_user("carol", "pw")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(user_store: UserStore) -> TestClient:
    app.dependency_overrides[get_user_store] = lambda: user_store
    return TestClient(app)


def _login(c: TestClient, username: str, password: str = "pw") -> None:
    res = c.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(_cfg, "AUTH_ENABLED", True)
    monkeypatch.setattr(_cfg, "AUTH_ADMIN_USERNAME", "admin")
    yield


def test_list_users_requires_super_admin(client: TestClient, auth_on: None) -> None:
    _login(client, "bob")
    assert client.get("/api/admin/users").status_code == 403

    _login(client, "admin")
    res = client.get("/api/admin/users")
    assert res.status_code == 200
    names = {u["username"] for u in res.json()["users"]}
    assert names == {"admin", "bob", "carol"}
    admin_row = next(u for u in res.json()["users"] if u["username"] == "admin")
    assert admin_row["can_manage_users"] is True
    bob_row = next(u for u in res.json()["users"] if u["username"] == "bob")
    assert bob_row["can_manage_users"] is False


def test_create_user(client: TestClient, auth_on: None) -> None:
    _login(client, "admin")
    res = client.post(
        "/api/admin/users",
        json={"username": "dave", "password": "secret", "role": "user"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["username"] == "dave"
    assert body["role"] == "user"
    assert body["can_manage_users"] is False

    dup = client.post(
        "/api/admin/users",
        json={"username": "dave", "password": "x"},
    )
    assert dup.status_code == 409


def test_update_user_role(client: TestClient, auth_on: None) -> None:
    _login(client, "admin")
    carol = next(
        u for u in client.get("/api/admin/users").json()["users"] if u["username"] == "carol"
    )
    res = client.patch(
        f"/api/admin/users/{carol['id']}/role",
        json={"role": "admin"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "admin"

    admin = next(
        u for u in client.get("/api/admin/users").json()["users"] if u["username"] == "admin"
    )
    blocked = client.patch(
        f"/api/admin/users/{admin['id']}/role",
        json={"role": "user"},
    )
    assert blocked.status_code == 400


def test_delete_user(client: TestClient, auth_on: None) -> None:
    _login(client, "admin")
    carol = next(
        u for u in client.get("/api/admin/users").json()["users"] if u["username"] == "carol"
    )
    assert client.delete(f"/api/admin/users/{carol['id']}").status_code == 200

    admin = next(
        u for u in client.get("/api/admin/users").json()["users"] if u["username"] == "admin"
    )
    assert client.delete(f"/api/admin/users/{admin['id']}").status_code == 400


def test_me_exposes_can_manage_users(client: TestClient, auth_on: None) -> None:
    _login(client, "admin")
    assert client.get("/api/auth/me").json()["can_manage_users"] is True

    _login(client, "bob")
    assert client.get("/api/auth/me").json()["can_manage_users"] is False
