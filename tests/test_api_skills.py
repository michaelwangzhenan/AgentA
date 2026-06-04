"""Skills 列表端点 UT。

用 monkeypatch 改 DEFAULT_SKILLS_DIR 指到 tmp_path 构造的目录树。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.cli.skill_loader as skill_loader
from src.api.main import app


def _write_skill(dir_path: Path, name: str, description: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def _write_invalid_skill(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(skill_loader, "DEFAULT_SKILLS_DIR", tmp_path)
    return TestClient(app)


def test_skills_empty_dir(client: TestClient) -> None:
    r = client.get("/api/skills")
    assert r.status_code == 200
    assert r.json() == {"loaded": [], "failed": []}


def test_skills_loaded(client: TestClient, tmp_path: Path) -> None:
    _write_skill(tmp_path / "skill_a", "skill_a", "测试技能 A", "skill body A")
    _write_skill(tmp_path / "skill_b", "skill_b", "测试技能 B", "skill body B")

    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert len(body["loaded"]) == 2
    assert body["failed"] == []
    names = {s["name"] for s in body["loaded"]}
    assert names == {"skill_a", "skill_b"}


def test_skills_failed(client: TestClient, tmp_path: Path) -> None:
    _write_invalid_skill(tmp_path / "broken")
    _write_skill(tmp_path / "good", "good", "OK skill", "body")

    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert len(body["loaded"]) == 1
    assert body["loaded"][0]["name"] == "good"
    assert len(body["failed"]) == 1
    assert "missing_frontmatter" in body["failed"][0]["reason"]
