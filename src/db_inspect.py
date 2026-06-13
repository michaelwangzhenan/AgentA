"""只读巡检 Chroma / SQLite / BM25 的公共读逻辑。

CLI（`tools/db_show.py`）与 API（`/admin/db/*`）共用本模块，保证两边口径一致。
原则：**只读、绝不写库**；遇坏库 / 坏向量段降级为返回 `error` 字段，不向上抛断，
让调用方仍能看到其余正常数据。
"""
from __future__ import annotations

import pickle
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import src.config as config

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DOC_PREVIEW_MAX = 400

# 敏感列名片段：SQLite 行级展示时命中即脱敏（值替换为 ***），避免密钥/口令外泄。
_SENSITIVE_COL_HINTS = (
    "password", "passwd", "pwd", "secret", "token",
    "api_key", "apikey", "key_hash", "hash", "salt",
)


def _resolve(raw: str | Path) -> Path:
    """相对路径按工程根解析为绝对路径。"""
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (_PROJECT_ROOT / p).resolve()


def truncate(s: str, max_len: int = DOC_PREVIEW_MAX) -> str:
    """正文预览截断；统一换行符。max_len < 3 时退化为纯截断避免负索引。"""
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(s) <= max_len:
        return s
    if max_len < 3:
        return s[:max_len]
    return s[: max_len - 3] + "..."


def is_sensitive_column(name: str) -> bool:
    low = (name or "").lower()
    return any(h in low for h in _SENSITIVE_COL_HINTS)


# ── 路径 ──────────────────────────────────────────────────────────────────────

def chroma_root() -> Path:
    return _resolve(config.CHROMA_DB_PATH)


def bm25_dir() -> Path:
    return _resolve(config.BM25_INDEX_DIR or config.CHROMA_DB_PATH)


def sqlite_db_files() -> list[tuple[str, Path]]:
    """(来源标签, 绝对路径) 列表：配置里各 *_DB_PATH + db/sqlite/*.db，按路径去重。"""
    seen: set[str] = set()
    out: list[tuple[str, Path]] = []
    for key in (
        "MEMORY_DB_PATH", "AUTH_DB_PATH", "USAGE_DB_PATH", "RAG_GOLDEN_DB_PATH",
        "USER_MEMORY_DB_PATH", "LEARNING_PLAN_DB_PATH", "QUIZ_DB_PATH", "SRS_DB_PATH",
    ):
        raw = getattr(config, key, None)
        if not raw:
            continue
        rp = _resolve(raw)
        k = str(rp).lower()
        if k in seen:
            continue
        seen.add(k)
        out.append((key, rp))

    glob_dir = _resolve(Path("db") / "sqlite")
    if glob_dir.is_dir():
        for f in sorted(glob_dir.glob("*.db")):
            rp = f.resolve()
            k = str(rp).lower()
            if k in seen:
                continue
            seen.add(k)
            out.append((f"glob:{glob_dir.name}", rp))
    return out


# ── Chroma ────────────────────────────────────────────────────────────────────

def _chroma_client():
    import chromadb

    return chromadb.PersistentClient(path=str(chroma_root()))


def _chroma_dim(col) -> int | None:
    """取一条向量看维度；坏段（如取 embeddings 报 Error finding id）降级为 None。"""
    try:
        g = col.get(limit=1, include=["embeddings"])
        embs = g.get("embeddings")
        if embs is not None and len(embs) > 0:
            return len(embs[0])
    except Exception:
        return None
    return None


def chroma_collections() -> dict:
    """L1：全部 collection 的名称 / 条数 / 向量维度 / 距离空间。"""
    try:
        client = _chroma_client()
    except Exception as e:
        return {"root": str(chroma_root()), "collections": [], "error": f"{type(e).__name__}: {e}"}
    items: list[dict] = []
    for meta in sorted(client.list_collections(), key=lambda c: c.name):
        row: dict = {"name": meta.name, "space": (meta.metadata or {}).get("hnsw:space")}
        try:
            col = client.get_collection(meta.name)
            row["count"] = col.count()
            row["dim"] = _chroma_dim(col)
        except Exception as e:
            row["count"] = None
            row["dim"] = None
            row["error"] = f"{type(e).__name__}: {e}"
        items.append(row)
    return {"root": str(chroma_root()), "collections": items}


# 过滤/排序时一次性拉取的硬上限：避免超大 collection 把整库正文拉进内存卡死。
# 正文/时间段过滤已在 Chroma 服务端 where 完成，这里只对预过滤后的候选集再做
# 文件名模糊 + 排序；候选超过 cap 则截断并在响应里标 truncated。
CHROMA_SCAN_CAP = 20000


def _rows_from_get(got: dict) -> list[dict]:
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    rows = []
    for i, _id in enumerate(ids):
        rows.append({
            "id": _id,
            "document": (docs[i] if i < len(docs) else "") or "",
            "metadata": metas[i] if i < len(metas) else None,
        })
    return rows


def _chroma_item_view(r: dict) -> dict:
    doc = r["document"] or ""
    return {"id": r["id"], "preview": truncate(doc), "doc_len": len(doc), "metadata": r["metadata"]}


def filter_sort_rows(
    rows: list[dict],
    *,
    filename_q: str | None = None,
    body_q: str | None = None,
    ts_from: int | None = None,
    ts_to: int | None = None,
    sort_by: str | None = None,
    desc: bool = False,
) -> list[dict]:
    """纯函数：对候选行（含 id/document/metadata）做过滤 + 排序，便于 UT。

    BM25 全量在内存，全部条件走这里；Chroma 的正文/时间段已在服务端 where 处理，
    只把 filename + sort 传进来即可（body_q/ts_* 留空）。
    """
    out = rows
    if filename_q:
        q = filename_q.lower()
        out = [r for r in out if q in str((r["metadata"] or {}).get("filename") or "").lower()]
    if body_q:
        q = body_q.lower()
        out = [r for r in out if q in (r.get("document") or "").lower()]
    if ts_from is not None or ts_to is not None:
        def _in_range(r: dict) -> bool:
            v = (r["metadata"] or {}).get("ingested_at")
            if v is None:
                return False
            v = float(v)
            if ts_from is not None and v < ts_from:
                return False
            if ts_to is not None and v > ts_to:
                return False
            return True
        out = [r for r in out if _in_range(r)]
    if sort_by in ("filename", "ingested_at"):
        def _key(r: dict):
            m = r["metadata"] or {}
            if sort_by == "filename":
                return (str(m.get("filename") or ""),)
            v = m.get("ingested_at")
            return (float(v) if v is not None else float("-inf"),)
        out = sorted(out, key=_key, reverse=desc)
    return out


def chroma_items(
    name: str,
    limit: int = 50,
    offset: int = 0,
    *,
    filename_q: str | None = None,
    body_q: str | None = None,
    ts_from: int | None = None,
    ts_to: int | None = None,
    sort_by: str | None = None,
    desc: bool = False,
) -> dict:
    """L2：某 collection 条目分页（id + 正文摘要 + metadata）。不取 embeddings。

    无过滤/排序时走 Chroma 原生分页（轻量）；有过滤/排序时：正文模糊走 where_document
    `$contains`、入库时间段走 where 数值范围（均服务端），取回 ≤ CHROMA_SCAN_CAP 条候选后，
    在内存做文件名模糊 + 排序 + 分页；候选触顶则 truncated=True。
    """
    client = _chroma_client()
    col = client.get_collection(name)  # 不存在会抛，由路由转 404

    has_server_filter = bool(body_q) or ts_from is not None or ts_to is not None
    needs_scan = bool(filename_q) or sort_by in ("filename", "ingested_at")

    # 无过滤无排序：原生分页，total 直接用 count（最省）
    if not has_server_filter and not needs_scan:
        total = col.count()
        try:
            got = col.get(limit=limit, offset=offset, include=["documents", "metadatas"])
        except Exception as e:
            return {"name": name, "total": total, "items": [], "truncated": False, "error": f"{type(e).__name__}: {e}"}
        items = [_chroma_item_view(r) for r in _rows_from_get(got)]
        return {"name": name, "total": total, "items": items, "truncated": False}

    # 有过滤/排序：服务端预过滤（正文/时间段）取回 ≤ cap 候选，内存做文件名+排序+分页
    # Chroma 每个 where 表达式只允许一个操作符，范围要用 $and 拆成两个单操作符子句
    where: dict | None = None
    clauses: list[dict] = []
    if ts_from is not None:
        clauses.append({"ingested_at": {"$gte": ts_from}})
    if ts_to is not None:
        clauses.append({"ingested_at": {"$lte": ts_to}})
    if len(clauses) == 1:
        where = clauses[0]
    elif len(clauses) >= 2:
        where = {"$and": clauses}
    where_document = {"$contains": body_q} if body_q else None

    get_kwargs: dict = {"include": ["documents", "metadatas"], "limit": CHROMA_SCAN_CAP}
    if where is not None:
        get_kwargs["where"] = where
    if where_document is not None:
        get_kwargs["where_document"] = where_document
    try:
        got = col.get(**get_kwargs)
    except Exception as e:
        return {"name": name, "total": 0, "items": [], "truncated": False, "error": f"{type(e).__name__}: {e}"}

    rows = _rows_from_get(got)
    truncated = len(rows) >= CHROMA_SCAN_CAP
    rows = filter_sort_rows(rows, filename_q=filename_q, sort_by=sort_by, desc=desc)
    total = len(rows)
    page = rows[offset: offset + limit]
    items = [_chroma_item_view(r) for r in page]
    return {"name": name, "total": total, "items": items, "truncated": truncated}


def chroma_item(name: str, item_id: str) -> dict | None:
    """L3：单条全文 + 全部 metadata。不存在返回 None。"""
    client = _chroma_client()
    col = client.get_collection(name)
    got = col.get(ids=[item_id], include=["documents", "metadatas"])
    ids = got.get("ids") or []
    if not ids:
        return None
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    return {
        "id": ids[0],
        "document": (docs[0] if docs else "") or "",
        "metadata": (metas[0] if metas else None),
    }


# ── BM25 ──────────────────────────────────────────────────────────────────────

def _bm25_files() -> list[Path]:
    base = bm25_dir()
    if not base.is_dir():
        return []
    return sorted(base.glob("bm25_*.pkl"))


def _coll_of(path: Path) -> str:
    stem = path.stem
    return stem[len("bm25_"):] if stem.startswith("bm25_") else stem


def bm25_indexes() -> dict:
    """L1：每个 bm25_*.pkl 的规模与加载结果（坏文件报原因）。"""
    base = bm25_dir()
    items: list[dict] = []
    for path in _bm25_files():
        coll = _coll_of(path)
        row: dict = {
            "file": path.name, "collection": coll, "bytes": path.stat().st_size,
        }
        try:
            with open(path, "rb") as f:
                pickle.load(f)
            from src.rag.bm25_index import BM25Index
            idx = BM25Index.load_or_new(coll, path)
            row["docs"] = len(idx.docs)
            row["k1"] = idx.k1
            row["b"] = idx.b
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        items.append(row)
    return {"dir": str(base), "indexes": items}


def _load_bm25(collection: str):
    from src.rag.bm25_index import BM25Index

    path = bm25_dir() / f"bm25_{collection}.pkl"
    if not path.exists():
        return None
    return BM25Index.load_or_new(collection, path)


def bm25_docs(
    collection: str,
    limit: int = 50,
    offset: int = 0,
    *,
    filename_q: str | None = None,
    body_q: str | None = None,
    ts_from: int | None = None,
    ts_to: int | None = None,
    sort_by: str | None = None,
    desc: bool = False,
) -> dict | None:
    """L2：某索引的文档块分页（id + 正文摘要）。索引不存在返回 None。

    索引已全量载入内存，过滤（文件名/正文/入库时间段）+ 排序（文件名/入库时间）全在内存做。
    """
    idx = _load_bm25(collection)
    if idx is None:
        return None
    # 默认按 id 稳定排序，保证未排序时翻页顺序确定
    rows = [
        {"id": cid, "document": d.document or "", "metadata": dict(d.metadata or {}), "tokens": len(d.tokens or [])}
        for cid, d in sorted(idx.docs.items())
    ]
    rows = filter_sort_rows(
        rows, filename_q=filename_q, body_q=body_q,
        ts_from=ts_from, ts_to=ts_to, sort_by=sort_by, desc=desc,
    )
    total = len(rows)
    page = rows[offset: offset + limit]
    items = [
        {"id": r["id"], "preview": truncate(r["document"]), "tokens": r["tokens"], "metadata": r["metadata"]}
        for r in page
    ]
    return {"collection": collection, "total": total, "items": items}


def bm25_doc(collection: str, doc_id: str) -> dict | None:
    """L3：单个文档块全文 + metadata + tokens 规模。不存在返回 None。"""
    idx = _load_bm25(collection)
    if idx is None:
        return None
    d = idx.docs.get(doc_id)
    if d is None:
        return None
    return {
        "id": doc_id,
        "document": d.document or "",
        "metadata": dict(d.metadata or {}),
        "tokens": len(d.tokens or []),
    }


# ── SQLite ────────────────────────────────────────────────────────────────────

def _ro_connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def sqlite_table_row_counts(db_path: Path) -> list[tuple[str, int]]:
    """库内各用户表的行数（过滤 sqlite_% 内部表）。"""
    conn = _ro_connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        out: list[tuple[str, int]] = []
        for t in tables:
            q = f'SELECT COUNT(*) FROM "{t.replace(chr(34), chr(34) + chr(34))}"'
            out.append((t, int(conn.execute(q).fetchone()[0])))
        return out
    finally:
        conn.close()


def _db_key(path: Path) -> str:
    """URL 友好的库标识：文件名去扩展名（如 auth.db -> auth）。"""
    return path.stem


def sqlite_databases() -> dict:
    """L1：全部库 + 每库表名与行数（不读单元格）。"""
    items: list[dict] = []
    for label, path in sqlite_db_files():
        row: dict = {
            "key": _db_key(path), "label": label, "file": path.name,
            "path": str(path), "exists": path.exists(),
        }
        if not path.exists():
            row["tables"] = []
        else:
            try:
                row["tables"] = [{"name": t, "rows": n} for t, n in sqlite_table_row_counts(path)]
            except sqlite3.Error as e:
                row["tables"] = []
                row["error"] = str(e)
        items.append(row)
    return {"databases": items}


def _resolve_db_key(db_key: str) -> Path | None:
    for _label, path in sqlite_db_files():
        if _db_key(path) == db_key:
            return path
    return None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _col_is_iso(conn: sqlite3.Connection, qtable: str, col: str) -> bool:
    """探一条非空值判断该列是不是 ISO 时间文本（影响时间段过滤的比较方式）。"""
    row = conn.execute(
        f"SELECT {_quote_ident(col)} FROM {qtable} WHERE {_quote_ident(col)} IS NOT NULL LIMIT 1"
    ).fetchone()
    v = row[0] if row else None
    return isinstance(v, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", v))


def _epoch_to_local_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def sqlite_table_rows(
    db_key: str,
    table: str,
    limit: int = 50,
    offset: int = 0,
    *,
    user_id: int | None = None,
    time_col: str | None = None,
    ts_from: int | None = None,
    ts_to: int | None = None,
    sort_by: str | None = None,
    desc: bool = False,
) -> dict | None:
    """L2/L3：表数据分页；敏感列（口令/密钥/hash 等）值脱敏为 ***。

    支持按 `user_id`（精确）和某时间列范围过滤，以及按列排序——列名一律按该表真实列
    白名单校验后再拼 SQL，值走绑定参数。时间列自动识别 ISO 文本 / epoch 数字两种存法。
    库不存在返回 None；表名非法返回 {error}。
    """
    path = _resolve_db_key(db_key)
    if path is None or not path.exists():
        return None
    conn = _ro_connect(path)
    try:
        valid = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if table not in valid:
            return {"db_key": db_key, "table": table, "error": "表不存在"}
        qtable = _quote_ident(table)
        columns = [c[0] for c in conn.execute(f"SELECT * FROM {qtable} LIMIT 0").description]
        colset = set(columns)

        clauses: list[str] = []
        params: list = []
        if user_id is not None and "user_id" in colset:
            clauses.append('"user_id" = ?')
            params.append(user_id)
        if time_col in colset and (ts_from is not None or ts_to is not None):
            iso = _col_is_iso(conn, qtable, time_col)
            if ts_from is not None:
                clauses.append(f"{_quote_ident(time_col)} >= ?")
                params.append(_epoch_to_local_iso(ts_from) if iso else ts_from)
            if ts_to is not None:
                clauses.append(f"{_quote_ident(time_col)} <= ?")
                params.append(_epoch_to_local_iso(ts_to) if iso else ts_to)
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        order_sql = ""
        if sort_by in colset:
            order_sql = f" ORDER BY {_quote_ident(sort_by)} {'DESC' if desc else 'ASC'}"

        total = int(conn.execute(f"SELECT COUNT(*) FROM {qtable}{where_sql}", params).fetchone()[0])
        cur = conn.execute(
            f"SELECT * FROM {qtable}{where_sql}{order_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        sensitive = {c for c in columns if is_sensitive_column(c)}
        rows = []
        for raw in cur.fetchall():
            row = {}
            for col_name, val in zip(columns, raw):
                row[col_name] = "***" if col_name in sensitive else val
            rows.append(row)
        return {
            "db_key": db_key, "table": table, "total": total,
            "columns": columns, "masked_columns": sorted(sensitive), "rows": rows,
        }
    finally:
        conn.close()
