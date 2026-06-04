"""
对话记忆持久化模块 —— SQLite 存储层

将 Agent 的 messages 历史持久化到本地 SQLite 数据库，支持多 session 管理。
重启后可通过 session_id 恢复完整的对话上下文。

表结构：
    messages(
        id            INTEGER  PRIMARY KEY AUTOINCREMENT,
        session_id    TEXT     NOT NULL,
        role          TEXT     NOT NULL,   -- system/user/assistant/tool
        content       TEXT     NOT NULL DEFAULT '',
        tool_calls    TEXT     NOT NULL DEFAULT '[]',  -- JSON，assistant role 时使用
        tool_call_id  TEXT     NOT NULL DEFAULT '',    -- tool role 时使用
        timestamp     TEXT     NOT NULL    -- ISO 8601 本地时间，精确到秒
    )
    sessions(
        session_id    TEXT     PRIMARY KEY,
        created_at    TEXT     NOT NULL,
        first_user_msg TEXT   NOT NULL DEFAULT ''   -- 首条用户消息摘要，便于展示
    )
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

# SQLite 文件路径，由 config.MEMORY_DB_PATH 决定（对应 .env 中的 MEMORY_DB_PATH）
MEMORY_DB_PATH: str = config.MEMORY_DB_PATH
# session 首条用户消息预览截断长度
_FIRST_MSG_PREVIEW_LEN: int = 80


class ChatHistoryStore:
    """
    SQLite 对话记忆存储（CRUD 依赖层）。

    职责单一：消息的 append / load / delete / list_sessions / clear。
    不感知"轮（turn）/ skill_pair 完整性 / max_history_turns 截断"等 loop 语义 ——
    这些业务策略由 `src/agent/core/history_manager.py` 的 `HistoryManager` 封装。

    命名约定（design.md §5）：数据存储用 `*Store` 后缀，区别于 `*Manager` helper。

    线程安全说明：每个实例持有独立连接，同一进程单实例使用即可。
    """

    def __init__(self, db_path: str = MEMORY_DB_PATH) -> None:
        """
        初始化存储，自动创建数据库文件和表结构。

        Args:
            db_path: SQLite 文件路径，默认 ./sqlite_db/chat_history.db。
        """
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("ChatHistoryStore 初始化完成: %s", db_path)

    # ── 表结构初始化 ──────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """创建 messages 和 sessions 表（幂等，已存在则跳过）。"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT    NOT NULL,
                role         TEXT    NOT NULL,
                content      TEXT    NOT NULL DEFAULT '',
                tool_calls   TEXT    NOT NULL DEFAULT '[]',
                tool_call_id TEXT    NOT NULL DEFAULT '',
                timestamp    TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id);

            CREATE TABLE IF NOT EXISTS sessions (
                session_id    TEXT PRIMARY KEY,
                created_at    TEXT NOT NULL,
                first_user_msg TEXT NOT NULL DEFAULT ''
            );
        """)
        self._conn.commit()

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        """将 SQLite 行对象转换为标准 OpenAI messages 格式的 dict。"""
        msg: dict[str, Any] = {
            "role": row["role"],
            "content": row["content"] if row["content"] else "",
        }
        tool_calls = json.loads(row["tool_calls"])
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if row["tool_call_id"]:
            msg["tool_call_id"] = row["tool_call_id"]
        return msg

    # ── 核心接口 ──────────────────────────────────────────────────────────────

    def append(self, session_id: str, msg: dict[str, Any]) -> None:
        """
        将单条 message 追加到指定 session。

        自动处理 tool_calls（assistant role）和 tool_call_id（tool role）的序列化。
        若 session 不存在则自动创建。

        Args:
            session_id: 会话 ID。
            msg: 标准 OpenAI messages 格式的单条消息 dict。
        """
        role: str = msg.get("role", "")
        content: str = msg.get("content") or ""
        tool_calls_raw = msg.get("tool_calls") or []
        tool_calls_json: str = json.dumps(tool_calls_raw, ensure_ascii=False)
        tool_call_id: str = msg.get("tool_call_id") or ""
        now = datetime.now().isoformat(timespec="seconds")

        with self._conn:
            # 若 session 不存在，插入 session 记录
            existing = self._conn.execute(
                "SELECT first_user_msg FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO sessions(session_id, created_at, first_user_msg) VALUES(?,?,?)",
                    (session_id, now, content[:_FIRST_MSG_PREVIEW_LEN] if role == "user" else ""),
                )
            elif role == "user" and not existing["first_user_msg"]:
                # session 由 create_empty_session 预建（标题为空），首条 user 消息时回填标题
                self._conn.execute(
                    "UPDATE sessions SET first_user_msg = ? WHERE session_id = ?",
                    (content[:_FIRST_MSG_PREVIEW_LEN], session_id),
                )

            # 追加消息
            self._conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, tool_calls, tool_call_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, role, content, tool_calls_json, tool_call_id, now),
            )

    def load(self, session_id: str) -> list[dict[str, Any]]:
        """
        加载指定 session 的完整 messages 历史，按时间顺序排列。

        tool_calls 字段自动反序列化为 list；空值字段自动清理（不传给 LLM）。

        Args:
            session_id: 会话 ID。

        Returns:
            messages list，可直接拼接到 Agent 的 messages 列表中。
            若 session 不存在返回空列表。
        """
        rows = self._conn.execute(
            """SELECT role, content, tool_calls, tool_call_id
               FROM messages
               WHERE session_id = ?
               ORDER BY id ASC""",
            (session_id,),
        ).fetchall()

        return [self._row_to_message(row) for row in rows]

    def load_last_n_messages(self, session_id: str, n: int) -> list[dict[str, Any]]:
        """
        仅加载指定 session 最近 n 条消息，避免全量加载长历史 session 的 DB I/O 开销。

        先按 id 倒序取 n 条，再反转还原时序，等价于全量加载后取末尾 n 条。

        Args:
            session_id: 会话 ID。
            n: 最多返回的消息条数。

        Returns:
            最近 n 条消息，时序升序，格式与 load() 相同。
        """
        rows = self._conn.execute(
            """SELECT role, content, tool_calls, tool_call_id
               FROM messages
               WHERE session_id = ?
               ORDER BY id DESC
               LIMIT ?""",
            (session_id, n),
        ).fetchall()

        return [self._row_to_message(row) for row in reversed(rows)]

    def clear(self, session_id: str) -> None:
        """
        清空指定 session 的所有消息记录（同时删除 session 元数据）。

        Args:
            session_id: 要清空的会话 ID。
        """
        with self._conn:
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
        logger.info("已清空 session: %s", session_id)

    def rename_session(self, session_id: str, title: str) -> bool:
        """
        重命名 session（复用 `first_user_msg` 列存用户手动标题）。

        Args:
            session_id: 要改名的会话 ID。
            title:      新标题，会按 `_FIRST_MSG_PREVIEW_LEN` 截断。

        Returns:
            True 表示找到并改名；False 表示 session 不存在。
        """
        with self._conn:
            cur = self._conn.execute(
                "UPDATE sessions SET first_user_msg = ? WHERE session_id = ?",
                (title[:_FIRST_MSG_PREVIEW_LEN], session_id),
            )
        ok = cur.rowcount > 0
        if ok:
            logger.info("已重命名 session %s -> %r", session_id, title[:_FIRST_MSG_PREVIEW_LEN])
        else:
            logger.info("rename_session: session 不存在，跳过: %s", session_id)
        return ok

    def create_empty_session(self, session_id: str, title: str = "") -> bool:
        """
        显式创建空 session（不写任何 message 也立即出现在列表里）。

        Args:
            session_id: 会话 ID。
            title:      初始标题（默认空字符串，前端展示时 fallback 到 `session_id 前 8 位`）。

        Returns:
            True 表示新创建；False 表示 session 已存在（幂等，未改动）。
        """
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO sessions(session_id, created_at, first_user_msg) VALUES(?,?,?)",
                (session_id, now, title[:_FIRST_MSG_PREVIEW_LEN]),
            )
        return cur.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        """
        删除指定 session 的所有消息记录及元数据。

        与 clear() 不同：clear() 用于重置当前活跃 session（调用方随后会新建同名或新 session）；
        delete_session() 用于彻底删除任意历史 session，返回是否实际删除了记录。

        Args:
            session_id: 要删除的会话 ID。

        Returns:
            True 表示 session 存在并已删除；False 表示 session 不存在。
        """
        with self._conn:
            existed = self._conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone() is not None
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
        if existed:
            logger.info("已删除 session: %s", session_id)
        else:
            logger.info("delete_session: session 不存在，跳过: %s", session_id)
        return existed

    def clean_all_sessions(self) -> int:
        """
        清空数据库中所有 session 的消息记录和元数据。

        Returns:
            被删除的 session 数量。
        """
        with self._conn:
            count: int = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM sessions")
        logger.info("已清空全部 %d 个 session", count)
        return count

    def list_sessions(
        self,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        列出历史 session，按创建时间降序排列。

        Args:
            query: 可选搜索词，按 session_id 前缀（去前后空白后大小写敏感）OR
                   first_user_msg 大小写不敏感 LIKE 匹配。None 或空串视为不过滤。
            limit: 可选返回上限。None 表示返回全部。

        Returns:
            list of dict，每项包含 session_id、created_at、first_user_msg、msg_count。
        """
        sql = """
            SELECT s.session_id, s.created_at, s.first_user_msg,
                   COUNT(m.id) AS msg_count
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
        """
        params: list[Any] = []
        if query:
            q = query.strip()
            if q:
                sql += (
                    " WHERE s.session_id LIKE ? "
                    "OR LOWER(s.first_user_msg) LIKE LOWER(?)"
                )
                params.extend([f"{q}%", f"%{q}%"])
        sql += " GROUP BY s.session_id ORDER BY s.created_at DESC"
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [dict[str, Any](row) for row in rows]

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "ChatHistoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
