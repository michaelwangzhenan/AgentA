"""项目 rules 读写端点 UT。

用 monkeypatch 改 cwd / config.USER_RULES_FILE 指到 tmp_path。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_cfg, "USER_RULES_FILE", ".agenta/rules.md")
    return TestClient(app)


def test_read_rules_when_missing(client: TestClient) -> None:
    r = client.get("/api/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == ""
    assert body["exists"] is False
    assert body["path"].endswith("rules.md")


def test_write_then_read(client: TestClient, tmp_path: Path) -> None:
    content = "# Rules\n- 用中文回答\n- 不要 emoji"
    r = client.put("/api/rules", json={"text": content})
    assert r.status_code == 200
    body = r.json()
    assert body["length"] == len(content)
    assert body["restart_required"] is True

    rules_path = tmp_path / ".agenta" / "rules.md"
    assert rules_path.exists()
    assert rules_path.read_text(encoding="utf-8") == content

    r2 = client.get("/api/rules")
    assert r2.status_code == 200
    assert r2.json()["text"] == content
    assert r2.json()["exists"] is True


def test_write_overwrites(client: TestClient, tmp_path: Path) -> None:
    client.put("/api/rules", json={"text": "v1"})
    client.put("/api/rules", json={"text": "v2 longer content"})

    rules_path = tmp_path / ".agenta" / "rules.md"
    assert rules_path.read_text(encoding="utf-8") == "v2 longer content"


def test_write_creates_parent_dir(client: TestClient, tmp_path: Path) -> None:
    assert not (tmp_path / ".agenta").exists()
    r = client.put("/api/rules", json={"text": "abc"})
    assert r.status_code == 200
    assert (tmp_path / ".agenta").is_dir()
