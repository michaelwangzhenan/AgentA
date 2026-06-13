"""SQLite 维护（破坏性）：保留期清理 / 按 user_id 清数据 / VACUUM。

与只读的 `db_inspect` 分开——本模块用**可写**连接。所有操作仅由 admin 触发，
且前端会先调 *_preview 看「将删多少」再确认。复用 `db_inspect` 的库路径解析。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import src.db_inspect as inspect

# 事件/日志类表：按 created_at（epoch）删 N 天前的行。均在 usage.db。
_EVENT_TABLES = ("usage_events", "agent_traces", "cache_lookups", "saving_events", "security_events")

# 按 user_id 清数据的级联计划：子表在前，父表在后；每条 (table, where)。
# where 里每个 ? 都绑定 user_id。users 账号行本身不删（走用户管理入口）。
_PURGE_PLAN: list[tuple[str, list[tuple[str, str]]]] = [
    ("chat_history", [
        ("messages", "session_id IN (SELECT session_id FROM sessions WHERE user_id=?)"),
        ("sessions", "user_id=?"),
    ]),
    ("usage", [
        ("trace_spans", "trace_id IN (SELECT trace_id FROM agent_traces WHERE user_id=?)"),
        ("usage_events", "user_id=?"),
        ("agent_traces", "user_id=?"),
        ("cache_lookups", "user_id=?"),
        ("saving_events", "user_id=?"),
        ("security_events", "user_id=?"),
    ]),
    ("learning", [
        ("learning_tasks", "plan_id IN (SELECT id FROM learning_plans WHERE user_id=?)"),
        ("learning_plans", "user_id=?"),
    ]),
    ("quiz", [
        ("quiz_questions", "quiz_set_id IN (SELECT id FROM quiz_sets WHERE user_id=?)"),
        ("quiz_sets", "user_id=?"),
    ]),
    ("user_memory", [("user_memories", "user_id=?")]),
    ("srs", [("srs_cards", "user_id=?")]),
    ("auth", [
        ("auth_sessions", "user_id=?"),
        ("user_settings", "user_id=?"),
        ("user_rules", "user_id=?"),
    ]),
]


def _db_path(db_key: str) -> Path | None:
    return inspect._resolve_db_key(db_key)  # noqa: SLF001 — 同包复用


def _rw_conn(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(path), timeout=5)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _count(conn: sqlite3.Connection, table: str, where: str, params: list) -> int:
    q = f'SELECT COUNT(*) FROM "{table}" WHERE {where}'
    return int(conn.execute(q, params).fetchone()[0])


# ── 保留期清理 ────────────────────────────────────────────────────────────────

def _prune_specs(cutoff: int, now: int) -> list[tuple[str, str, str, list]]:
    """返回 (db_key, table, where, params)；查询 / 删除共用同一组条件。"""
    specs: list[tuple[str, str, str, list]] = []
    for t in _EVENT_TABLES:
        specs.append(("usage", t, "created_at < ?", [cutoff]))
    # trace_spans：删掉所属 trace 已不在保留窗内（或本就孤儿）的 span
    specs.append((
        "usage", "trace_spans",
        "trace_id NOT IN (SELECT trace_id FROM agent_traces WHERE created_at >= ?)", [cutoff],
    ))
    # 过期登录会话
    specs.append(("auth", "auth_sessions", "expires_at < ?", [now]))
    return specs


def _prune(days: int, *, execute: bool) -> dict:
    if days < 1:
        return {"error": "保留天数至少为 1"}
    now = int(time.time())
    cutoff = now - days * 86400
    items: list[dict] = []
    total = 0
    conns: dict[str, sqlite3.Connection] = {}
    try:
        for db_key, table, where, params in _prune_specs(cutoff, now):
            path = _db_path(db_key)
            if path is None or not path.exists():
                continue
            conn = conns.get(db_key)
            if conn is None:
                conn = conns[db_key] = _rw_conn(path)
            if table not in _tables(conn):
                continue
            n = _count(conn, table, where, params)
            if execute and n:
                conn.execute(f'DELETE FROM "{table}" WHERE {where}', params)
            items.append({"db": db_key, "table": table, "count": n})
            total += n
        if execute:
            for c in conns.values():
                c.commit()
    finally:
        for c in conns.values():
            c.close()
    return {"days": days, "cutoff": cutoff, "items": items, "total": total, "executed": execute}


def prune_preview(days: int) -> dict:
    return _prune(days, execute=False)


def prune(days: int) -> dict:
    return _prune(days, execute=True)


# ── 按 user_id 清数据 ─────────────────────────────────────────────────────────

def _purge_user(user_id: int, *, execute: bool) -> dict:
    items: list[dict] = []
    total = 0
    for db_key, steps in _PURGE_PLAN:
        path = _db_path(db_key)
        if path is None or not path.exists():
            continue
        conn = _rw_conn(path)
        try:
            present = _tables(conn)
            for table, where in steps:
                if table not in present:
                    continue
                params = [user_id] * where.count("?")
                n = _count(conn, table, where, params)
                if execute and n:
                    conn.execute(f'DELETE FROM "{table}" WHERE {where}', params)
                items.append({"db": db_key, "table": table, "count": n})
                total += n
            if execute:
                conn.commit()
        finally:
            conn.close()
    return {"user_id": user_id, "items": items, "total": total, "executed": execute}


def purge_user_preview(user_id: int) -> dict:
    return _purge_user(user_id, execute=False)


def purge_user(user_id: int) -> dict:
    return _purge_user(user_id, execute=True)


# ── VACUUM ────────────────────────────────────────────────────────────────────

def vacuum(db_key: str | None = None) -> dict:
    """对指定库或全部库执行 VACUUM 回收空间；返回每库结果。"""
    targets: list[tuple[str, Path]]
    if db_key:
        path = _db_path(db_key)
        targets = [(db_key, path)] if path else []
    else:
        targets = [(inspect._db_key(p), p) for _label, p in inspect.sqlite_db_files()]  # noqa: SLF001

    results: list[dict] = []
    for key, path in targets:
        if path is None or not path.exists():
            results.append({"db": key, "ok": False, "error": "文件不存在"})
            continue
        before = path.stat().st_size
        conn = _rw_conn(path)
        try:
            conn.execute("VACUUM")
            conn.commit()
            after = path.stat().st_size
            results.append({"db": key, "ok": True, "freed_bytes": max(0, before - after), "size": after})
        except Exception as e:
            results.append({"db": key, "ok": False, "error": f"{type(e).__name__}: {e}"})
        finally:
            conn.close()
    return {"results": results}
