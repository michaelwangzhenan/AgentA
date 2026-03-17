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
        timestamp     TEXT     NOT NULL    -- ISO 8601 格式
    )
    sessions(
        session_id    TEXT     PRIMARY KEY,
        created_at    TEXT     NOT NULL,
        first_user_msg TEXT   NOT NULL DEFAULT ''  -- 首条用户消息摘要，便于展示
    )
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

# SQLite 文件路径，由 config.MEMORY_DB_PATH 决定（对应 .env 中的 MEMORY_DB_PATH）
MEMORY_DB_PATH: str = config.MEMORY_DB_PATH


class MemoryStore:
    """
    SQLite 对话记忆存储。

    线程安全说明：每个实例持有独立连接，同一进程单实例使用即可。
    """

    def __init__(self, db_path: str = MEMORY_DB_PATH) -> None:
        """
        初始化存储，自动创建数据库文件和表结构。

        Args:
            db_path: SQLite 文件路径，默认 ./memory.db（与 chroma_db 同级）。
        """
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("MemoryStore 初始化完成: %s", db_path)

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
        now = datetime.now(timezone.utc).isoformat()

        with self._conn:
            # 若 session 不存在，插入 session 记录
            existing = self._conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not existing:
                self._conn.execute(
                    "INSERT INTO sessions(session_id, created_at, first_user_msg) VALUES(?,?,?)",
                    (session_id, now, content[:80] if role == "user" else ""),
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

        messages: list[dict[str, Any]] = []
        for row in rows:
            msg: dict[str, Any] = {"role": row["role"]}
            if row["content"]:
                msg["content"] = row["content"]
            else:
                msg["content"] = ""

            tool_calls = json.loads(row["tool_calls"])
            if tool_calls:
                msg["tool_calls"] = tool_calls

            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]

            messages.append(msg)

        return messages

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

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        列出所有历史 session，按创建时间降序排列。

        Returns:
            list of dict，每项包含 session_id、created_at、first_user_msg、msg_count。
        """
        rows = self._conn.execute(
            """SELECT s.session_id, s.created_at, s.first_user_msg,
                      COUNT(m.id) AS msg_count
               FROM sessions s
               LEFT JOIN messages m ON s.session_id = m.session_id
               GROUP BY s.session_id
               ORDER BY s.created_at DESC""",
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
