"""src/runtime_backup.py 验收：清单构建、备份/还原往返、SQLite 在线备份、还原路径安全校验。"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.services.runtime_backup as rb

ROOT = Path(__file__).resolve().parents[1]


def _make_fake_root(root: Path) -> None:
    """在临时根下造出各类待备份样本：.env / sqlite / 向量库目录树 / 报告 / 编辑器配置。"""
    (root / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    (root / ".agenta").mkdir()
    (root / ".agenta" / "config_overrides.json").write_text('{"a":1}', encoding="utf-8")

    (root / "db" / "sqlite").mkdir(parents=True)
    db = root / "db" / "sqlite" / "session.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE msg (id INTEGER, txt TEXT)")
    conn.execute("INSERT INTO msg VALUES (1, 'hello')")
    conn.commit()
    conn.close()

    (root / "db" / "chroma").mkdir(parents=True)
    (root / "db" / "chroma" / "chroma.sqlite3").write_bytes(b"vectordata")

    (root / "tools" / "rag_eval").mkdir(parents=True)
    (root / "tools" / "rag_eval" / "golden.json").write_text("[]", encoding="utf-8")
    (root / "tools" / "reports" / "security").mkdir(parents=True)
    (root / "tools" / "reports" / "security" / "r1.md").write_text("report", encoding="utf-8")

    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "ws.code-workspace").write_text("{}", encoding="utf-8")


def _fake_config(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        MEMORY_DB_PATH=str(root / "db" / "sqlite" / "session.db"),
        AUTH_DB_PATH=str(root / "db" / "sqlite" / "auth.db"),  # 不存在 → 应跳过
        CHROMA_DB_PATH=str(root / "db" / "chroma"),
        BM25_INDEX_DIR=str(root / "db" / "bm25"),  # 不存在 → 应跳过
    )


def test_categories_filter_drops_c(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _make_fake_root(root)
    cfg = _fake_config(root)
    with_c = [c for c, _, _ in rb.build_plan(root, cfg, categories=None)]  # None=全选
    without_c = [c for c, _, _ in rb.build_plan(root, cfg, categories={"A", "B", "E", "F", "K"})]
    assert "C" in with_c
    assert "C" not in without_c


def test_categories_only_b(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _make_fake_root(root)
    cfg = _fake_config(root)
    cats = {c for c, _, _ in rb.build_plan(root, cfg, categories={"B"})}
    assert cats == {"B"}


def test_roundtrip_restores_all(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _make_fake_root(root)
    cfg = _fake_config(root)

    plan = rb.build_plan(root, cfg, categories=None)
    zip_path = rb.create_backup(
        plan, root, tmp_path / "out", include_vectors=True, timestamp="20260613-120000"
    )
    assert zip_path.exists()

    manifest = rb.read_manifest(zip_path)
    assert manifest["include_vectors"] is True
    arcs = {f["arc"] for f in manifest["files"]}
    assert "db/sqlite/session.db" in arcs
    assert "db/chroma/chroma.sqlite3" in arcs
    assert ".env" in arcs and "ws.code-workspace" in arcs
    assert not any("auth.db" in a for a in arcs)  # 不存在的库不进 manifest
    assert not any("bm25" in a for a in arcs)

    dest = tmp_path / "restored"
    dest.mkdir()
    n = rb.restore_backup(zip_path, dest, manifest)
    assert n == len(manifest["files"])
    assert (dest / ".env").read_text(encoding="utf-8") == "OPENAI_API_KEY=secret\n"
    assert (dest / "tools" / "reports" / "security" / "r1.md").read_text(encoding="utf-8") == "report"

    conn = sqlite3.connect(dest / "db" / "sqlite" / "session.db")
    rows = conn.execute("SELECT txt FROM msg").fetchall()
    conn.close()
    assert rows == [("hello",)]


def test_list_snapshots_summary(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _make_fake_root(root)
    cfg = _fake_config(root)
    out = tmp_path / "out"
    plan = rb.build_plan(root, cfg, categories={"A", "B", "E", "F", "K"})
    rb.create_backup(plan, root, out, include_vectors=False, timestamp="20260613-130000")

    snaps = rb.list_snapshots(out)
    assert len(snaps) == 1
    assert snaps[0]["timestamp"] == "20260613-130000"
    assert snaps[0]["include_vectors"] is False
    assert snaps[0]["file_count"] > 0
    assert snaps[0]["name"].startswith("agenta-backup-")


def test_list_empty_dir(tmp_path):
    assert rb.list_snapshots(tmp_path / "nope") == []


def test_validate_restore_targets_ok(tmp_path):
    root = tmp_path / "proj"
    manifest = {"files": [{"arc": ".env", "restore": ".env", "external": False}]}
    assert rb.validate_restore_targets(manifest, root) == []


def test_validate_restore_targets_rejects_traversal(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    manifest = {
        "files": [
            {"arc": "ok", "restore": "db/x.db", "external": False},
            {"arc": "bad-up", "restore": "../evil.txt", "external": False},
            {"arc": "bad-ext", "restore": "C:/Windows/x", "external": True},
        ]
    }
    bad = rb.validate_restore_targets(manifest, root)
    assert "bad-up" in bad
    assert "bad-ext" in bad
    assert "ok" not in bad


def test_validate_backup_archive_rejects_oversized_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(rb.config, "BACKUP_MAX_UPLOAD_MB", 1)
    z = tmp_path / "big.zip"
    z.write_bytes(b"x" * (2 * 1024 * 1024))
    with pytest.raises(rb.BackupArchiveError, match="备份文件过大"):
        rb.validate_backup_archive(z)


def test_validate_backup_archive_rejects_zip_bomb(tmp_path, monkeypatch):
    monkeypatch.setattr(rb.config, "BACKUP_MAX_UPLOAD_MB", 64)
    monkeypatch.setattr(rb.config, "BACKUP_MAX_UNZIP_MB", 1)
    monkeypatch.setattr(rb.config, "BACKUP_MAX_COMPRESSION_RATIO", 100)
    from zipfile import ZipFile

    z = tmp_path / "bomb.zip"
    with ZipFile(z, "w") as zf:
        zf.writestr("huge.txt", "x" * (2 * 1024 * 1024))
    with pytest.raises(rb.BackupArchiveError, match="解压后总大小"):
        rb.validate_backup_archive(z)


def test_cli_help_exits_zero():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cli" / "backup_cli.py"), "-h"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r.returncode == 0
    for kw in ("backup", "restore", "list"):
        assert kw in r.stdout
