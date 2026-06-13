"""tools/db_show.py 验收：帮助、纯函数与 SQLite 表统计（临时库，不依赖真实数据）。"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def db_show():
    path = ROOT / "tools" / "db_show.py"
    spec = importlib.util.spec_from_file_location("db_show_cli", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_show_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_help_exits_zero():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "db_show.py"), "-h"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r.returncode == 0
    for kw in ("summary", "chroma", "sqlite", "bm25"):
        assert kw in r.stdout


def test_truncate(db_show):
    t = db_show._truncate
    assert t("ab", 10) == "ab"
    assert t("x" * 100, 10).endswith("...")
    assert len(t("x" * 100, 10)) == 10
    assert t("x" * 100, 2) == "xx"


def test_metadata_preview(db_show):
    p = db_show._metadata_preview
    assert p(None) == "{}"
    assert p({}) == "{}"
    out = p({"a": 1, "b": "x"})
    assert "a=1" in out and "b=" in out
    big = {f"k{i}": i for i in range(db_show._META_KEYS_MAX + 5)}
    assert "键)" in p(big)


def test_sqlite_table_row_counts(db_show, tmp_path):
    db = tmp_path / "sample.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE foo (id INTEGER)")
    conn.execute("CREATE TABLE bar (name TEXT)")
    conn.executemany("INSERT INTO foo VALUES (?)", [(1,), (2,), (3,)])
    conn.execute("INSERT INTO bar VALUES ('a')")
    conn.commit()
    conn.close()
    assert dict(db_show._sqlite_table_row_counts(db)) == {"foo": 3, "bar": 1}


def test_sqlite_skips_internal_tables(db_show, tmp_path):
    db = tmp_path / "auto.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('x')")
    conn.commit()
    conn.close()
    names = [name for name, _ in db_show._sqlite_table_row_counts(db)]
    assert "t" in names
    assert all(not n.startswith("sqlite_") for n in names)
