"""SQLite 维护（破坏性）：保留期清理 / 按 user_id 清数据 / VACUUM。

与只读的 `db_inspect` 分开——本模块用**可写**连接。所有操作仅由 admin 触发，
且前端会先调 *_preview 看「将删多少」再确认。复用 `db_inspect` 的库路径解析。
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import time
from pathlib import Path

import src.config as config
import src.services.db_inspect as inspect

# Chroma 持久化根下 segment 目录名是 UUID；用它识别段目录，避开 bm25_*.pkl / chroma.sqlite3 等
_CHROMA_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# 事件/日志类表：按 created_at（epoch）删 N 天前的行。均在 usage.db。
_EVENT_TABLES = ("usage_events", "agent_traces", "cache_lookups", "saving_events", "security_events")

# 按 user_id 清数据的级联计划：子表在前，父表在后；每条 (table, where)。
# 按 user_id 清理的主要表（直接含 user_id 列，可逐行勾选）。
# child=(子表, 子表外键, 父表关联键)：选中的父行删除前先级联删其子行；None 表示无子表。
# users 账号行本身不在内（删账号走用户管理入口）。
_PURGE_TABLES: list[tuple[str, str, tuple[str, str, str] | None]] = [
    ("session", "sessions", ("messages", "session_id", "session_id")),
    ("usage", "usage_events", None),
    ("usage", "agent_traces", ("trace_spans", "trace_id", "trace_id")),
    ("usage", "cache_lookups", None),
    ("usage", "saving_events", None),
    ("usage", "security_events", None),
    ("learning", "learning_plans", ("learning_tasks", "plan_id", "id")),
    ("quiz", "quiz_sets", ("quiz_questions", "quiz_set_id", "id")),
    ("user_memory", "user_memories", None),
    ("srs", "srs_cards", None),
    ("auth", "auth_sessions", None),
    ("auth", "user_settings", None),
    ("auth", "user_rules", None),
]

# 预览每表最多列出的行数（超出时 truncated=True，但「全选」仍可全删）
PURGE_PREVIEW_CAP = 500


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

def purge_user_preview(user_id: int) -> dict:
    """列出该 user_id 在各主要表里将删的行（带 rowid + 各列，敏感列脱敏），每表上限 cap。

    子表（messages 等）不单列——它们会跟随被选中的父行级联删除，仅在表头标注。
    """
    tables: list[dict] = []
    conns: dict[str, sqlite3.Connection] = {}
    try:
        for db_key, table, child in _PURGE_TABLES:
            path = _db_path(db_key)
            if path is None or not path.exists():
                continue
            conn = conns.get(db_key)
            if conn is None:
                conn = conns[db_key] = inspect._ro_connect(path)  # noqa: SLF001
            if table not in _tables(conn):
                continue
            total = _count(conn, table, "user_id=?", [user_id])
            if total == 0:
                continue
            cur = conn.execute(
                f'SELECT rowid, * FROM "{table}" WHERE user_id=? LIMIT ?', [user_id, PURGE_PREVIEW_CAP]
            )
            cols = [c[0] for c in cur.description]
            sensitive = {c for c in cols if inspect.is_sensitive_column(c)}
            rows = []
            for raw in cur.fetchall():
                rows.append({n: ("***" if n in sensitive else v) for n, v in zip(cols, raw)})
            tables.append({
                "db": db_key, "table": table, "total": total,
                "truncated": total > PURGE_PREVIEW_CAP, "columns": cols, "rows": rows,
                "child": child[0] if child else None,
            })
    finally:
        for c in conns.values():
            c.close()
    return {"user_id": user_id, "cap": PURGE_PREVIEW_CAP, "tables": tables}


def purge_user(user_id: int, selections: list[dict]) -> dict:
    """按选择删除：每项 {db, table, all, rowids}。all=True 删该表该用户全部行；
    否则只删选中 rowid。父表删除前先级联删其子表行（按全量或选中 rowid 推导）。
    """
    whitelist = {(db, table): child for db, table, child in _PURGE_TABLES}
    items: list[dict] = []
    total = 0
    conns: dict[str, sqlite3.Connection] = {}
    try:
        for sel in selections:
            db_key = sel.get("db")
            table = sel.get("table")
            child = whitelist.get((db_key, table))
            if (db_key, table) not in whitelist:
                continue
            path = _db_path(db_key)
            if path is None or not path.exists():
                continue
            conn = conns.get(db_key)
            if conn is None:
                conn = conns[db_key] = _rw_conn(path)
            present = _tables(conn)
            if table not in present:
                continue
            take_all = bool(sel.get("all"))
            rowids = [int(x) for x in (sel.get("rowids") or [])]
            if not take_all and not rowids:
                continue

            if child is not None:
                child_table, child_fk, parent_key = child
                if child_table in present:
                    if take_all:
                        sub = f'SELECT "{parent_key}" FROM "{table}" WHERE user_id=?'
                        cur = conn.execute(f'DELETE FROM "{child_table}" WHERE "{child_fk}" IN ({sub})', [user_id])
                    else:
                        ph = ",".join("?" * len(rowids))
                        sub = f'SELECT "{parent_key}" FROM "{table}" WHERE rowid IN ({ph})'
                        cur = conn.execute(f'DELETE FROM "{child_table}" WHERE "{child_fk}" IN ({sub})', rowids)
                    if cur.rowcount:
                        items.append({"db": db_key, "table": child_table, "deleted": cur.rowcount})
                        total += cur.rowcount

            if take_all:
                cur = conn.execute(f'DELETE FROM "{table}" WHERE user_id=?', [user_id])
            else:
                ph = ",".join("?" * len(rowids))
                cur = conn.execute(f'DELETE FROM "{table}" WHERE rowid IN ({ph})', rowids)
            items.append({"db": db_key, "table": table, "deleted": cur.rowcount})
            total += cur.rowcount
        for c in conns.values():
            c.commit()
    finally:
        for c in conns.values():
            c.close()
    return {"user_id": user_id, "items": items, "total": total, "executed": True}


# ── VACUUM ────────────────────────────────────────────────────────────────────

# ── Chroma 孤儿 segment 清理 ──────────────────────────────────────────────────
# delete_collection() 只删 chroma.sqlite3 catalog，不 unlink 磁盘上的 <uuid>/ 段目录。
# 删库 / 清空后这些不再被任何活跃 collection 引用的目录就成了孤儿，纯占磁盘。

def _chroma_root() -> Path:
    return Path(config.CHROMA_DB_PATH).resolve()


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def _live_vector_segment_ids(root: Path) -> set[str] | None:
    """从 chroma.sqlite3 反查活跃 VECTOR 段 UUID。

    安全策略：sqlite 不存在或读失败返回 None，调用方据此放弃清理，绝不"读不到 → 全删"。
    """
    sqlite_path = root / "chroma.sqlite3"
    if not sqlite_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        try:
            return {r[0] for r in conn.execute("SELECT id FROM segments WHERE scope = 'VECTOR'")}
        finally:
            conn.close()
    except Exception:
        return None


def _orphan_dirs(root: Path) -> list[Path] | None:
    """列出磁盘上不再被任何活跃 VECTOR 段引用的 UUID 目录；读不到 sqlite 返回 None。"""
    if not root.is_dir():
        return []
    live = _live_vector_segment_ids(root)
    if live is None:
        return None
    return [
        p for p in root.iterdir()
        if p.is_dir() and _CHROMA_UUID_RE.match(p.name) and p.name not in live
    ]


def orphan_segments_preview() -> dict:
    """预览将清理的孤儿 segment 目录（UUID + 占用字节）。available=False 表示无法安全判定。"""
    root = _chroma_root()
    dirs = _orphan_dirs(root)
    if dirs is None:
        return {"available": False, "root": str(root), "count": 0, "total_bytes": 0, "items": []}
    items = [{"uuid": d.name, "bytes": _dir_size(d)} for d in dirs]
    return {
        "available": True,
        "root": str(root),
        "count": len(items),
        "total_bytes": sum(i["bytes"] for i in items),
        "items": items,
    }


def cleanup_orphan_segments() -> dict:
    """物理删除孤儿 segment 目录；返回已删 UUID / 回收字节 / 失败项（被占用等）。"""
    root = _chroma_root()
    dirs = _orphan_dirs(root)
    if dirs is None:
        return {"available": False, "removed": [], "freed_bytes": 0, "failed": []}
    removed: list[str] = []
    failed: list[dict] = []
    freed = 0
    for d in dirs:
        size = _dir_size(d)
        try:
            shutil.rmtree(d)
            removed.append(d.name)
            freed += size
        except OSError as e:
            failed.append({"uuid": d.name, "error": f"{type(e).__name__}: {e}"})
    return {"available": True, "removed": removed, "freed_bytes": freed, "failed": failed}


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


# ── BM25 修复 ─────────────────────────────────────────────────────────────

def repair_preview() -> dict:
    """扫描全部 BM25 索引侧车健康状态。"""
    items: list[dict] = []
    for path in inspect._bm25_files():  # noqa: SLF001
        items.append(inspect.bm25_sidecar_health(path))
    need = sum(1 for i in items if i.get("needs_repair"))
    return {"indexes": items, "needs_repair": need}


def repair_run(collections: list[str] | None = None) -> dict:
    """从 pkl 重建 manifest + chunks.jsonl；默认只修复 needs_repair 的索引。"""
    from src.rag.bm25_index import rewrite_index_sidecars

    preview = repair_preview()
    targets = [
        i["collection"] for i in preview["indexes"]
        if i.get("needs_repair") and i.get("pkl_exists")
    ]
    if collections is not None:
        allowed = set(collections)
        targets = [c for c in targets if c in allowed]
    results: list[dict] = []
    for coll in targets:
        try:
            out = rewrite_index_sidecars(coll)
            results.append(out)
        except Exception as e:
            results.append({"collection": coll, "ok": False, "error": f"{type(e).__name__}: {e}"})
    ok = sum(1 for r in results if r.get("ok"))
    return {"repaired": ok, "failed": len(results) - ok, "items": results}
