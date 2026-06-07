"""每用户 rules 读写端点 UT。

rules 现在按用户独享，存 auth.db.user_rules 表（不再是磁盘文件）。
用临时 UserStore 注入共享单例，避免污染真实 auth.db。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.memory.user_store import UserStore, reset_shared_store_for_testing


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    store = UserStore(db_path=str(tmp_path / "auth_test.db"))
    reset_shared_store_for_testing(store)
    yield TestClient(app)
    reset_shared_store_for_testing(None)
    store.close()


def test_read_rules_when_empty(client: TestClient) -> None:
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert r.json()["text"] == ""


def test_write_then_read(client: TestClient) -> None:
    content = "# Rules\n- 用中文回答\n- 不要 emoji"
    r = client.put("/api/rules", json={"text": content})
    assert r.status_code == 200
    assert r.json()["length"] == len(content)

    r2 = client.get("/api/rules")
    assert r2.status_code == 200
    assert r2.json()["text"] == content


def test_write_overwrites(client: TestClient) -> None:
    client.put("/api/rules", json={"text": "v1"})
    client.put("/api/rules", json={"text": "v2 longer content"})
    assert client.get("/api/rules").json()["text"] == "v2 longer content"
