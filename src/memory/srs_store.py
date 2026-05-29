"""
SRS 主动复习调度持久化模块 —— SQLite 存储层（Phase 2.4 §4.9.9 D1 / D3 / D8-D10）

将 SM-2 调度的卡片状态持久化到本地独立 SQLite（默认 ./sqlite_db/srs.db）。
卡片来源（D5）：① quiz_question — Phase 2.3 错题进 SRS 复习；② manual — 用户
手动加自定义卡（正面 Q + 背面 A）。learning_task 不进，详 [§4.13.1 #23]
(../../docs/iter_2_agent.md#4131-deferred-backlog暂时不做)。

表结构（D8 冗余存 front + back：quiz_question 被 delete 后 SRS 卡仍可独立复习）：
    srs_cards(
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type     TEXT    NOT NULL,                  -- 'quiz_question' / 'manual'
        source_ref      INTEGER,                           -- quiz_question.id（manual 卡为 NULL）
        front           TEXT    NOT NULL,                  -- 题面 / 正面（quiz: stem；manual: 用户输入）
        back            TEXT    NOT NULL,                  -- 答案 / 背面（quiz: correct_answer + explanation；manual: 用户输入）
        note            TEXT    NOT NULL DEFAULT '',       -- 可选自由备注
        -- SM-2 调度状态 ---------------------------------------------------------
        ease_factor     REAL    NOT NULL DEFAULT 2.5,      -- 难度因子，下限 1.3（D11）
        interval_days   INTEGER NOT NULL DEFAULT 0,        -- 当前 interval 天数；0 = 新卡
        repetitions     INTEGER NOT NULL DEFAULT 0,        -- 累计成功 review 次数（again 时重置）
        lapses          INTEGER NOT NULL DEFAULT 0,        -- 答错总次数（again 触发）
        next_review_at  TEXT    NOT NULL,                  -- ISO 本地时间；新卡 = created_at
        last_reviewed_at TEXT   NOT NULL DEFAULT '',       -- '' 表示从未 review
        -- 元信息 ---------------------------------------------------------------
        status          TEXT    NOT NULL DEFAULT 'active', -- 'active' / 'suspended' / 'archived'
        created_at      TEXT    NOT NULL,
        updated_at      TEXT    NOT NULL
    )

跨 session 持久化场景（验收 ② / ④）：用户重启 agent → 新 session 问"今天有什么要复习"
→ Agent 调 `query_srs_due` tool → 本 store 按 `next_review_at <= now AND status='active'`
扫出 due 卡 → 渲染给用户。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

SRS_DB_PATH: str = config.SRS_DB_PATH

# 合法 source_type 枚举（D5）
_SOURCE_TYPES: tuple[str, ...] = ("quiz_question", "manual")
# 合法 status 枚举（D9）
_CARD_STATUS: tuple[str, ...] = ("active", "suspended", "archived")

# SM-2 算法常量（D11：ease 下限 1.3、interval 下限 1）
EASE_FACTOR_INIT: float = 2.5
EASE_FACTOR_MIN: float = 1.3
INTERVAL_MIN_DAYS: int = 1


class SRSStore:
    """
    SQLite SRS 卡片存储（CRUD 依赖层）。

    职责单一：srs_card 的 create / read / update（review 后调度状态更新）/
    suspend / archive / delete。**不感知 SM-2 公式**，公式由
    [`src.agent.core.srs_scheduler`](../agent/core/srs_scheduler.py) 计算后
    把新 state（ease / interval / repetitions / lapses / next_review_at）
    传入 `update_review_state` 写库。

    命名约定（[agenta-conventions.mdc §2](../../.cursor/rules/agenta-conventions.mdc)）：
    数据存储用 `*Store` 后缀；公式 / 调度逻辑放 `*_scheduler.py` 助手模块。
    """

    def __init__(self, db_path: str = SRS_DB_PATH) -> None:
        """初始化存储，自动创建数据库文件和表结构。"""
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._create_tables()
        logger.info("SRSStore 初始化完成: %s", db_path)

    # ── 表结构初始化 ──────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """创建 srs_cards 表（幂等）。"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS srs_cards (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type      TEXT    NOT NULL,
                source_ref       INTEGER,
                front            TEXT    NOT NULL,
                back             TEXT    NOT NULL,
                note             TEXT    NOT NULL DEFAULT '',
                ease_factor      REAL    NOT NULL DEFAULT 2.5,
                interval_days    INTEGER NOT NULL DEFAULT 0,
                repetitions      INTEGER NOT NULL DEFAULT 0,
                lapses           INTEGER NOT NULL DEFAULT 0,
                next_review_at   TEXT    NOT NULL,
                last_reviewed_at TEXT    NOT NULL DEFAULT '',
                status           TEXT    NOT NULL DEFAULT 'active',
                created_at       TEXT    NOT NULL,
                updated_at       TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_srs_status_due
                ON srs_cards(status, next_review_at);
            CREATE INDEX IF NOT EXISTS idx_srs_source
                ON srs_cards(source_type, source_ref);
        """)
        self._conn.commit()

    # ── 内部 helper ───────────────────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _row_to_card(row: sqlite3.Row) -> dict[str, Any]:
        """SQLite 行 → card dict。"""
        return {
            "id": row["id"],
            "source_type": row["source_type"],
            "source_ref": row["source_ref"],
            "front": row["front"],
            "back": row["back"],
            "note": row["note"],
            "ease_factor": float(row["ease_factor"]),
            "interval_days": int(row["interval_days"]),
            "repetitions": int(row["repetitions"]),
            "lapses": int(row["lapses"]),
            "next_review_at": row["next_review_at"],
            "last_reviewed_at": row["last_reviewed_at"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ── card CRUD ────────────────────────────────────────────────────────────

    def add_card(
        self,
        source_type: str,
        front: str,
        back: str,
        source_ref: int | None = None,
        note: str = "",
    ) -> int:
        """
        新建一张 SRS 卡。`next_review_at` 初始化为 now（新卡立即 due 一次，
        SM-2 公式在用户首次 review 后给出真正的 interval）。

        Args:
            source_type: 'quiz_question' / 'manual'。
            front: 题面 / 正面文本，非空。
            back: 答案 / 背面文本，非空。
            source_ref: 卡片来源外部 id；quiz_question 必填，manual 必为 None。
            note: 可选自由备注（≤ 200 字）。

        Returns:
            新 card 的 id（自增整数）。

        Raises:
            ValueError: 入参非法（source_type 不在枚举内 / front 或 back 空 /
                       source_type 与 source_ref 不匹配）。
        """
        if source_type not in _SOURCE_TYPES:
            raise ValueError(
                f"source_type 必须是 {_SOURCE_TYPES} 之一，收到 {source_type!r}"
            )
        front = (front or "").strip()
        back = (back or "").strip()
        if not front:
            raise ValueError("front 不能为空")
        if not back:
            raise ValueError("back 不能为空")
        if source_type == "quiz_question":
            if not isinstance(source_ref, int) or source_ref < 1:
                raise ValueError(
                    f"source_type=quiz_question 时 source_ref 必须是 ≥ 1 整数，"
                    f"收到 {source_ref!r}"
                )
        else:  # manual
            if source_ref is not None:
                raise ValueError(
                    f"source_type=manual 时 source_ref 必须为 None，收到 {source_ref!r}"
                )

        note = (note or "")[:200]
        now = self._now()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO srs_cards("
                "  source_type, source_ref, front, back, note,"
                "  ease_factor, interval_days, repetitions, lapses,"
                "  next_review_at, last_reviewed_at, status,"
                "  created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, '', 'active', ?, ?)",
                (source_type, source_ref, front, back, note,
                 EASE_FACTOR_INIT, now, now, now),
            )
        card_id = int(cursor.lastrowid or 0)
        logger.info(
            "add_card: id=%d, type=%s, ref=%s, front=%r",
            card_id, source_type, source_ref, front[:40],
        )
        return card_id

    def get_card(self, card_id: int) -> dict[str, Any] | None:
        """读单卡（含全部 SM-2 调度字段 + 元信息）。"""
        row = self._conn.execute(
            "SELECT * FROM srs_cards WHERE id = ?", (card_id,),
        ).fetchone()
        return self._row_to_card(row) if row else None

    def card_exists_for_source(self, source_type: str, source_ref: int) -> int | None:
        """
        查给定 (source_type, source_ref) 是否已存在 active / suspended 卡片；
        archived 不计（用户已归档表示"不要这张卡了"）。

        Returns:
            已存在的 card_id；不存在返 None。
        """
        if source_type not in _SOURCE_TYPES:
            return None
        row = self._conn.execute(
            "SELECT id FROM srs_cards "
            "WHERE source_type = ? AND source_ref = ? AND status != 'archived' "
            "LIMIT 1",
            (source_type, source_ref),
        ).fetchone()
        return int(row["id"]) if row else None

    def list_cards(
        self,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        列卡片，按创建时间倒序 + id 倒序作稳定 tie-breaker。

        Args:
            status: 可选过滤；None → 列 active + suspended（archived 默认排除）。
            limit: 可选条数上限；None 不限。
        """
        sql = "SELECT * FROM srs_cards"
        clauses: list[str] = []
        params: list[Any] = []
        if status is None:
            clauses.append("status != 'archived'")
        else:
            if status not in _CARD_STATUS:
                logger.warning("list_cards: 非法 status=%r，返空列表", status)
                return []
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC"
        if isinstance(limit, int) and limit > 0:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_card(r) for r in rows]

    def list_due(self, limit: int | None = None, now: str | None = None) -> list[dict[str, Any]]:
        """
        列 due 卡片（status='active' 且 next_review_at <= now），按 next_review_at
        升序（最早 due 的先 review）+ id 升序作 tie-breaker。

        Args:
            limit: 返回最多条数；None 走 config.SRS_DEFAULT_DUE_QUERY_LIMIT。
            now: 可选 ISO 时间串覆盖（UT 用）；None 取当前本地时间。
        """
        eff_now = now if now is not None else self._now()
        eff_limit = limit if isinstance(limit, int) and limit > 0 else config.SRS_DEFAULT_DUE_QUERY_LIMIT
        rows = self._conn.execute(
            "SELECT * FROM srs_cards "
            "WHERE status = 'active' AND next_review_at <= ? "
            "ORDER BY next_review_at ASC, id ASC "
            f"LIMIT {int(eff_limit)}",
            (eff_now,),
        ).fetchall()
        return [self._row_to_card(r) for r in rows]

    def update_review_state(
        self,
        card_id: int,
        *,
        ease_factor: float,
        interval_days: int,
        repetitions: int,
        lapses: int,
        next_review_at: str,
    ) -> bool:
        """
        把 SM-2 公式算出来的新状态写库 + 更新 last_reviewed_at + updated_at。

        Returns:
            True 更新成功；False card 不存在 / 不是 active（suspended / archived 不允许 review）。
        """
        card = self.get_card(card_id)
        if card is None:
            logger.warning("update_review_state: card_id=%d 不存在", card_id)
            return False
        if card["status"] != "active":
            logger.warning(
                "update_review_state: card_id=%d 状态 %s，禁止 review",
                card_id, card["status"],
            )
            return False

        # 边界保护（D11：ease ≥ 1.3, interval ≥ 1）—— 即便 caller 没夹紧也兜底
        ef = max(EASE_FACTOR_MIN, float(ease_factor))
        iv = max(INTERVAL_MIN_DAYS, int(interval_days))
        reps = max(0, int(repetitions))
        lp = max(0, int(lapses))

        now = self._now()
        with self._conn:
            self._conn.execute(
                "UPDATE srs_cards SET "
                "  ease_factor = ?, interval_days = ?, repetitions = ?, lapses = ?, "
                "  next_review_at = ?, last_reviewed_at = ?, updated_at = ? "
                "WHERE id = ?",
                (ef, iv, reps, lp, next_review_at, now, now, card_id),
            )
        logger.info(
            "update_review_state: card_id=%d, ef=%.2f, iv=%d, reps=%d, lapses=%d, next=%s",
            card_id, ef, iv, reps, lp, next_review_at,
        )
        return True

    def set_status(self, card_id: int, status: str) -> bool:
        """
        修改卡片状态（active / suspended / archived）；非法 status 拒改。
        archived 是"软删除"，与 delete_card（硬删）不同。
        """
        if status not in _CARD_STATUS:
            logger.warning("set_status: 非法 status=%r", status)
            return False
        card = self.get_card(card_id)
        if card is None:
            return False
        now = self._now()
        with self._conn:
            self._conn.execute(
                "UPDATE srs_cards SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, card_id),
            )
        logger.info("set_status: card_id=%d, status=%s", card_id, status)
        return True

    def suspend(self, card_id: int) -> bool:
        """暂停一张卡（不再出现在 due 列表，但保留 SM-2 状态可恢复）。"""
        return self.set_status(card_id, "suspended")

    def resume(self, card_id: int) -> bool:
        """从 suspended 恢复为 active。"""
        card = self.get_card(card_id)
        if card is None or card["status"] != "suspended":
            return False
        return self.set_status(card_id, "active")

    def archive(self, card_id: int) -> bool:
        """归档一张卡（软删除，不出现在 list_cards 默认列表）。"""
        return self.set_status(card_id, "archived")

    def delete_card(self, card_id: int) -> bool:
        """硬删除一张卡（不可恢复）。常规业务请用 archive。"""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM srs_cards WHERE id = ?", (card_id,),
            )
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("delete_card: card_id=%d", card_id)
        return deleted

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def stats(self, now: str | None = None) -> dict[str, Any]:
        """
        返回 SRS 队列摘要统计（D15 MVP 最简版）：
            - total_active：未归档 active 卡总数
            - total_suspended：suspended 卡总数
            - total_archived：archived 卡总数
            - due_count：当前 due（active 且 next_review_at <= now）数
            - avg_ease：active 卡平均 ease_factor（无 active 卡返 0.0）
            - mature_count：active 中 interval_days >= 21（Anki 默认"mature"门槛）
        """
        eff_now = now if now is not None else self._now()
        row = self._conn.execute("""
            SELECT
              SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS total_active,
              SUM(CASE WHEN status = 'suspended' THEN 1 ELSE 0 END) AS total_suspended,
              SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) AS total_archived,
              SUM(CASE WHEN status = 'active' AND next_review_at <= ? THEN 1 ELSE 0 END) AS due_count,
              AVG(CASE WHEN status = 'active' THEN ease_factor ELSE NULL END) AS avg_ease,
              SUM(CASE WHEN status = 'active' AND interval_days >= 21 THEN 1 ELSE 0 END) AS mature_count
            FROM srs_cards
        """, (eff_now,)).fetchone()
        return {
            "total_active": int(row["total_active"] or 0),
            "total_suspended": int(row["total_suspended"] or 0),
            "total_archived": int(row["total_archived"] or 0),
            "due_count": int(row["due_count"] or 0),
            "avg_ease": round(float(row["avg_ease"] or 0.0), 2),
            "mature_count": int(row["mature_count"] or 0),
        }

    # ── 资源管理 ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "SRSStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── 进程内单例 helper ───────────────────────────────────────────────────────
# 给 tools.py（写）+ CLI handlers.py（读）等多个调用方共享同一连接；
# 避免不同模块各自 `SRSStore()` 导致同进程多连接 / SQLite write-lock 冲突。

_shared_store: SRSStore | None = None


def get_shared_store() -> SRSStore:
    """获取进程级共享 SRSStore；首次调用懒加载。"""
    global _shared_store
    if _shared_store is None:
        _shared_store = SRSStore()
    return _shared_store


def reset_shared_store_for_testing(store: SRSStore | None = None) -> None:
    """
    UT 专用：注入 mock store / 重置为 None（让下次 get_shared_store 懒加载真实 store）。
    生产代码不要调用。
    """
    global _shared_store
    _shared_store = store
