"""/admin/backup/* 端点 UT：create/list/download/delete/restore + 文件名/路径穿越拦截。

鉴权由 conftest 的 _disable_auth_by_default 兜底为 admin；备份目录指向临时目录，
还原用 monkeypatch 把 _PROJECT_ROOT 改到临时根，避免污染真实项目。
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.routes.backup as backup_route
from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def backup_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "backups"
    monkeypatch.setattr(backup_route.config, "BACKUP_DIR", str(d))
    return d


def _write_dummy_backup(d: Path, name: str = "agenta-backup-20260613-120000.zip") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "backup-manifest.json",
            json.dumps(
                {
                    "timestamp": "20260613-120000",
                    "created_at": "2026-06-13T12:00:00",
                    "include_vectors": True,
                    "category_stats": {"A": {"files": 1, "bytes": 3}},
                    "files": [
                        {"category": "A", "arc": ".env", "restore": ".env",
                         "external": False, "bytes": 3}
                    ],
                }
            ),
        )
        zf.writestr(".env", "X=1")
    return p


def _zip_bytes(files: dict, manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("backup-manifest.json", json.dumps(manifest))
        for arc, content in files.items():
            zf.writestr(arc, content)
    return buf.getvalue()


def test_list_empty(client, backup_dir):
    r = client.get("/api/admin/backup/list")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_create_returns_snapshot(client, backup_dir, monkeypatch):
    seen: dict = {}

    def fake_make_backup(out_dir, *, categories=None, timestamp=None):
        seen["categories"] = categories
        return _write_dummy_backup(Path(out_dir))

    monkeypatch.setattr(backup_route.rb, "make_backup", fake_make_backup)
    r = client.post("/api/admin/backup/create", json={"categories": ["A", "B", "C"]})
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("agenta-backup-")
    assert body["file_count"] == 1
    assert body["include_vectors"] is True
    assert seen["categories"] == {"A", "B", "C"}


def test_create_rejects_bad_category(client, backup_dir):
    r = client.post("/api/admin/backup/create", json={"categories": ["A", "Z"]})
    assert r.status_code == 400


def test_create_rejects_empty_categories(client, backup_dir):
    r = client.post("/api/admin/backup/create", json={"categories": []})
    assert r.status_code == 400


def test_list_after_create(client, backup_dir):
    _write_dummy_backup(backup_dir)
    r = client.get("/api/admin/backup/list")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["timestamp"] == "20260613-120000"


def test_download_ok(client, backup_dir):
    _write_dummy_backup(backup_dir)
    r = client.get("/api/admin/backup/download/agenta-backup-20260613-120000.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


def test_download_bad_name_400(client, backup_dir):
    r = client.get("/api/admin/backup/download/evil.zip")
    assert r.status_code == 400


def test_download_missing_404(client, backup_dir):
    r = client.get("/api/admin/backup/download/agenta-backup-20990101-000000.zip")
    assert r.status_code == 404


def test_delete_ok(client, backup_dir):
    p = _write_dummy_backup(backup_dir)
    r = client.delete("/api/admin/backup/agenta-backup-20260613-120000.zip")
    assert r.status_code == 200
    assert not p.exists()


def test_delete_bad_name_400(client, backup_dir):
    r = client.delete("/api/admin/backup/notmatching.zip")
    assert r.status_code == 400


def test_restore_invalid_zip_400(client):
    r = client.post(
        "/api/admin/backup/restore",
        files={"file": ("x.zip", b"not a zip", "application/zip")},
    )
    assert r.status_code == 400


def test_restore_traversal_rejected_400(client, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(backup_route, "_PROJECT_ROOT", root)
    data = _zip_bytes(
        {"a": "x"},
        {"files": [{"arc": "a", "restore": "../evil.txt", "external": False}]},
    )
    r = client.post(
        "/api/admin/backup/restore",
        files={"file": ("b.zip", data, "application/zip")},
    )
    assert r.status_code == 400
    assert not (tmp_path / "evil.txt").exists()


def test_restore_ok(client, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(backup_route, "_PROJECT_ROOT", root)
    data = _zip_bytes(
        {"sub/f.txt": "hello"},
        {"files": [{"arc": "sub/f.txt", "restore": "sub/f.txt", "external": False}]},
    )
    r = client.post(
        "/api/admin/backup/restore",
        files={"file": ("b.zip", data, "application/zip")},
    )
    assert r.status_code == 200
    assert r.json()["restored"] == 1
    assert (root / "sub" / "f.txt").read_text(encoding="utf-8") == "hello"
