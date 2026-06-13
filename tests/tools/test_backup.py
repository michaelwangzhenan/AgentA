"""tools/backup.py 验收：清单构建、备份/还原往返一致、SQLite 在线备份、list 摘要。"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def backup():
    path = ROOT / "tools" / "backup.py"
    spec = importlib.util.spec_from_file_location("backup_cli", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backup_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_fake_root(root: Path) -> None:
    """在临时根下造出各类待备份样本：.env / sqlite / 向量库目录树 / 报告 / 编辑器配置。"""
    (root / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    (root / ".agenta").mkdir()
    (root / ".agenta" / "config_overrides.json").write_text('{"a":1}', encoding="utf-8")

    (root / "db" / "sqlite").mkdir(parents=True)
    db = root / "db" / "sqlite" / "chat_history.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE msg (id INTEGER, txt TEXT)")
    conn.execute("INSERT INTO msg VALUES (1, 'hello')")
    conn.commit()
    conn.close()

    (root / "db" / "chroma").mkdir(parents=True)
    (root / "db" / "chroma" / "chroma.sqlite3").write_bytes(b"vectordata")

    (root / "tools" / "rag_eval").mkdir(parents=True)
    (root / "tools" / "rag_eval" / "golden.json").write_text("[]", encoding="utf-8")
    (root / "tools" / "agent_eval" / "reports").mkdir(parents=True)
    (root / "tools" / "agent_eval" / "reports" / "r1.md").write_text("report", encoding="utf-8")

    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "ws.code-workspace").write_text("{}", encoding="utf-8")


def _fake_config(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        MEMORY_DB_PATH=str(root / "db" / "sqlite" / "chat_history.db"),
        AUTH_DB_PATH=str(root / "db" / "sqlite" / "auth.db"),  # 不存在 → 应跳过
        CHROMA_DB_PATH=str(root / "db" / "chroma"),
        BM25_INDEX_DIR=str(root / "db" / "bm25"),  # 不存在 → 应跳过
    )


def test_help_exits_zero():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "backup.py"), "-h"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r.returncode == 0
    for kw in ("backup", "restore", "list"):
        assert kw in r.stdout


def test_skip_vectors_drops_c(backup, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _make_fake_root(root)
    cfg = _fake_config(root)
    with_c = [c for c, _, _ in backup.build_plan(root, cfg, skip_vectors=False)]
    without_c = [c for c, _, _ in backup.build_plan(root, cfg, skip_vectors=True)]
    assert "C" in with_c
    assert "C" not in without_c


def test_roundtrip_restores_all(backup, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _make_fake_root(root)
    cfg = _fake_config(root)

    plan = backup.build_plan(root, cfg, skip_vectors=False)
    zip_path = backup.create_backup(
        plan, root, tmp_path / "out", include_vectors=True, timestamp="20260613-120000"
    )
    assert zip_path.exists()

    manifest = backup.read_manifest(zip_path)
    assert manifest["include_vectors"] is True
    # 不存在的 auth.db / bm25 不应进 manifest
    arcs = {f["arc"] for f in manifest["files"]}
    assert "db/sqlite/chat_history.db" in arcs
    assert "db/chroma/chroma.sqlite3" in arcs
    assert ".env" in arcs and "ws.code-workspace" in arcs
    assert not any("auth.db" in a for a in arcs)
    assert not any("bm25" in a for a in arcs)

    # 还原到全新空目录，逐一比对内容
    dest = tmp_path / "restored"
    dest.mkdir()
    n = backup.restore_backup(zip_path, dest, manifest)
    assert n == len(manifest["files"])
    assert (dest / ".env").read_text(encoding="utf-8") == "OPENAI_API_KEY=secret\n"
    assert (dest / "tools" / "agent_eval" / "reports" / "r1.md").read_text(encoding="utf-8") == "report"

    # SQLite 在线备份还原后可正常读取
    conn = sqlite3.connect(dest / "db" / "sqlite" / "chat_history.db")
    rows = conn.execute("SELECT txt FROM msg").fetchall()
    conn.close()
    assert rows == [("hello",)]


def test_list_snapshots_summary(backup, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _make_fake_root(root)
    cfg = _fake_config(root)
    out = tmp_path / "out"
    plan = backup.build_plan(root, cfg, skip_vectors=True)
    backup.create_backup(plan, root, out, include_vectors=False, timestamp="20260613-130000")

    snaps = backup.list_snapshots(out)
    assert len(snaps) == 1
    assert snaps[0]["timestamp"] == "20260613-130000"
    assert snaps[0]["include_vectors"] is False
    assert snaps[0]["file_count"] > 0


def test_list_empty_dir(backup, tmp_path):
    assert backup.list_snapshots(tmp_path / "nope") == []
