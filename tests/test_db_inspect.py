"""src/db_inspect.py 纯逻辑 UT：脱敏、表分页、键派生（临时 sqlite，不碰真实库）。"""
from __future__ import annotations

import sqlite3

import pytest

import src.db_inspect as inspect


def test_is_sensitive_column():
    assert inspect.is_sensitive_column("password_hash")
    assert inspect.is_sensitive_column("API_KEY")
    assert inspect.is_sensitive_column("token")
    assert not inspect.is_sensitive_column("username")
    assert not inspect.is_sensitive_column("created_at")


def test_truncate_boundaries():
    assert inspect.truncate("ab", 10) == "ab"
    assert inspect.truncate("x" * 100, 10).endswith("...")
    assert inspect.truncate("x" * 100, 2) == "xx"
    assert inspect.truncate(None, 10) == ""  # type: ignore[arg-type]


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER, username TEXT, password_hash TEXT)")
    conn.executemany(
        "INSERT INTO users VALUES (?,?,?)",
        [(1, "alice", "secrethash1"), (2, "bob", "secrethash2")],
    )
    conn.commit()
    conn.close()


def test_sqlite_table_rows_masks_sensitive(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    _make_db(db)
    monkeypatch.setattr(inspect, "sqlite_db_files", lambda: [("AUTH_DB_PATH", db)])

    out = inspect.sqlite_table_rows("auth", "users", limit=10, offset=0)
    assert out is not None
    assert out["total"] == 2
    assert "password_hash" in out["masked_columns"]
    assert all(row["password_hash"] == "***" for row in out["rows"])
    # 非敏感列照常返回
    assert {r["username"] for r in out["rows"]} == {"alice", "bob"}


def test_sqlite_table_rows_pagination(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    _make_db(db)
    monkeypatch.setattr(inspect, "sqlite_db_files", lambda: [("AUTH_DB_PATH", db)])

    out = inspect.sqlite_table_rows("auth", "users", limit=1, offset=1)
    assert out["total"] == 2
    assert len(out["rows"]) == 1


def test_sqlite_table_rows_bad_table(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    _make_db(db)
    monkeypatch.setattr(inspect, "sqlite_db_files", lambda: [("AUTH_DB_PATH", db)])

    out = inspect.sqlite_table_rows("auth", "nonexist", limit=10, offset=0)
    assert out is not None and out.get("error")


def test_sqlite_table_rows_unknown_db(tmp_path, monkeypatch):
    monkeypatch.setattr(inspect, "sqlite_db_files", lambda: [])
    assert inspect.sqlite_table_rows("ghost", "t", limit=10, offset=0) is None


def _rows():
    return [
        {"id": "a", "document": "hello world", "metadata": {"filename": "alpha.md", "ingested_at": 100}},
        {"id": "b", "document": "foo bar", "metadata": {"filename": "Beta.txt", "ingested_at": 300}},
        {"id": "c", "document": "baz", "metadata": {"filename": "gamma.md"}},  # 无 ingested_at
    ]


def test_filter_sort_filename_substring_ci():
    out = inspect.filter_sort_rows(_rows(), filename_q="beta")
    assert [r["id"] for r in out] == ["b"]  # 大小写不敏感


def test_filter_sort_body_substring_ci():
    out = inspect.filter_sort_rows(_rows(), body_q="WORLD")
    assert [r["id"] for r in out] == ["a"]


def test_filter_sort_ts_range_excludes_missing():
    # [200, 400] 命中 b(300)，a(100) 超下界、c 无 ingested_at 一律排除
    out = inspect.filter_sort_rows(_rows(), ts_from=200, ts_to=400)
    assert [r["id"] for r in out] == ["b"]


def test_filter_sort_sort_ingested_desc():
    out = inspect.filter_sort_rows(_rows(), sort_by="ingested_at", desc=True)
    # 有时间的按降序在前，缺失的（-inf）落最后
    assert [r["id"] for r in out] == ["b", "a", "c"]


def test_filter_sort_sort_filename_asc():
    out = inspect.filter_sort_rows(_rows(), sort_by="filename", desc=False)
    assert [r["id"] for r in out] == ["b", "a", "c"]  # Beta.txt < alpha.md < gamma.md（按原串）


def test_filter_sort_noop_keeps_order():
    out = inspect.filter_sort_rows(_rows())
    assert [r["id"] for r in out] == ["a", "b", "c"]


def test_sqlite_databases_shape(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    _make_db(db)
    monkeypatch.setattr(inspect, "sqlite_db_files", lambda: [("AUTH_DB_PATH", db)])

    out = inspect.sqlite_databases()["databases"]
    assert len(out) == 1
    assert out[0]["key"] == "auth"
    assert out[0]["exists"] is True
    assert {t["name"]: t["rows"] for t in out[0]["tables"]} == {"users": 2}
