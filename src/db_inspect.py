"""只读巡检 Chroma / SQLite / BM25 的公共读逻辑。

CLI（`tools/db_show.py`）与 API（`/admin/db/*`）共用本模块，保证两边口径一致。
原则：**只读、绝不写库**；遇坏库 / 坏向量段降级为返回 `error` 字段，不向上抛断，
让调用方仍能看到其余正常数据。
"""
from __future__ import annotations

import pickle
import sqlite3
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


def chroma_items(name: str, limit: int = 50, offset: int = 0) -> dict:
    """L2：某 collection 条目分页（id + 正文摘要 + metadata）。不取 embeddings。"""
    client = _chroma_client()
    col = client.get_collection(name)  # 不存在会抛，由路由转 404
    total = col.count()
    try:
        got = col.get(limit=limit, offset=offset, include=["documents", "metadatas"])
    except Exception as e:
        return {"name": name, "total": total, "items": [], "error": f"{type(e).__name__}: {e}"}
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    items = []
    for i, _id in enumerate(ids):
        doc = (docs[i] if i < len(docs) else "") or ""
        items.append({
            "id": _id,
            "preview": truncate(doc),
            "doc_len": len(doc),
            "metadata": metas[i] if i < len(metas) else None,
        })
    return {"name": name, "total": total, "items": items}


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


def bm25_docs(collection: str, limit: int = 50, offset: int = 0) -> dict | None:
    """L2：某索引的文档块分页（id + 正文摘要）。索引不存在返回 None。"""
    idx = _load_bm25(collection)
    if idx is None:
        return None
    ids = sorted(idx.docs.keys())
    total = len(ids)
    page = ids[offset: offset + limit]
    items = []
    for _id in page:
        d = idx.docs[_id]
        items.append({
            "id": _id,
            "preview": truncate(d.document or ""),
            "tokens": len(d.tokens or []),
            "metadata": dict(d.metadata or {}),
        })
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


def sqlite_table_rows(db_key: str, table: str, limit: int = 50, offset: int = 0) -> dict | None:
    """L2/L3：表数据分页；敏感列（口令/密钥/hash 等）值脱敏为 ***。

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
        quoted = f'"{table.replace(chr(34), chr(34) + chr(34))}"'
        total = int(conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
        cur = conn.execute(f"SELECT * FROM {quoted} LIMIT ? OFFSET ?", (limit, offset))
        columns = [c[0] for c in cur.description]
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
