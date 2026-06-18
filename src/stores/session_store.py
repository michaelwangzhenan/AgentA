"""
Session 持久化模块 —— SQLite 存储层

管理会话（session）及其消息：持久化到本地 SQLite，支持多 session 管理与
按 session_id 恢复完整对话上下文。除消息 CRUD 外，还负责 session 生命周期
（list / rename / create / delete）与按 user_id 的归属隔离。

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
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import src.config as config
from src.stores.user_context import current_user_id

logger = logging.getLogger(__name__)

# SQLite 文件路径，由 config.MEMORY_DB_PATH 决定（对应 .env 中的 MEMORY_DB_PATH）
MEMORY_DB_PATH: str = config.MEMORY_DB_PATH
# session 首条用户消息预览截断长度
_FIRST_MSG_PREVIEW_LEN: int = 80


class SessionStore:
    """
    SQLite 会话存储（CRUD 依赖层）：管理 session 元数据 + 其下消息。

    职责：消息的 append / load / delete + session 生命周期 list_sessions / rename / clear。
    不感知"轮（turn）/ skill_pair 完整性 / max_history_turns 截断"等 loop 语义 ——
    这些业务策略由 `src/agent/core/history_manager.py` 的 `HistoryManager` 封装。

    命名约定：数据存储用 `*Store` 后缀，区别于 `*Manager` helper。

    线程安全：连接以 `check_same_thread=False` 跨线程共享，所有读写经 `threading.Lock`
    串行化（与 `UserStore` 一致）。进程级单例在 Web 线程池里被多请求并发访问时安全。
    """

    def __init__(self, db_path: str = MEMORY_DB_PATH) -> None:
        """
        初始化存储，自动创建数据库文件和表结构。

        Args:
            db_path: SQLite 文件路径，默认 ./db/sqlite/session.db。
        """
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()
        logger.info("SessionStore 初始化完成: %s", db_path)

    # ── 表结构初始化 ──────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """创建 messages 和 sessions 表（幂等，已存在则跳过）。"""
        with self._lock, self._conn:
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
                user_id       INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT NOT NULL,
                first_user_msg TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            """)

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

    def append(self, session_id: str, msg: dict[str, Any], user_id: int | None = None) -> None:
        """
        将单条 message 追加到指定 session。

        自动处理 tool_calls（assistant role）和 tool_call_id（tool role）的序列化。
        若 session 不存在则自动创建（归属 user_id，缺省取当前用户）。

        Args:
            session_id: 会话 ID。
            msg: 标准 OpenAI messages 格式的单条消息 dict。
            user_id: 新建 session 时的归属用户；None 取 current_user_id()。
        """
        uid = user_id if user_id is not None else current_user_id()
        role: str = msg.get("role", "")
        content: str = msg.get("content") or ""
        tool_calls_raw = msg.get("tool_calls") or []
        tool_calls_json: str = json.dumps(tool_calls_raw, ensure_ascii=False)
        tool_call_id: str = msg.get("tool_call_id") or ""
        now = datetime.now().isoformat(timespec="seconds")

        with self._lock, self._conn:
            # 若 session 不存在，插入 session 记录
            existing = self._conn.execute(
                "SELECT first_user_msg FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO sessions(session_id, user_id, created_at, first_user_msg) VALUES(?,?,?,?)",
                    (session_id, uid, now, content[:_FIRST_MSG_PREVIEW_LEN] if role == "user" else ""),
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

    def load(self, session_id: str, user_id: int | None = None) -> list[dict[str, Any]]:
        """
        加载指定 session 的完整 messages 历史，按时间顺序排列。

        tool_calls 字段自动反序列化为 list；空值字段自动清理（不传给 LLM）。

        Args:
            session_id: 会话 ID。
            user_id: 归属用户；None 取 current_user_id()。非本人 session 返回空列表
                （纵深防御：即便上层漏调 owns_session 也不跨用户泄露）。

        Returns:
            messages list，可直接拼接到 Agent 的 messages 列表中。
            若 session 不存在或不归属该用户返回空列表。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock:
            if not self._owns_unlocked(session_id, uid):
                return []
            rows = self._conn.execute(
                """SELECT role, content, tool_calls, tool_call_id
                   FROM messages
                   WHERE session_id = ?
                   ORDER BY id ASC""",
                (session_id,),
            ).fetchall()

        return [self._row_to_message(row) for row in rows]

    def load_last_n_messages(
        self, session_id: str, n: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """
        仅加载指定 session 最近 n 条消息，避免全量加载长历史 session 的 DB I/O 开销。

        先按 id 倒序取 n 条，再反转还原时序，等价于全量加载后取末尾 n 条。

        Args:
            session_id: 会话 ID。
            n: 最多返回的消息条数。
            user_id: 归属用户；None 取 current_user_id()。非本人 session 返回空列表。

        Returns:
            最近 n 条消息，时序升序，格式与 load() 相同。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock:
            if not self._owns_unlocked(session_id, uid):
                return []
            rows = self._conn.execute(
                """SELECT role, content, tool_calls, tool_call_id
                   FROM messages
                   WHERE session_id = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (session_id, n),
            ).fetchall()

        return [self._row_to_message(row) for row in reversed(rows)]

    def count_user_messages(self, session_id: str, user_id: int | None = None) -> int:
        """统计某 session 内 role='user' 的消息条数（用于记忆提取的无状态节流判定）。

        非本人 session 返回 0（纵深防御，同 load）。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock:
            if not self._owns_unlocked(session_id, uid):
                return 0
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
                (session_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def truncate_from_user_message(
        self, session_id: str, user_index: int, user_id: int | None = None
    ) -> int:
        """删除从第 `user_index`（0 基）条 user 消息起（含）之后的全部消息。

        用于"编辑重发 / 重新生成"：丢弃某条用户消息及其后的所有回答与轮次，
        让调用方随后用 `agent.run()` 重新追加（run 会自己再写一遍 user 消息）。

        按 user 角色的序号定位、再按行 id 截断，因此 tool / assistant 等中间行
        多少都不影响定位的准确性。

        Args:
            session_id: 会话 ID。
            user_index: 第几条 user 消息（0 基）。越界（无对应 user 消息）则不删。
            user_id: 归属用户；None 取 current_user_id()。非本人 session 不删、返回 0。

        Returns:
            实际删除的消息行数。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock:
            if not self._owns_unlocked(session_id, uid):
                return 0
            rows = self._conn.execute(
                """SELECT id FROM messages
                   WHERE session_id = ? AND role = 'user'
                   ORDER BY id ASC""",
                (session_id,),
            ).fetchall()
            if user_index < 0 or user_index >= len(rows):
                return 0
            cutoff_id = rows[user_index]["id"]
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM messages WHERE session_id = ? AND id >= ?",
                    (session_id, cutoff_id),
                )
        logger.info(
            "已截断 session %s：从第 %d 条 user 消息起删除 %d 行",
            session_id, user_index, cur.rowcount,
        )
        return cur.rowcount

    def clear(self, session_id: str, user_id: int | None = None) -> None:
        """
        清空指定 session 的所有消息记录（同时删除 session 元数据）。

        Args:
            session_id: 要清空的会话 ID。
            user_id: 归属用户；None 取 current_user_id()。非本人 session 不做任何操作。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock, self._conn:
            if not self._owns_unlocked(session_id, uid):
                return
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
        logger.info("已清空 session: %s", session_id)

    def delete_all_for_user(self, user_id: int) -> None:
        """删除某用户的全部会话及其消息（admin 删用户时级联清理）。"""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM messages WHERE session_id IN "
                "(SELECT session_id FROM sessions WHERE user_id = ?)",
                (user_id,),
            )
            self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        logger.info("已删除 user=%d 的全部会话", user_id)

    def rename_session(self, session_id: str, title: str, user_id: int | None = None) -> bool:
        """
        重命名 session（复用 `first_user_msg` 列存用户手动标题）。

        Args:
            session_id: 要改名的会话 ID。
            title:      新标题，会按 `_FIRST_MSG_PREVIEW_LEN` 截断。
            user_id:    归属用户；None 取 current_user_id()。非本人 session 返回 False。

        Returns:
            True 表示找到并改名；False 表示 session 不存在或不归属该用户。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock, self._conn:
            if not self._owns_unlocked(session_id, uid):
                logger.info("rename_session: session 不存在或不归属用户，跳过: %s", session_id)
                return False
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

    def create_empty_session(self, session_id: str, title: str = "", user_id: int | None = None) -> bool:
        """
        显式创建空 session（不写任何 message 也立即出现在列表里）。

        Args:
            session_id: 会话 ID。
            title:      初始标题（默认空字符串，前端展示时 fallback 到 `session_id 前 8 位`）。
            user_id:    归属用户；None 取 current_user_id()。

        Returns:
            True 表示新创建；False 表示 session 已存在（幂等，未改动）。
        """
        uid = user_id if user_id is not None else current_user_id()
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO sessions(session_id, user_id, created_at, first_user_msg) VALUES(?,?,?,?)",
                (session_id, uid, now, title[:_FIRST_MSG_PREVIEW_LEN]),
            )
        return cur.rowcount > 0

    def _owns_unlocked(self, session_id: str, uid: int) -> bool:
        """锁内归属校验：session 归属 uid 返回 True；session 不存在视为不归属。

        供已持锁的方法做纵深防御复用（不自行 acquire，避免非重入锁死锁）。
        """
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?",
            (session_id, uid),
        ).fetchone()
        return row is not None

    def owns_session(self, session_id: str, user_id: int | None = None) -> bool:
        """该 session 是否归属指定用户（用于 API 层鉴权）。session 不存在返回 False。"""
        uid = user_id if user_id is not None else current_user_id()
        with self._lock:
            return self._owns_unlocked(session_id, uid)

    def get_session_owner(self, session_id: str) -> int | None:
        """返回 session 归属的 user_id；session 不存在返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return int(row["user_id"]) if row else None

    def delete_session(self, session_id: str, user_id: int | None = None) -> bool:
        """
        删除指定 session 的所有消息记录及元数据。

        与 clear() 不同：clear() 用于重置当前活跃 session（调用方随后会新建同名或新 session）；
        delete_session() 用于彻底删除任意历史 session，返回是否实际删除了记录。

        Args:
            session_id: 要删除的会话 ID。
            user_id: 归属用户；None 取 current_user_id()。非本人 session 不删、返回 False。

        Returns:
            True 表示 session 存在且归属该用户并已删除；False 表示不存在或不归属。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock, self._conn:
            existed = self._owns_unlocked(session_id, uid)
            if not existed:
                logger.info("delete_session: session 不存在或不归属用户，跳过: %s", session_id)
                return False
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
        logger.info("已删除 session: %s", session_id)
        return existed

    def clean_all_sessions(self) -> int:
        """
        清空数据库中所有 session 的消息记录和元数据。

        Returns:
            被删除的 session 数量。
        """
        with self._lock, self._conn:
            count: int = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM sessions")
        logger.info("已清空全部 %d 个 session", count)
        return count

    def list_sessions(
        self,
        query: str | None = None,
        limit: int | None = None,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        列出指定用户的历史 session，按创建时间降序排列。

        Args:
            query: 可选搜索词，按 session_id 前缀（去前后空白后大小写敏感）OR
                   first_user_msg 大小写不敏感 LIKE 匹配。None 或空串视为不过滤。
            limit: 可选返回上限。None 表示返回全部。
            user_id: 归属用户；None 取 current_user_id()。

        Returns:
            list of dict，每项包含 session_id、created_at、first_user_msg、msg_count。
        """
        uid = user_id if user_id is not None else current_user_id()
        sql = """
            SELECT s.session_id, s.created_at, s.first_user_msg,
                   COUNT(m.id) AS msg_count
            FROM sessions s
            LEFT JOIN messages m ON s.session_id = m.session_id
            WHERE s.user_id = ?
        """
        params: list[Any] = [uid]
        if query:
            q = query.strip()
            if q:
                sql += (
                    " AND (s.session_id LIKE ? "
                    "OR LOWER(s.first_user_msg) LIKE LOWER(?))"
                )
                params.extend([f"{q}%", f"%{q}%"])
        sql += " GROUP BY s.session_id ORDER BY s.created_at DESC"
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict[str, Any](row) for row in rows]

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
