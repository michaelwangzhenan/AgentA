"""知识库文档级索引 —— 按 Chroma collection 维护 doc_id 聚合行，供 L2 列表分页。

入库 / 删除时增量更新；老库用 ``backfill_collection`` 或 CLI 一次性回填。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

_SORT_COLUMNS = frozenset({
    "filename", "lang", "chunks", "total_chars", "mtime", "ingested_at",
})


class KBDocIndexStore:
    """SQLite 文档级索引（按 collection + doc_id 主键）。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or config.KB_DOC_INDEX_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS kb_documents (
                collection   TEXT NOT NULL,
                doc_id       TEXT NOT NULL,
                filename     TEXT NOT NULL DEFAULT '',
                source       TEXT NOT NULL DEFAULT '',
                ext          TEXT NOT NULL DEFAULT '',
                lang         TEXT NOT NULL DEFAULT '',
                mtime        REAL NOT NULL DEFAULT 0,
                ingested_at  REAL NOT NULL DEFAULT 0,
                chunks       INTEGER NOT NULL DEFAULT 0,
                total_chars  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (collection, doc_id)
            );
            CREATE INDEX IF NOT EXISTS idx_kb_docs_coll_ingested
                ON kb_documents(collection, ingested_at DESC);
            CREATE INDEX IF NOT EXISTS idx_kb_docs_coll_filename
                ON kb_documents(collection, filename);
            """)

    def upsert(
        self,
        collection: str,
        *,
        doc_id: str,
        filename: str = "",
        source: str = "",
        ext: str = "",
        lang: str = "",
        mtime: float = 0.0,
        ingested_at: float = 0.0,
        chunks: int = 0,
        total_chars: int = 0,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO kb_documents(
                       collection, doc_id, filename, source, ext, lang,
                       mtime, ingested_at, chunks, total_chars
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(collection, doc_id) DO UPDATE SET
                       filename=excluded.filename,
                       source=excluded.source,
                       ext=excluded.ext,
                       lang=excluded.lang,
                       mtime=excluded.mtime,
                       ingested_at=excluded.ingested_at,
                       chunks=excluded.chunks,
                       total_chars=excluded.total_chars""",
                (
                    collection,
                    doc_id,
                    filename,
                    source,
                    ext,
                    lang,
                    float(mtime),
                    float(ingested_at),
                    int(chunks),
                    int(total_chars),
                ),
            )

    def delete_doc(self, collection: str, doc_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM kb_documents WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            )
        return cur.rowcount > 0

    def clear_collection(self, collection: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM kb_documents WHERE collection = ?", (collection,)
            )
        return cur.rowcount

    def collection_stats(self, collection: str) -> tuple[int, int]:
        """返回 (文档数, chunks 合计)。"""
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS docs, COALESCE(SUM(chunks), 0) AS chunks
                   FROM kb_documents WHERE collection = ?""",
                (collection,),
            ).fetchone()
        if row is None:
            return 0, 0
        return int(row["docs"]), int(row["chunks"])

    def list_page(
        self,
        collection: str,
        *,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "ingested_at",
        desc: bool = True,
        filename_q: str | None = None,
        lang: str | None = None,
        ext: str | None = None,
        ts_from: float | None = None,
        ts_to: float | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        sort_col = sort_by if sort_by in _SORT_COLUMNS else "ingested_at"
        order = "DESC" if desc else "ASC"

        clauses = ["collection = ?"]
        params: list[Any] = [collection]
        if filename_q:
            q = f"%{filename_q.strip().lower()}%"
            clauses.append(
                "(LOWER(filename) LIKE ? OR LOWER(source) LIKE ?)"
            )
            params.extend([q, q])
        if lang:
            clauses.append("lang = ?")
            params.append(lang.strip())
        if ext:
            clauses.append("ext = ?")
            params.append(ext.strip())
        if ts_from is not None:
            clauses.append("ingested_at >= ?")
            params.append(float(ts_from))
        if ts_to is not None:
            clauses.append("ingested_at <= ?")
            params.append(float(ts_to))

        where_sql = " AND ".join(clauses)
        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM kb_documents WHERE {where_sql}",
                    params,
                ).fetchone()[0]
            )
            offset = (page - 1) * page_size
            rows = self._conn.execute(
                f"""SELECT doc_id, filename, source, ext, lang, mtime,
                           ingested_at, chunks, total_chars
                    FROM kb_documents
                    WHERE {where_sql}
                    ORDER BY {sort_col} {order}, doc_id ASC
                    LIMIT ? OFFSET ?""",
                [*params, page_size, offset],
            ).fetchall()

        items = [
            {
                "doc_id": r["doc_id"],
                "filename": r["filename"] or "",
                "source": r["source"] or "",
                "ext": r["ext"] or "",
                "lang": r["lang"] or "",
                "mtime": float(r["mtime"] or 0.0),
                "ingested_at": float(r["ingested_at"] or 0.0),
                "chunks": int(r["chunks"] or 0),
                "total_chars": int(r["total_chars"] or 0),
            }
            for r in rows
        ]
        return items, total

    def replace_collection(self, collection: str, rows: list[dict[str, Any]]) -> int:
        """回填：先清库再批量写入。"""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM kb_documents WHERE collection = ?", (collection,)
            )
            for row in rows:
                self._conn.execute(
                    """INSERT INTO kb_documents(
                           collection, doc_id, filename, source, ext, lang,
                           mtime, ingested_at, chunks, total_chars
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        collection,
                        row["doc_id"],
                        row.get("filename") or "",
                        row.get("source") or "",
                        row.get("ext") or "",
                        row.get("lang") or "",
                        float(row.get("mtime") or 0.0),
                        float(row.get("ingested_at") or 0.0),
                        int(row.get("chunks") or 0),
                        int(row.get("total_chars") or 0),
                    ),
                )
        return len(rows)

    def close(self) -> None:
        self._conn.close()


_store: KBDocIndexStore | None = None
_store_lock = threading.Lock()


def get_kb_doc_index() -> KBDocIndexStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = KBDocIndexStore()
    return _store
