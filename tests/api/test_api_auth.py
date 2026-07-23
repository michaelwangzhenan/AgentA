"""认证端点 UT：注册已关闭、登录仍可用。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.deps import get_user_store
from src.api.main import app
from src.stores.user_store import UserStore


@pytest.fixture
def user_store(tmp_path: Path) -> Iterator[UserStore]:
    s = UserStore(str(tmp_path / "auth.db"))
    s.create_user("alice", "pw", role="admin")
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


def test_register_endpoint_removed(client: TestClient) -> None:
    res = client.post("/api/auth/register", json={"username": "newbie", "password": "pw"})
    assert res.status_code == 404


def test_login_still_works(client: TestClient) -> None:
    orig = _cfg.AUTH_ENABLED
    _cfg.AUTH_ENABLED = True
    try:
        res = client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        assert res.status_code == 200
        assert res.json()["user"]["username"] == "alice"
    finally:
        _cfg.AUTH_ENABLED = orig
