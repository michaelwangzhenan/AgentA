"""src/db_maintain.py UT：保留期清理 / 按 user_id 清数据 / VACUUM（临时库）。"""
from __future__ import annotations

import sqlite3
import time

import pytest

import src.db_inspect as inspect
import src.db_maintain as maintain


def _mk(path, ddl, rows):
    conn = sqlite3.connect(path)
    for stmt in ddl:
        conn.execute(stmt)
    for q, params in rows:
        conn.execute(q, params)
    conn.commit()
    conn.close()


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    now = int(time.time())
    old = now - 100 * 86400
    recent = now - 1 * 86400
    db = tmp_path / "usage.db"
    _mk(
        db,
        [
            "CREATE TABLE usage_events (id INTEGER, user_id INTEGER, created_at INTEGER)",
            "CREATE TABLE agent_traces (id INTEGER, trace_id TEXT, user_id INTEGER, created_at INTEGER)",
            "CREATE TABLE trace_spans (id INTEGER, trace_id TEXT)",
            "CREATE TABLE cache_lookups (id INTEGER, user_id INTEGER, created_at INTEGER)",
            "CREATE TABLE saving_events (id INTEGER, user_id INTEGER, created_at INTEGER)",
            "CREATE TABLE security_events (id INTEGER, user_id INTEGER, created_at INTEGER)",
        ],
        [
            ("INSERT INTO usage_events VALUES (?,?,?)", (1, 1, old)),
            ("INSERT INTO usage_events VALUES (?,?,?)", (2, 4, recent)),
            ("INSERT INTO agent_traces VALUES (?,?,?,?)", (1, "tA", 1, old)),
            ("INSERT INTO agent_traces VALUES (?,?,?,?)", (2, "tB", 4, recent)),
            ("INSERT INTO trace_spans VALUES (?,?)", (1, "tA")),  # 属于将被清的旧 trace
            ("INSERT INTO trace_spans VALUES (?,?)", (2, "tB")),  # 属于保留 trace
            ("INSERT INTO cache_lookups VALUES (?,?,?)", (1, 1, old)),
            ("INSERT INTO saving_events VALUES (?,?,?)", (1, 1, old)),
            ("INSERT INTO security_events VALUES (?,?,?)", (1, 1, old)),
        ],
    )
    monkeypatch.setattr(inspect, "sqlite_db_files", lambda: [("USAGE_DB_PATH", db)])
    return db


def test_prune_preview_counts_old(usage_db):
    out = maintain.prune_preview(30)
    by = {(i["table"]): i["count"] for i in out["items"]}
    assert by["usage_events"] == 1
    assert by["agent_traces"] == 1
    assert by["trace_spans"] == 1  # tA 的 span
    assert out["executed"] is False


def test_prune_executes_and_keeps_recent(usage_db):
    maintain.prune(30)
    conn = sqlite3.connect(usage_db)
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1
    assert conn.execute("SELECT id FROM usage_events").fetchone()[0] == 2  # 保留 recent
    assert conn.execute("SELECT COUNT(*) FROM trace_spans").fetchone()[0] == 1  # 只剩 tB
    conn.close()


def test_prune_rejects_zero_days(usage_db):
    out = maintain.prune_preview(0)
    assert out.get("error")


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    db = tmp_path / "chat_history.db"
    _mk(
        db,
        [
            "CREATE TABLE sessions (session_id TEXT, user_id INTEGER)",
            "CREATE TABLE messages (id INTEGER, session_id TEXT)",
        ],
        [
            ("INSERT INTO sessions VALUES (?,?)", ("s1", 1)),
            ("INSERT INTO sessions VALUES (?,?)", ("s2", 4)),
            ("INSERT INTO messages VALUES (?,?)", (1, "s1")),
            ("INSERT INTO messages VALUES (?,?)", (2, "s1")),
            ("INSERT INTO messages VALUES (?,?)", (3, "s2")),
        ],
    )
    monkeypatch.setattr(inspect, "sqlite_db_files", lambda: [("MEMORY_DB_PATH", db)])
    return db


def test_purge_user_preview_lists_rows(chat_db):
    out = maintain.purge_user_preview(1)
    sess = next(t for t in out["tables"] if t["table"] == "sessions")
    assert sess["total"] == 1
    assert sess["child"] == "messages"  # 子表跟随级联，仅标注
    assert "rowid" in sess["columns"]
    assert len(sess["rows"]) == 1
    # messages 子表不单列
    assert all(t["table"] != "messages" for t in out["tables"])


def test_purge_user_all_cascade(chat_db):
    maintain.purge_user(1, [{"db": "chat_history", "table": "sessions", "all": True, "rowids": []}])
    conn = sqlite3.connect(chat_db)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1  # s2 留存
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1  # s2 的消息留存
    conn.close()


def test_purge_user_selected_rowids_cascade(chat_db):
    # 只删 s1 这一行（按 rowid），其 messages 级联删；s2 不动
    pre = maintain.purge_user_preview(1)
    sess = next(t for t in pre["tables"] if t["table"] == "sessions")
    rid = sess["rows"][0]["rowid"]
    out = maintain.purge_user(1, [{"db": "chat_history", "table": "sessions", "all": False, "rowids": [rid]}])
    by = {i["table"]: i["deleted"] for i in out["items"]}
    assert by["sessions"] == 1
    assert by["messages"] == 2
    conn = sqlite3.connect(chat_db)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    conn.close()


def test_vacuum_runs(chat_db):
    out = maintain.vacuum("chat_history")
    assert out["results"][0]["ok"] is True
