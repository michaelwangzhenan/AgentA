"""实时安全拦截事件存储层。

记录**对话进行中真实发生的安全拦截**（与离线红队评估区分）：
    - scrub：外部不可信内容（RAG / web / fetch / MCP）命中注入模板被清洗
    - tool ：工具被名单门（SECURITY_MODE / BLOCKLIST / ALLOWLIST）拦下
    - ssrf ：fetch_url 的 URL 被 SSRF 防御拒绝

复用 ``usage.db``（与 ``UsageStore`` / ``TraceStore`` 同库不同表）；独立 connection +
``threading.Lock``，SQLite 文件级锁保证跨连接并发写安全。

埋点为旁路：``record_security_event`` 写入出错只记日志、绝不抛——绝不阻断主对话。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

EVENT_SCRUB = "scrub"
EVENT_TOOL = "tool"
EVENT_SSRF = "ssrf"
_EVENT_TYPES = (EVENT_SCRUB, EVENT_TOOL, EVENT_SSRF)


class SecurityEventStore:
    """实时安全拦截事件存储（写 usage.db）。内置锁，多线程安全。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or config.USAGE_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()
        logger.info("SecurityEventStore 初始化完成: %s", self._db_path)

    def _create_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS security_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type  TEXT NOT NULL,
                    detail      TEXT NOT NULL DEFAULT '',
                    user_id     INTEGER NOT NULL,
                    created_at  INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_secevent_time
                    ON security_events(created_at);
                CREATE INDEX IF NOT EXISTS idx_secevent_user_time
                    ON security_events(user_id, created_at);
            """)

    @staticmethod
    def _now() -> int:
        return int(time.time())

    # ── 写入 ────────────────────────────────────────────────────────────────

    def record(self, event_type: str, detail: str, user_id: int) -> None:
        """写一条拦截事件。"""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO security_events (event_type, detail, user_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (str(event_type), str(detail or ""), int(user_id), self._now()),
            )

    # ── 读取 ────────────────────────────────────────────────────────────────

    def summary(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> dict[str, Any]:
        """区间内总拦截数 + 分类型计数。"""
        where = "created_at >= ? AND created_at < ?"
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(int(user_id))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT event_type, COUNT(*) AS cnt FROM security_events "
                f"WHERE {where} GROUP BY event_type",
                params,
            ).fetchall()
        by_type = {t: 0 for t in _EVENT_TYPES}
        for r in rows:
            by_type[r["event_type"]] = int(r["cnt"])
        return {"total": sum(by_type.values()), "by_type": by_type}

    def recent(
        self,
        start_ts: int,
        end_ts: int,
        user_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """区间内最近若干条拦截（时间倒序）。"""
        where = "created_at >= ? AND created_at < ?"
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(int(user_id))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT event_type, detail, user_id, created_at FROM security_events "
                f"WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                [*params, max(1, int(limit))],
            ).fetchall()
        return [
            {
                "event_type": r["event_type"],
                "detail": r["detail"],
                "user_id": int(r["user_id"]),
                "created_at": int(r["created_at"]),
            }
            for r in rows
        ]

    def delete_all_for_user(self, user_id: int) -> int:
        """删除某用户全部拦截事件（注销 / admin 删号级联）。返回删除条数。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM security_events WHERE user_id = ?", (int(user_id),)
            )
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SecurityEventStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── 进程内单例 helper ───────────────────────────────────────────────────────

_shared_store: SecurityEventStore | None = None
_shared_lock = threading.Lock()


def get_shared_store() -> SecurityEventStore:
    """获取进程级共享 SecurityEventStore；首次调用懒加载（双检锁）。"""
    global _shared_store
    if _shared_store is None:
        with _shared_lock:
            if _shared_store is None:
                _shared_store = SecurityEventStore()
    return _shared_store


def reset_shared_store_for_testing(store: SecurityEventStore | None = None) -> None:
    """UT 专用：注入 mock store / 重置为 None。生产代码不要调用。"""
    global _shared_store
    _shared_store = store


def record_security_event(event_type: str, detail: str) -> None:
    """记录一条实时安全拦截（旁路埋点）。

    读 `current_user_id()` 作归属；**异常只记日志、绝不抛**——绝不阻断主对话。
    """
    try:
        from src.stores.user_context import current_user_id

        get_shared_store().record(event_type, detail, current_user_id())
    except Exception:  # noqa: BLE001 — 埋点旁路，绝不影响对话
        logger.warning("[security_event] 记录失败（已忽略，不影响对话）", exc_info=True)
