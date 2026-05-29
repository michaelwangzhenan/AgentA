"""
学习计划持久化模块 —— SQLite 存储层（Phase 2.2 §4.9.7 D1 / D9 / D10）

将用户的学习计划（learning_plans）与学习任务（learning_tasks）持久化到本地独立的
SQLite 数据库（默认 ./sqlite_db/learning.db）。区别于 Phase 2.1 plan-execute
的"单次问答内用完即弃 plan"（寄生 messages，详 [iter_2_agent.md §4.9.6 D1](../../docs/iter_2_agent.md#496-agent-循环升级-phase-21)），
本期学习计划是**跨 session 长期持久化的 plan**，按周/月级生命周期管理。

表结构：
    learning_plans(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        goal          TEXT    NOT NULL,                  -- "8 周准备 ML 面试"
        weeks         INTEGER NOT NULL DEFAULT 0,        -- 总周数；0 表示未指定
        status        TEXT    NOT NULL DEFAULT 'active', -- active / completed / abandoned
        is_active     INTEGER NOT NULL DEFAULT 0,        -- 0 / 1，同时仅一条为 1
        created_at    TEXT    NOT NULL,                  -- ISO 8601 本地时间
        updated_at    TEXT    NOT NULL
    )
    learning_tasks(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id       INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
        stage_idx     INTEGER NOT NULL,                  -- 阶段编号（Week 1, 2...），从 1 起
        order_idx     INTEGER NOT NULL,                  -- 阶段内顺序，从 1 起
        title         TEXT    NOT NULL,                  -- 任务描述，动词起头
        status        TEXT    NOT NULL DEFAULT 'pending',-- pending / success / skipped
        note          TEXT    NOT NULL DEFAULT '',       -- 完成备注 / 失败原因
        completed_at  TEXT    NOT NULL DEFAULT ''        -- success / skipped 时填
    )

多 plan 并存（D2）：通过 `is_active=1` 标记当前 active plan；同一时刻仅一条 plan
为 active，由 `create_plan` / `switch_active` 等方法在 application 层维护互斥。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

LEARNING_PLAN_DB_PATH: str = config.LEARNING_PLAN_DB_PATH

# plan 合法状态枚举
_PLAN_STATUS: tuple[str, ...] = ("active", "completed", "abandoned")
# task 合法状态枚举
_TASK_STATUS: tuple[str, ...] = ("pending", "success", "skipped")


class LearningPlanStore:
    """
    SQLite 学习计划存储（CRUD 依赖层）。

    职责单一：plan / task 的 create / read / update / delete + active 标记互斥维护。
    不感知"如何根据用户输入生成 plan / LLM 决策"等业务策略 ——
    这些由 [study-planner skill](../../.agenta/skills/study-planner/SKILL.md)
    + [tools.py 业务 plan tool](../agent/tools.py) 在 Agent loop 内驱动。

    命名约定（[agenta-conventions.mdc §2](../../.cursor/rules/agenta-conventions.mdc)）：
    数据存储用 `*Store` 后缀，区别于 `*Manager` helper。
    """

    def __init__(self, db_path: str = LEARNING_PLAN_DB_PATH) -> None:
        """
        初始化存储，自动创建数据库文件和表结构。

        Args:
            db_path: SQLite 文件路径，默认 ./sqlite_db/learning.db。
        """
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._create_tables()
        # session 级"加载到 prompt"映射（in-memory，不落 DB）：
        # 对标 Agent Skills 的 load_skill 生命周期 —— 必须由用户/CLI 显式
        # `/study load [id]` 才注入；切 session 自动清空。详 design.md §3.9.4。
        self._loaded_by_session: dict[str, int] = {}
        logger.info("LearningPlanStore 初始化完成: %s", db_path)

    # ── 表结构初始化 ──────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """创建 learning_plans / learning_tasks 表（幂等）。"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS learning_plans (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                goal        TEXT    NOT NULL,
                weeks       INTEGER NOT NULL DEFAULT 0,
                status      TEXT    NOT NULL DEFAULT 'active',
                is_active   INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_plans_active
                ON learning_plans(is_active);
            CREATE INDEX IF NOT EXISTS idx_plans_status
                ON learning_plans(status);

            CREATE TABLE IF NOT EXISTS learning_tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id       INTEGER NOT NULL REFERENCES learning_plans(id) ON DELETE CASCADE,
                stage_idx     INTEGER NOT NULL,
                order_idx     INTEGER NOT NULL,
                title         TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending',
                note          TEXT    NOT NULL DEFAULT '',
                completed_at  TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_plan
                ON learning_tasks(plan_id, stage_idx, order_idx);
        """)
        self._conn.commit()

    # ── 内部 helper ───────────────────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> dict[str, Any]:
        """SQLite 行 → plan dict（不含 tasks）。"""
        return {
            "id": row["id"],
            "goal": row["goal"],
            "weeks": row["weeks"],
            "status": row["status"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        """SQLite 行 → task dict。"""
        return {
            "id": row["id"],
            "plan_id": row["plan_id"],
            "stage_idx": row["stage_idx"],
            "order_idx": row["order_idx"],
            "title": row["title"],
            "status": row["status"],
            "note": row["note"],
            "completed_at": row["completed_at"],
        }

    # ── plan CRUD ────────────────────────────────────────────────────────────

    def create_plan(self, goal: str, weeks: int = 0, set_active: bool = True) -> int:
        """
        新建一个学习计划。

        Args:
            goal: 学习目标描述，如 "8 周准备 ML 面试"。
            weeks: 总周数；0 表示未指定。
            set_active: 是否同时把它设为 active plan（D2：新建默认 active，旧的自动 archive）。

        Returns:
            新 plan 的 id（自增整数）。
        """
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal 不能为空")
        if weeks < 0:
            raise ValueError(f"weeks 必须 ≥ 0，收到 {weeks!r}")

        now = self._now()
        with self._conn:
            if set_active:
                # 先把所有 plan 置为非 active，确保互斥
                self._conn.execute(
                    "UPDATE learning_plans SET is_active = 0, updated_at = ? "
                    "WHERE is_active = 1",
                    (now,),
                )
            cursor = self._conn.execute(
                "INSERT INTO learning_plans(goal, weeks, status, is_active, created_at, updated_at) "
                "VALUES (?, ?, 'active', ?, ?, ?)",
                (goal, weeks, 1 if set_active else 0, now, now),
            )
        plan_id = int(cursor.lastrowid or 0)
        logger.info("create_plan: id=%d, goal=%r, weeks=%d, active=%s",
                    plan_id, goal, weeks, set_active)
        return plan_id

    def add_tasks(self, plan_id: int, tasks: list[dict[str, Any]]) -> int:
        """
        批量给指定 plan 添加任务。

        Args:
            plan_id: plan id。
            tasks: 任务列表，每项是 {stage_idx, order_idx, title}。

        Returns:
            插入的任务数。
        """
        if not isinstance(tasks, list) or not tasks:
            return 0
        if self.get_plan(plan_id) is None:
            raise ValueError(f"plan_id={plan_id} 不存在")

        rows = []
        for t in tasks:
            stage_idx = int(t.get("stage_idx", 0))
            order_idx = int(t.get("order_idx", 0))
            title = (t.get("title") or "").strip()
            if stage_idx < 1 or order_idx < 1 or not title:
                logger.warning("add_tasks: 跳过非法 task: %r", t)
                continue
            rows.append((plan_id, stage_idx, order_idx, title))

        if not rows:
            return 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO learning_tasks(plan_id, stage_idx, order_idx, title) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.execute(
                "UPDATE learning_plans SET updated_at = ? WHERE id = ?",
                (self._now(), plan_id),
            )
        logger.info("add_tasks: plan_id=%d, +%d task", plan_id, len(rows))
        return len(rows)

    def get_plan(self, plan_id: int) -> dict[str, Any] | None:
        """读单个 plan 元信息（不含 tasks）。"""
        row = self._conn.execute(
            "SELECT * FROM learning_plans WHERE id = ?", (plan_id,),
        ).fetchone()
        return self._row_to_plan(row) if row else None

    def get_plan_with_tasks(self, plan_id: int) -> dict[str, Any] | None:
        """读单个 plan + 全部 tasks（按 stage_idx, order_idx 升序）。"""
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        plan["tasks"] = self._get_tasks(plan_id)
        return plan

    def get_active(self) -> dict[str, Any] | None:
        """读当前 active plan + 全部 tasks；无 active 返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM learning_plans WHERE is_active = 1 LIMIT 1",
        ).fetchone()
        if not row:
            return None
        plan = self._row_to_plan(row)
        plan["tasks"] = self._get_tasks(plan["id"])
        return plan

    def list_plans(self, include_abandoned: bool = False) -> list[dict[str, Any]]:
        """
        列全部 plan 摘要（含 task 计数 + 完成数），按 active 优先 + 创建时间倒序。

        Args:
            include_abandoned: 是否包含 abandoned 状态的 plan，默认不含。
        """
        sql = """
            SELECT p.*,
                   COUNT(t.id) AS task_count,
                   SUM(CASE WHEN t.status = 'success' THEN 1 ELSE 0 END) AS done_count
            FROM learning_plans p
            LEFT JOIN learning_tasks t ON p.id = t.plan_id
        """
        params: list[Any] = []
        if not include_abandoned:
            sql += " WHERE p.status != 'abandoned'"
        # id DESC 作 created_at tie-breaker：批量测试 / 同秒新建时按"新→旧"稳定排
        sql += " GROUP BY p.id ORDER BY p.is_active DESC, p.created_at DESC, p.id DESC"
        rows = self._conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            plan = self._row_to_plan(r)
            plan["task_count"] = int(r["task_count"] or 0)
            plan["done_count"] = int(r["done_count"] or 0)
            out.append(plan)
        return out

    # ── active 标记互斥维护（D9） ─────────────────────────────────────────────

    def switch_active(self, plan_id: int) -> bool:
        """
        把指定 plan 切为 active；其它 plan 自动 set is_active=0。

        Returns:
            True 切换成功；False plan 不存在 / 已 abandoned。
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            logger.warning("switch_active: plan_id=%d 不存在", plan_id)
            return False
        if plan["status"] == "abandoned":
            logger.warning("switch_active: plan_id=%d 已 abandoned，不能切换", plan_id)
            return False

        now = self._now()
        with self._conn:
            self._conn.execute(
                "UPDATE learning_plans SET is_active = 0, updated_at = ? "
                "WHERE is_active = 1 AND id != ?",
                (now, plan_id),
            )
            self._conn.execute(
                "UPDATE learning_plans SET is_active = 1, updated_at = ? WHERE id = ?",
                (now, plan_id),
            )
        logger.info("switch_active: plan_id=%d", plan_id)
        return True

    def abandon_plan(self, plan_id: int) -> bool:
        """
        将指定 plan 标为 abandoned + 置非 active；plan 不存在返回 False。
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            return False
        now = self._now()
        with self._conn:
            self._conn.execute(
                "UPDATE learning_plans SET status = 'abandoned', is_active = 0, updated_at = ? "
                "WHERE id = ?",
                (now, plan_id),
            )
        logger.info("abandon_plan: plan_id=%d", plan_id)
        return True

    def complete_plan(self, plan_id: int) -> bool:
        """
        将指定 plan 标为 completed + 置非 active（所有 task 完成时由业务层调用）。
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            return False
        now = self._now()
        with self._conn:
            self._conn.execute(
                "UPDATE learning_plans SET status = 'completed', is_active = 0, updated_at = ? "
                "WHERE id = ?",
                (now, plan_id),
            )
        logger.info("complete_plan: plan_id=%d", plan_id)
        return True

    def delete_plan(self, plan_id: int) -> bool:
        """
        硬删除 plan + 级联删除其 tasks（ON DELETE CASCADE）。
        主要给测试 / 手动清理用；常规业务请用 abandon_plan。
        """
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM learning_plans WHERE id = ?", (plan_id,),
            )
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("delete_plan: plan_id=%d", plan_id)
        return deleted

    # ── task CRUD ────────────────────────────────────────────────────────────

    def _get_tasks(self, plan_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM learning_tasks WHERE plan_id = ? "
            "ORDER BY stage_idx ASC, order_idx ASC, id ASC",
            (plan_id,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update_task_status(
        self, plan_id: int, task_id: int, status: str, note: str = "",
    ) -> bool:
        """
        更新指定 task 的 status / note。

        Args:
            plan_id: 隶属 plan id（双重校验：防止跨 plan 误更新）。
            task_id: task id。
            status: pending / success / skipped 之一。
            note: 可选备注（≤ 200 字，超出截断）。

        Returns:
            True 更新成功；False task 不存在 / 不属于该 plan / status 非法。
        """
        if status not in _TASK_STATUS:
            logger.warning("update_task_status: 非法 status=%r", status)
            return False

        row = self._conn.execute(
            "SELECT id FROM learning_tasks WHERE id = ? AND plan_id = ?",
            (task_id, plan_id),
        ).fetchone()
        if not row:
            logger.warning("update_task_status: task_id=%d 不属于 plan_id=%d",
                           task_id, plan_id)
            return False

        now = self._now()
        completed_at = now if status in ("success", "skipped") else ""
        note = (note or "")[:200]
        with self._conn:
            self._conn.execute(
                "UPDATE learning_tasks SET status = ?, note = ?, completed_at = ? "
                "WHERE id = ?",
                (status, note, completed_at, task_id),
            )
            self._conn.execute(
                "UPDATE learning_plans SET updated_at = ? WHERE id = ?",
                (now, plan_id),
            )
        return True

    # ── session 级 loaded plan 映射（手动 /study load）─────────────────────
    # 对标 Agent Skills 的 load_skill 生命周期：默认不注入，用户用 `/study load [id]`
    # 手动激活；切 session 自然清空（因 session_id 变了 dict 里查不到）。
    # 不提供"卸载"命令 —— 新建 session 即天然清空。详 design.md §3.9.4。

    def mark_loaded(self, session_id: str, plan_id: int) -> bool:
        """
        将指定 plan 标记为当前 session 已"加载"到 prompt 注入。

        Args:
            session_id: 当前会话 id。
            plan_id: 要加载的 plan id；必须存在且非 abandoned。

        Returns:
            True 标记成功；False plan 不存在 / 已 abandoned（不写映射）。
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            logger.warning("mark_loaded: plan_id=%d 不存在", plan_id)
            return False
        if plan["status"] == "abandoned":
            logger.warning("mark_loaded: plan_id=%d 已 abandoned，拒绝 load", plan_id)
            return False
        self._loaded_by_session[session_id] = plan_id
        logger.info("mark_loaded: session=%s, plan_id=%d", session_id, plan_id)
        return True

    def get_loaded(self, session_id: str) -> int | None:
        """
        读当前 session 已加载的 plan_id；含 stale 自动清理（plan 已被 abandon /
        delete 时返回 None 并从映射中 evict，避免后续误注入失效内容）。
        """
        pid = self._loaded_by_session.get(session_id)
        if pid is None:
            return None
        plan = self.get_plan(pid)
        if plan is None or plan["status"] == "abandoned":
            # stale：上次 load 后 plan 被 abandon / delete，自动 evict
            self._loaded_by_session.pop(session_id, None)
            logger.info("get_loaded: session=%s 的 plan_id=%d 已失效，自动清除", session_id, pid)
            return None
        return pid

    def clear_loaded(self, session_id: str | None = None) -> None:
        """
        清除 loaded 映射。
        session_id 给定 → 清单个；None → 清全部（主要给 UT / 进程级 reset）。
        """
        if session_id is None:
            self._loaded_by_session.clear()
        else:
            self._loaded_by_session.pop(session_id, None)

    # ── 摘要 / 上下文注入 helper ─────────────────────────────────────────────

    def render_plan_for_prompt(self, plan_id: int, max_chars: int = 1500) -> str:
        """
        把指定 plan 渲染成可注入 system prompt 的 markdown 块；
        plan 不存在 / 已 abandoned 时返回空字符串（caller 凭此跳过注入）。
        超出 max_chars 自动截断（保留前部 + 末尾标注 "...截断..."）。
        """
        plan = self.get_plan_with_tasks(plan_id)
        if plan is None or plan["status"] == "abandoned":
            return ""

        lines = [
            f"## 当前学习计划（plan_id={plan['id']}）",
            f"**目标**：{plan['goal']}",
        ]
        if plan["weeks"]:
            lines.append(f"**周期**：{plan['weeks']} 周")
        tasks = plan.get("tasks", [])
        done = sum(1 for t in tasks if t["status"] == "success")
        total = len(tasks)
        lines.append(f"**进度**：{done}/{total} 完成")
        lines.append("")

        # 按 stage 分组渲染
        if tasks:
            current_stage = None
            for t in tasks:
                if t["stage_idx"] != current_stage:
                    current_stage = t["stage_idx"]
                    lines.append(f"### Stage {current_stage}")
                icon = {"pending": "☐", "success": "✓", "skipped": "⏭️"}.get(t["status"], "?")
                note_suffix = f" — {t['note']}" if t["note"] else ""
                lines.append(f"- {icon} [task_id={t['id']}] {t['title']}{note_suffix}")

        out = "\n".join(lines)
        if len(out) > max_chars:
            out = out[: max_chars - 20] + "\n...（已截断）..."
        return out

    # ── 资源管理 ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "LearningPlanStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── 进程内单例 helper ───────────────────────────────────────────────────────
# 给 tools.py（写）+ agent.py（注入 system block 时读）等多个调用方共享同一连接；
# 避免不同模块各自 `LearningPlanStore()` 导致同进程多连接 / SQLite write-lock 冲突。

_shared_store: LearningPlanStore | None = None


def get_shared_store() -> LearningPlanStore:
    """获取进程级共享 LearningPlanStore；首次调用懒加载。"""
    global _shared_store
    if _shared_store is None:
        _shared_store = LearningPlanStore()
    return _shared_store


def reset_shared_store_for_testing(store: LearningPlanStore | None = None) -> None:
    """
    UT 专用：注入 mock store / 重置为 None（让下次 get_shared_store 懒加载真实 store）。
    生产代码不要调用。
    """
    global _shared_store
    _shared_store = store
