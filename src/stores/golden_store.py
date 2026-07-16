"""RAG golden 数据集存储层。

把 RAG 检索评估的 golden（标准答案基准）从静态 JSON 升级为可在线 CRUD 的 SQLite，
每条带来源（手工 / AI 生成）与审核状态（待审 / 通过 / 拒绝）。这样 RAG 入库时可以让
LLM 自动生成候选写入（状态 pending），管理员在前端审核后合入正式评估集。

一张表 ``rag_golden``：
    id, query, expected_keywords(JSON list), expected_source, expected_source_contains,
    type, note, source('manual'|'ai'), status('pending'|'approved'|'rejected'),
    doc_id(AI 生成时来源文档), created_at, updated_at

评估脚本通过 ``list_for_eval`` 取 golden（默认只取 approved），字段对齐
``tools/rag_eval/runner.py`` 的黄金集格式（query / expected_keywords /
expected_source / expected_source_contains / note / type）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

SOURCE_MANUAL = "manual"
SOURCE_AI = "ai"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

_VALID_SOURCES = (SOURCE_MANUAL, SOURCE_AI)
_VALID_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)


class GoldenStore:
    """RAG golden CRUD 存储。内置 ``threading.Lock``，可多线程安全读写。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or config.RAG_GOLDEN_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()
        self._migrate()
        logger.info("GoldenStore 初始化完成: %s", self._db_path)

    def _create_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS rag_golden (
                    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                    query                    TEXT NOT NULL,
                    expected_keywords        TEXT NOT NULL DEFAULT '[]',
                    expected_source          TEXT NOT NULL DEFAULT '',
                    expected_source_contains TEXT NOT NULL DEFAULT '',
                    type                     TEXT NOT NULL DEFAULT '',
                    note                     TEXT NOT NULL DEFAULT '',
                    source                   TEXT NOT NULL DEFAULT 'manual',
                    status                   TEXT NOT NULL DEFAULT 'approved',
                    doc_id                   TEXT NOT NULL DEFAULT '',
                    created_at               INTEGER NOT NULL,
                    updated_at               INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_golden_status ON rag_golden(status);
            """)

    def _migrate(self) -> None:
        """老库补列：expected_source / type 是后加的，旧库用 ADD COLUMN 补齐（纯追加，安全）。"""
        with self._lock, self._conn:
            cols = {
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(rag_golden)").fetchall()
            }
            for col in ("expected_source", "type"):
                if col not in cols:
                    self._conn.execute(
                        f"ALTER TABLE rag_golden ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
                    )

    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _normalize_keywords(keywords: Any) -> list[str]:
        """把输入归一成去空白的字符串列表（接受 list 或逗号分隔字符串）。"""
        if keywords is None:
            return []
        if isinstance(keywords, str):
            parts = [p.strip() for p in keywords.split(",")]
        elif isinstance(keywords, (list, tuple)):
            parts = [str(p).strip() for p in keywords]
        else:
            return []
        return [p for p in parts if p]

    def _row_to_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        try:
            kws = json.loads(r["expected_keywords"] or "[]")
        except (json.JSONDecodeError, TypeError):
            kws = []
        return {
            "id": int(r["id"]),
            "query": r["query"],
            "expected_keywords": kws if isinstance(kws, list) else [],
            "expected_source": r["expected_source"] or "",
            "expected_source_contains": r["expected_source_contains"] or "",
            "type": r["type"] or "",
            "note": r["note"] or "",
            "source": r["source"],
            "status": r["status"],
            "doc_id": r["doc_id"] or "",
            "created_at": int(r["created_at"]),
            "updated_at": int(r["updated_at"]),
        }

    # ── 写入 ────────────────────────────────────────────────────────────────

    def create(
        self,
        query: str,
        expected_keywords: Any = None,
        expected_source_contains: str = "",
        note: str = "",
        source: str = SOURCE_MANUAL,
        status: str = STATUS_APPROVED,
        doc_id: str = "",
        expected_source: str = "",
        golden_type: str = "",
    ) -> int:
        """新增一条 golden，返回新行 id。query 为空抛 ValueError。"""
        q = (query or "").strip()
        if not q:
            raise ValueError("query 不能为空")
        src = source if source in _VALID_SOURCES else SOURCE_MANUAL
        st = status if status in _VALID_STATUSES else STATUS_PENDING
        kws = json.dumps(self._normalize_keywords(expected_keywords), ensure_ascii=False)
        now = self._now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO rag_golden"
                "(query, expected_keywords, expected_source, expected_source_contains, "
                " type, note, source, status, doc_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (q, kws, (expected_source or "").strip(),
                 (expected_source_contains or "").strip(), (golden_type or "").strip(),
                 (note or "").strip(), src, st, (doc_id or "").strip(), now, now),
            )
        return int(cur.lastrowid)

    def update(self, golden_id: int, **fields: Any) -> bool:
        """按 id 局部更新。可改 query / expected_keywords / expected_source /
        expected_source_contains / type / note / status。无该行返回 False。"""
        sets: list[str] = []
        params: list[Any] = []
        if "query" in fields:
            q = (fields["query"] or "").strip()
            if not q:
                raise ValueError("query 不能为空")
            sets.append("query = ?")
            params.append(q)
        if "expected_keywords" in fields:
            sets.append("expected_keywords = ?")
            params.append(json.dumps(self._normalize_keywords(fields["expected_keywords"]), ensure_ascii=False))
        if "expected_source" in fields:
            sets.append("expected_source = ?")
            params.append((fields["expected_source"] or "").strip())
        if "expected_source_contains" in fields:
            sets.append("expected_source_contains = ?")
            params.append((fields["expected_source_contains"] or "").strip())
        if "type" in fields:
            sets.append("type = ?")
            params.append((fields["type"] or "").strip())
        if "note" in fields:
            sets.append("note = ?")
            params.append((fields["note"] or "").strip())
        if "status" in fields:
            st = fields["status"]
            if st not in _VALID_STATUSES:
                raise ValueError(f"非法状态: {st}")
            sets.append("status = ?")
            params.append(st)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(self._now())
        params.append(int(golden_id))
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE rag_golden SET {', '.join(sets)} WHERE id = ?", params
            )
        return cur.rowcount > 0

    def set_status(self, golden_id: int, status: str) -> bool:
        """审核：把某条 golden 标成 approved / rejected / pending。"""
        return self.update(golden_id, status=status)

    def delete(self, golden_id: int) -> bool:
        """删一条 golden；无该行返回 False。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM rag_golden WHERE id = ?", (int(golden_id),)
            )
        return cur.rowcount > 0

    # ── 读取 ────────────────────────────────────────────────────────────────

    def get(self, golden_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM rag_golden WHERE id = ?", (int(golden_id),)
            ).fetchone()
        return self._row_to_dict(r) if r else None

    def list(
        self,
        status: str | None = None,
        source: str | None = None,
        doc_id: str | None = None,
        source_contains: str | None = None,
        query_contains: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页列出（时间倒序）。返回 (rows, total)。"""
        where = "1=1"
        params: list[Any] = []
        if status in _VALID_STATUSES:
            where += " AND status = ?"
            params.append(status)
        if source in _VALID_SOURCES:
            where += " AND source = ?"
            params.append(source)
        if doc_id:  # 按关联 KB 文档筛选（仅 AI / 手动生成的候选有 doc_id）
            where += " AND doc_id = ?"
            params.append(str(doc_id))
        if source_contains:  # 按来源文件名/路径子串过滤（expected_source_contains）
            where += " AND expected_source_contains LIKE ?"
            params.append(f"%{source_contains}%")
        if query_contains:  # 按问题子串过滤（query）
            where += " AND query LIKE ?"
            params.append(f"%{query_contains}%")
        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM rag_golden WHERE {where}", params
                ).fetchone()[0]
            )
            rows = self._conn.execute(
                f"SELECT * FROM rag_golden WHERE {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        return [self._row_to_dict(r) for r in rows], total

    def counts(self) -> dict[str, int]:
        """各状态条数 + 总数（看板概览用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS c FROM rag_golden GROUP BY status"
            ).fetchall()
        out = {STATUS_PENDING: 0, STATUS_APPROVED: 0, STATUS_REJECTED: 0}
        for r in rows:
            out[r["status"]] = int(r["c"])
        out["total"] = sum(out.values())
        return out

    def doc_counts(self) -> dict[str, dict[str, int]]:
        """按关联文档 doc_id 统计候选数：{doc_id: {"total": n, "pending": m}}。

        供知识库 L2 文档行显示「已生成 N 条候选（含待审）」。只含有 doc_id 的记录。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, status, COUNT(*) AS c FROM rag_golden "
                "WHERE doc_id != '' GROUP BY doc_id, status"
            ).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            d = out.setdefault(r["doc_id"], {"total": 0, "pending": 0})
            d["total"] += int(r["c"])
            if r["status"] == STATUS_PENDING:
                d["pending"] += int(r["c"])
        return out

    def delete_pending_by_doc(self, doc_id: str) -> int:
        """删某文档下所有 pending 候选（手动"重新生成"前清旧，approved/rejected 保留）。"""
        if not doc_id:
            return 0
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM rag_golden WHERE doc_id = ? AND status = ?",
                (str(doc_id), STATUS_PENDING),
            )
        return cur.rowcount

    def delete_by_doc(self, doc_id: str) -> int:
        """删某文档关联的全部 golden（pending / approved / rejected）。"""
        if not doc_id:
            return 0
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM rag_golden WHERE doc_id = ?",
                (str(doc_id),),
            )
        return cur.rowcount

    def export_all(self) -> list[dict[str, Any]]:
        """导出全部 golden（完整字段，时间倒序），供"导出 json"下载。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM rag_golden ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_for_eval(self, use_pending: bool = False) -> list[dict[str, Any]]:
        """取评估用 golden，字段对齐 rag_eval runner 黄金集格式。

        默认只取 approved；``use_pending=True`` 时把 pending 也纳入（拒绝的永不纳入）。
        """
        statuses = [STATUS_APPROVED] + ([STATUS_PENDING] if use_pending else [])
        placeholder = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM rag_golden WHERE status IN ({placeholder}) "
                "ORDER BY id ASC",
                statuses,
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = self._row_to_dict(r)
            item: dict[str, Any] = {"query": d["query"]}
            if d["expected_keywords"]:
                item["expected_keywords"] = d["expected_keywords"]
            if d["expected_source"]:
                item["expected_source"] = d["expected_source"]
            if d["expected_source_contains"]:
                item["expected_source_contains"] = d["expected_source_contains"]
            if d["type"]:
                item["type"] = d["type"]
            if d["note"]:
                item["note"] = d["note"]
            out.append(item)
        return out

    def import_items(
        self, items: list[dict[str, Any]], source: str = SOURCE_MANUAL, status: str = STATUS_APPROVED
    ) -> int:
        """从 JSON 黄金集批量导入（幂等：query 已存在则跳过）。返回新增条数。"""
        added = 0
        for it in items:
            q = str(it.get("query", "")).strip()
            if not q:
                continue
            with self._lock:
                exists = self._conn.execute(
                    "SELECT 1 FROM rag_golden WHERE query = ? LIMIT 1", (q,)
                ).fetchone()
            if exists:
                continue
            self.create(
                query=q,
                expected_keywords=it.get("expected_keywords"),
                expected_source=it.get("expected_source", ""),
                expected_source_contains=it.get("expected_source_contains", ""),
                golden_type=it.get("type", ""),
                note=it.get("note", ""),
                source=source,
                status=status,
            )
            added += 1
        return added

    # ── 资源管理 ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "GoldenStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── 进程内单例 helper ───────────────────────────────────────────────────────

_shared_store: GoldenStore | None = None
_shared_lock = threading.Lock()


def get_shared_store() -> GoldenStore:
    """获取进程级共享 GoldenStore；首次调用懒加载（双检锁）。"""
    global _shared_store
    if _shared_store is None:
        with _shared_lock:
            if _shared_store is None:
                _shared_store = GoldenStore()
    return _shared_store


def reset_shared_store() -> None:
    """清掉进程级单例，下次 get_shared_store() 按当前 RAG_GOLDEN_DB_PATH 重建。

    供 RAG_GOLDEN_DB_PATH 在线改动后的 config hook 调用，让新路径即时生效。
    """
    global _shared_store
    with _shared_lock:
        _shared_store = None


def reset_shared_store_for_testing(store: GoldenStore | None = None) -> None:
    """UT 专用：注入 mock store / 重置为 None。生产代码不要调用。"""
    global _shared_store
    _shared_store = store
