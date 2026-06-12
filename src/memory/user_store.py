"""多用户账号 / 登录态 / 每用户 rules 的 SQLite 存储层。

独立于业务数据库（chat_history / user_memory 等），单独存 `./db/sqlite/auth.db`。
密码用标准库 `hashlib.pbkdf2_hmac`（零新依赖）+ 每用户随机 salt 哈希，不存明文。

三张表：
    users(id, username UNIQUE, password_hash, salt, role, created_at)
    auth_sessions(token, user_id, created_at, expires_at)   -- cookie 里存 token
    user_rules(user_id PK, content, updated_at)             -- 每用户偏好规则
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

AUTH_DB_PATH: str = config.AUTH_DB_PATH

# 合法角色枚举
ROLE_USER = "user"
ROLE_ADMIN = "admin"
_ROLES: tuple[str, ...] = (ROLE_USER, ROLE_ADMIN)

# pbkdf2 参数（仅本地学习项目，取一个合理的迭代数）
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_ALGO = "sha256"


def _hash_password(password: str, salt: str) -> str:
    """用 pbkdf2_hmac 派生密码哈希（hex 串）。"""
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return dk.hex()


class UserStore:
    """账号 / 登录态 / 每用户 rules 存储（CRUD 依赖层）。

    内置 threading.Lock，可被多线程安全读写。
    """

    def __init__(self, db_path: str = AUTH_DB_PATH) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()
        logger.info("UserStore 初始化完成: %s", self._db_path)

    def _create_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT    NOT NULL UNIQUE,
                    password_hash TEXT    NOT NULL,
                    salt          TEXT    NOT NULL,
                    role          TEXT    NOT NULL DEFAULT 'user',
                    created_at    TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token      TEXT    PRIMARY KEY,
                    user_id    INTEGER NOT NULL,
                    created_at TEXT    NOT NULL,
                    expires_at TEXT    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                    ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS user_rules (
                    user_id    INTEGER PRIMARY KEY,
                    content    TEXT    NOT NULL DEFAULT '',
                    updated_at TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id          INTEGER PRIMARY KEY,
                    active_model     TEXT,
                    thinking_enabled INTEGER,
                    thinking_budget  INTEGER,
                    updated_at       TEXT NOT NULL
                );
            """)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "username": row["username"],
            "role": row["role"],
            "created_at": row["created_at"],
        }

    # ── 账号 ────────────────────────────────────────────────────────────────

    def count_users(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, username: str, password: str, role: str = ROLE_USER) -> dict[str, Any] | None:
        """新建用户；用户名已占用返回 None。role 非法降级为 user。"""
        username = (username or "").strip()
        if not username or not password:
            return None
        if role not in _ROLES:
            role = ROLE_USER
        salt = secrets.token_hex(16)
        pwd_hash = _hash_password(password, salt)
        now = self._now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            # 用户名不区分大小写：admin / Admin / ADMIN 视为同一占用
            exists = self._conn.execute(
                "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            if exists is not None:
                return None
            cur = self._conn.execute(
                "INSERT INTO users(username, password_hash, salt, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, pwd_hash, salt, role, now),
            )
            uid = int(cur.lastrowid or 0)
        logger.info("create_user: id=%d, username=%r, role=%s", uid, username, role)
        return {"id": uid, "username": username, "role": role, "created_at": now}

    def verify_password(self, username: str, password: str) -> dict[str, Any] | None:
        """校验密码，成功返回 user dict，失败返回 None。"""
        username = (username or "").strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
        if row is None:
            return None
        if not secrets.compare_digest(
            _hash_password(password, row["salt"]), row["password_hash"]
        ):
            return None
        return self._row_to_user(row)

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                ((username or "").strip(),),
            ).fetchone()
        return self._row_to_user(row) if row else None

    # ── 登录态 ──────────────────────────────────────────────────────────────

    def create_session(self, user_id: int, ttl_days: int) -> str:
        """为 user 新建登录态，返回 token（写进 cookie）。

        登录时顺带清理所有已过期的登录态行：惰性删除只在 token 被访问时触发，长期不再
        登录的废 token 不会被回收，这里在每次新建时主动清一次。
        """
        token = secrets.token_urlsafe(32)
        now = self._now()
        expires = now + timedelta(days=max(1, ttl_days))
        now_str = now.isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?", (now_str,)
            )
            self._conn.execute(
                "INSERT INTO auth_sessions(token, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token, user_id, now_str, expires.isoformat(timespec="seconds")),
            )
        return token

    def purge_expired_sessions(self) -> int:
        """删除所有已过期的登录态行，返回删除条数（供定时清理显式调用）。"""
        now_str = self._now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?", (now_str,)
            )
        return cur.rowcount

    def get_user_by_token(self, token: str) -> dict[str, Any] | None:
        """凭 token 取用户；token 无效 / 过期返回 None（过期顺手删除）。"""
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, expires_at FROM auth_sessions WHERE token = ?", (token,)
            ).fetchone()
        if row is None:
            return None
        try:
            expired = datetime.fromisoformat(row["expires_at"]) < self._now()
        except ValueError:
            expired = True
        if expired:
            self.delete_session(token)
            return None
        return self.get_user_by_id(int(row["user_id"]))

    def delete_session(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))

    # ── 每用户 rules ──────────────────────────────────────────────────────────

    def get_rules(self, user_id: int) -> str:
        """读某用户的偏好规则文本；无则返回空串。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM user_rules WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row["content"] if row else ""

    def set_rules(self, user_id: int, content: str) -> None:
        """写某用户的偏好规则文本（upsert）。"""
        now = self._now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO user_rules(user_id, content, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET content = excluded.content, "
                "updated_at = excluded.updated_at",
                (user_id, content or "", now),
            )

    # ── 账号资料修改 ────────────────────────────────────────────────────────

    def update_username(self, user_id: int, new_username: str) -> str:
        """改用户名。返回 'ok' / 'invalid'（空） / 'taken'（已占用） / 'notfound'。"""
        new_username = (new_username or "").strip()
        if not new_username:
            return "invalid"
        with self._lock, self._conn:
            me = self._conn.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if me is None:
                return "notfound"
            taken = self._conn.execute(
                "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE AND id != ?",
                (new_username, user_id),
            ).fetchone()
            if taken is not None:
                return "taken"
            self._conn.execute(
                "UPDATE users SET username = ? WHERE id = ?", (new_username, user_id)
            )
        logger.info("update_username: id=%d -> %r", user_id, new_username)
        return "ok"

    def update_password(self, user_id: int, old_password: str, new_password: str) -> str:
        """改密码（需校验旧密码）。返回 'ok' / 'invalid'（新空） / 'wrong_old' / 'notfound'。"""
        if not new_password:
            return "invalid"
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT salt, password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if row is None:
                return "notfound"
            if not secrets.compare_digest(
                _hash_password(old_password, row["salt"]), row["password_hash"]
            ):
                return "wrong_old"
            new_salt = secrets.token_hex(16)
            new_hash = _hash_password(new_password, new_salt)
            self._conn.execute(
                "UPDATE users SET salt = ?, password_hash = ? WHERE id = ?",
                (new_salt, new_hash, user_id),
            )
        logger.info("update_password: id=%d", user_id)
        return "ok"

    # ── 用户管理（admin） ──────────────────────────────────────────────────────

    def list_users(self) -> list[dict[str, Any]]:
        """列出所有用户（id 升序）。"""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
        return [self._row_to_user(r) for r in rows]

    def count_admins(self) -> int:
        with self._lock:
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM users WHERE role = ?", (ROLE_ADMIN,)
                ).fetchone()[0]
            )

    def delete_user(self, user_id: int) -> bool:
        """删除账号本身：users + 该用户登录态 + 偏好规则。

        业务数据（会话 / 记忆 / 计划等）由调用方按需级联清理，不在本方法内。
        """
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self._conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            self._conn.execute("DELETE FROM user_rules WHERE user_id = ?", (user_id,))
            self._conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("delete_user: id=%d", user_id)
        return deleted

    # ── 每用户 LLM 偏好（模型 / thinking） ──────────────────────────────────────

    def get_settings(self, user_id: int) -> dict[str, Any]:
        """读某用户的 LLM 偏好；未设置的字段为 None（调用方回落全局默认）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT active_model, thinking_enabled, thinking_budget "
                "FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return {"active_model": None, "thinking_enabled": None, "thinking_budget": None}
        te = row["thinking_enabled"]
        return {
            "active_model": row["active_model"],
            "thinking_enabled": None if te is None else bool(te),
            "thinking_budget": row["thinking_budget"],
        }

    def set_settings(
        self,
        user_id: int,
        active_model: str | None = None,
        thinking_enabled: bool | None = None,
        thinking_budget: int | None = None,
    ) -> None:
        """upsert 某用户 LLM 偏好；传 None 的字段保持原值（COALESCE 合并）。"""
        now = self._now().isoformat(timespec="seconds")
        te = None if thinking_enabled is None else int(thinking_enabled)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO user_settings"
                "(user_id, active_model, thinking_enabled, thinking_budget, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "active_model = COALESCE(excluded.active_model, user_settings.active_model), "
                "thinking_enabled = COALESCE(excluded.thinking_enabled, user_settings.thinking_enabled), "
                "thinking_budget = COALESCE(excluded.thinking_budget, user_settings.thinking_budget), "
                "updated_at = excluded.updated_at",
                (user_id, active_model, te, thinking_budget, now),
            )

    # ── 资源管理 ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "UserStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── 进程内单例 helper ───────────────────────────────────────────────────────

_shared_store: UserStore | None = None


def get_shared_store() -> UserStore:
    """获取进程级共享 UserStore；首次调用懒加载。"""
    global _shared_store
    if _shared_store is None:
        _shared_store = UserStore()
    return _shared_store


def reset_shared_store_for_testing(store: UserStore | None = None) -> None:
    """UT 专用：注入 mock store / 重置为 None。生产代码不要调用。"""
    global _shared_store
    _shared_store = store
