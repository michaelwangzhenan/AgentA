"""
Quiz 出题持久化模块 —— SQLite 存储层（Phase 2.3 §4.9.8 D1 / D9 / D10）

将 Agent 给用户生成的 quiz_set + 每道 quiz_question 持久化到本地 SQLite
（默认 ./sqlite_db/quiz.db）。区别于 [§4.9.7 学习计划](../../docs/iter_2_agent.md#497-学习计划生成-phase-22)
的"周/月级长期目标跟踪"，本期是"周期性自检练习"：一次性出 5-15 题、用户作答后批改、
跨 session 留档用于复盘 / 喂 Phase 2.4 SRS。

表结构：
    quiz_sets(
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        topic           TEXT    NOT NULL,                  -- 出题主题
        plan_id         INTEGER,                           -- 可选关联 learning_plans（软引用，plan abandon 不级联）
        stage_idx       INTEGER,                           -- 可选关联 plan stage
        num_questions   INTEGER NOT NULL,                  -- 题数（落库当下的实际数量）
        status          TEXT    NOT NULL DEFAULT 'created',-- created / graded / archived
        total_score     REAL,                              -- 批改总分（0-100），NULL = 未批
        created_at      TEXT    NOT NULL,
        graded_at       TEXT    NOT NULL DEFAULT '',       -- '' 表示未批改
        updated_at      TEXT    NOT NULL
    )
    quiz_questions(
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_set_id     INTEGER NOT NULL REFERENCES quiz_sets(id) ON DELETE CASCADE,
        order_idx       INTEGER NOT NULL,                  -- 题号，从 1 起
        q_type          TEXT    NOT NULL,                  -- mcq_single / mcq_multi / short_answer
        stem            TEXT    NOT NULL,                  -- 题干
        options         TEXT    NOT NULL DEFAULT '',       -- MCQ 选项 JSON 数组字符串；简答留空
        correct_answer  TEXT    NOT NULL,                  -- MCQ: "A" / "AC"；简答: 标准答案文本
        explanation     TEXT    NOT NULL DEFAULT '',       -- 简短考点说明
        user_answer     TEXT    NOT NULL DEFAULT '',       -- 用户作答（批改时填）
        score           REAL    NOT NULL DEFAULT 0.0,      -- 单题得分 0.0-1.0（MCQ 整对 1.0 / 否则 0；简答按 LLM-judge）
        feedback        TEXT    NOT NULL DEFAULT '',       -- 批改反馈（string-match 简评 / LLM 反馈）
        harness_flagged INTEGER NOT NULL DEFAULT 0         -- Phase 2.5：critic 自检判定本题批改可能有偏（0/1）
    )

跨 session 复盘场景：用户重启 agent → 新 session 问"上次 quiz 哪些错了 / 列出我做过的 quiz"
→ Agent 调 `query_quiz_history` tool → 走本 store 查全部 → 渲染给用户。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

QUIZ_DB_PATH: str = config.QUIZ_DB_PATH

# quiz_set 合法状态枚举
_QUIZ_SET_STATUS: tuple[str, ...] = ("created", "graded", "archived")
# quiz_question 合法题型枚举（D10）
_QUESTION_TYPES: tuple[str, ...] = ("mcq_single", "mcq_multi", "short_answer")


class QuizStore:
    """
    SQLite Quiz 存储（CRUD 依赖层）。

    职责单一：quiz_set / quiz_question 的 create / read / update（批改）/ archive / delete。
    不感知"如何生成题 / 如何批改"等业务策略 ——
    这些由 [quiz-maker skill](../../.agenta/skills/quiz-maker/SKILL.md)
    + [tools.py Quiz 业务 tool](../agent/tools.py) 在 Agent loop 内驱动。

    命名约定（[agenta-conventions.mdc §2](../../.cursor/rules/agenta-conventions.mdc)）：
    数据存储用 `*Store` 后缀，与 `*Manager` helper 区分。
    """

    def __init__(self, db_path: str = QUIZ_DB_PATH) -> None:
        """初始化存储，自动创建数据库文件和表结构。"""
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._create_tables()
        logger.info("QuizStore 初始化完成: %s", db_path)

    # ── 表结构初始化 ──────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """创建 quiz_sets / quiz_questions 表（幂等）+ fail-fast 检测旧 schema。

        Phase 2.5 起 quiz_questions 加 `harness_flagged` 列。沿用 [`UserMemoryStore`](user_memory.py)
        的 fail-fast 模式：旧 quiz.db 升级时不做 ALTER TABLE auto-migrate，PRAGMA 自检
        缺列直接抛 RuntimeError 提示删库重建（单用户 MVP 场景损失可接受，避免引入迁移代码）。
        """
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS quiz_sets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                topic           TEXT    NOT NULL,
                plan_id         INTEGER,
                stage_idx       INTEGER,
                num_questions   INTEGER NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'created',
                total_score     REAL,
                created_at      TEXT    NOT NULL,
                graded_at       TEXT    NOT NULL DEFAULT '',
                updated_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quiz_sets_status
                ON quiz_sets(status);
            CREATE INDEX IF NOT EXISTS idx_quiz_sets_plan
                ON quiz_sets(plan_id);

            CREATE TABLE IF NOT EXISTS quiz_questions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_set_id     INTEGER NOT NULL REFERENCES quiz_sets(id) ON DELETE CASCADE,
                order_idx       INTEGER NOT NULL,
                q_type          TEXT    NOT NULL,
                stem            TEXT    NOT NULL,
                options         TEXT    NOT NULL DEFAULT '',
                correct_answer  TEXT    NOT NULL,
                explanation     TEXT    NOT NULL DEFAULT '',
                user_answer     TEXT    NOT NULL DEFAULT '',
                score           REAL    NOT NULL DEFAULT 0.0,
                feedback        TEXT    NOT NULL DEFAULT '',
                harness_flagged INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_quiz_questions_set
                ON quiz_questions(quiz_set_id, order_idx);
        """)
        self._conn.commit()

        # fail-fast：旧 quiz.db 升级到 Phase 2.5 时，PRAGMA 自检缺 harness_flagged 列
        # 直接抛 RuntimeError 让用户删库重建（不走 ALTER TABLE auto-migrate）
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(quiz_questions)")}
        if "harness_flagged" not in cols:
            raise RuntimeError(
                f"quiz.db schema 已过期（缺 harness_flagged 列，Phase 2.5 引入）。\n"
                f"请删除 {self._db_path} 后重启（单用户 MVP 不做向后兼容迁移）。"
            )

    # ── 内部 helper ───────────────────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _row_to_quiz_set(row: sqlite3.Row) -> dict[str, Any]:
        """SQLite 行 → quiz_set dict（不含 questions）。"""
        return {
            "id": row["id"],
            "topic": row["topic"],
            "plan_id": row["plan_id"],
            "stage_idx": row["stage_idx"],
            "num_questions": row["num_questions"],
            "status": row["status"],
            "total_score": row["total_score"],
            "created_at": row["created_at"],
            "graded_at": row["graded_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_question(row: sqlite3.Row) -> dict[str, Any]:
        """SQLite 行 → question dict；options 串自动 JSON 解析回 list。"""
        opts_raw = row["options"] or ""
        try:
            options = json.loads(opts_raw) if opts_raw else []
        except (json.JSONDecodeError, TypeError):
            options = []
        return {
            "id": row["id"],
            "quiz_set_id": row["quiz_set_id"],
            "order_idx": row["order_idx"],
            "q_type": row["q_type"],
            "stem": row["stem"],
            "options": options,
            "correct_answer": row["correct_answer"],
            "explanation": row["explanation"],
            "user_answer": row["user_answer"],
            "score": float(row["score"] or 0.0),
            "feedback": row["feedback"],
            "harness_flagged": bool(row["harness_flagged"]),
        }

    # ── quiz_set CRUD ────────────────────────────────────────────────────────

    def create_quiz_set(
        self,
        topic: str,
        num_questions: int,
        plan_id: int | None = None,
        stage_idx: int | None = None,
    ) -> int:
        """
        新建一个 quiz_set 记录（status 默认 created，未批改）。

        Args:
            topic: 出题主题（用户给的关键词或 plan stage 标题）。
            num_questions: 题数（≥ 1）。
            plan_id: 可选关联的 learning_plan id（软引用）。
            stage_idx: 可选关联的 plan stage（≥ 1）。

        Returns:
            新 quiz_set 的 id（自增整数）。
        """
        topic = (topic or "").strip()
        if not topic:
            raise ValueError("topic 不能为空")
        if num_questions < 1:
            raise ValueError(f"num_questions 必须 ≥ 1，收到 {num_questions!r}")
        if plan_id is not None and plan_id < 1:
            raise ValueError(f"plan_id 必须 ≥ 1 或 None，收到 {plan_id!r}")
        if stage_idx is not None and stage_idx < 1:
            raise ValueError(f"stage_idx 必须 ≥ 1 或 None，收到 {stage_idx!r}")

        now = self._now()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO quiz_sets(topic, plan_id, stage_idx, num_questions, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'created', ?, ?)",
                (topic, plan_id, stage_idx, num_questions, now, now),
            )
        quiz_set_id = int(cursor.lastrowid or 0)
        logger.info(
            "create_quiz_set: id=%d, topic=%r, n=%d, plan=%s, stage=%s",
            quiz_set_id, topic, num_questions, plan_id, stage_idx,
        )
        return quiz_set_id

    def add_questions(self, quiz_set_id: int, questions: list[dict[str, Any]]) -> int:
        """
        批量给指定 quiz_set 添加题目。

        Args:
            quiz_set_id: quiz_set id。
            questions: 题目列表，每项 {order_idx, q_type, stem, options(list[str]?),
                       correct_answer, explanation?}。

        Returns:
            插入的题目数；非法 row 静默跳过并 log warning。
        """
        if not isinstance(questions, list) or not questions:
            return 0
        if self.get_quiz_set(quiz_set_id) is None:
            raise ValueError(f"quiz_set_id={quiz_set_id} 不存在")

        rows: list[tuple[Any, ...]] = []
        for q in questions:
            order_idx = int(q.get("order_idx", 0))
            q_type = (q.get("q_type") or "").strip()
            stem = (q.get("stem") or "").strip()
            correct_answer = (q.get("correct_answer") or "").strip()
            if (order_idx < 1 or q_type not in _QUESTION_TYPES
                    or not stem or not correct_answer):
                logger.warning("add_questions: 跳过非法 question: %r", q)
                continue
            options_raw = q.get("options") or []
            if isinstance(options_raw, list):
                # 序列化为 JSON 串保留顺序与原文；简答的 options=[] 也会被序列化为 "[]"
                options_str = json.dumps(options_raw, ensure_ascii=False) if options_raw else ""
            elif isinstance(options_raw, str):
                options_str = options_raw  # 已是序列化串
            else:
                options_str = ""
            explanation = (q.get("explanation") or "").strip()
            rows.append((
                quiz_set_id, order_idx, q_type, stem,
                options_str, correct_answer, explanation,
            ))

        if not rows:
            return 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO quiz_questions(quiz_set_id, order_idx, q_type, "
                "stem, options, correct_answer, explanation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.execute(
                "UPDATE quiz_sets SET updated_at = ? WHERE id = ?",
                (self._now(), quiz_set_id),
            )
        logger.info("add_questions: quiz_set_id=%d, +%d question", quiz_set_id, len(rows))
        return len(rows)

    def get_quiz_set(self, quiz_set_id: int) -> dict[str, Any] | None:
        """读单个 quiz_set 元信息（不含 questions）。"""
        row = self._conn.execute(
            "SELECT * FROM quiz_sets WHERE id = ?", (quiz_set_id,),
        ).fetchone()
        return self._row_to_quiz_set(row) if row else None

    def get_quiz_with_questions(self, quiz_set_id: int) -> dict[str, Any] | None:
        """读单个 quiz_set + 全部 questions（按 order_idx 升序）。"""
        quiz_set = self.get_quiz_set(quiz_set_id)
        if quiz_set is None:
            return None
        quiz_set["questions"] = self._get_questions(quiz_set_id)
        return quiz_set

    def list_quiz_sets(
        self,
        plan_id: int | None = None,
        limit: int | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """
        列 quiz_set 摘要，按创建时间倒序 + id 倒序作稳定 tie-breaker。

        Args:
            plan_id: 可选过滤；只列与该 plan 绑定的 quiz。
            limit: 可选条数上限；None 不限。
            include_archived: 是否包含 archived 状态；默认不含。
        """
        sql = "SELECT * FROM quiz_sets"
        clauses: list[str] = []
        params: list[Any] = []
        if not include_archived:
            clauses.append("status != 'archived'")
        if plan_id is not None:
            clauses.append("plan_id = ?")
            params.append(plan_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # created_at DESC + id DESC：同秒新建时按"新→旧"稳定排（仿 §4.9.7 list_plans）
        sql += " ORDER BY created_at DESC, id DESC"
        if isinstance(limit, int) and limit > 0:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_quiz_set(r) for r in rows]

    # ── 批改 ─────────────────────────────────────────────────────────────────

    def update_grading(
        self,
        quiz_set_id: int,
        gradings: list[dict[str, Any]],
        total_score: float,
    ) -> bool:
        """
        批量写入批改结果 + 同步 quiz_set 状态为 graded。

        Args:
            quiz_set_id: quiz_set id。
            gradings: list of {question_id, user_answer, score(0-1), feedback?}。
            total_score: 总分（0-100 标准化分）；写入 quiz_sets.total_score。

        Returns:
            True 全部更新成功；False quiz_set 不存在 / archived。
        """
        quiz_set = self.get_quiz_set(quiz_set_id)
        if quiz_set is None:
            logger.warning("update_grading: quiz_set_id=%d 不存在", quiz_set_id)
            return False
        if quiz_set["status"] == "archived":
            logger.warning("update_grading: quiz_set_id=%d 已 archived，禁止改", quiz_set_id)
            return False
        if not isinstance(gradings, list):
            return False

        now = self._now()
        ts = max(0.0, min(100.0, float(total_score)))

        # 收集 question_id → quiz_set_id 防止跨 set 误改
        rows = self._conn.execute(
            "SELECT id FROM quiz_questions WHERE quiz_set_id = ?", (quiz_set_id,),
        ).fetchall()
        valid_qids = {int(r["id"]) for r in rows}

        with self._conn:
            for g in gradings:
                qid = g.get("question_id")
                if not isinstance(qid, int) or qid not in valid_qids:
                    logger.warning(
                        "update_grading: 跳过 question_id=%r（不属于 quiz_set=%d）",
                        qid, quiz_set_id,
                    )
                    continue
                user_answer = str(g.get("user_answer") or "").strip()
                score = max(0.0, min(1.0, float(g.get("score") or 0.0)))
                feedback = str(g.get("feedback") or "").strip()[:500]
                self._conn.execute(
                    "UPDATE quiz_questions SET user_answer = ?, score = ?, feedback = ? "
                    "WHERE id = ?",
                    (user_answer, score, feedback, qid),
                )
            self._conn.execute(
                "UPDATE quiz_sets SET status = 'graded', total_score = ?, "
                "graded_at = ?, updated_at = ? WHERE id = ?",
                (ts, now, now, quiz_set_id),
            )
        logger.info(
            "update_grading: quiz_set_id=%d, total=%.1f, updated %d gradings",
            quiz_set_id, ts, len(gradings),
        )
        return True

    # ── 归档 / 删除 ──────────────────────────────────────────────────────────

    def mark_question_harness_flagged(self, question_id: int) -> bool:
        """Phase 2.5：把指定题号的 `harness_flagged` 置 1（critic 自检判定批改可能有偏）。

        Returns:
            True 该题存在且更新成功；False 题不存在。
        """
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE quiz_questions SET harness_flagged = 1 WHERE id = ?",
                (question_id,),
            )
        flagged = cursor.rowcount > 0
        if flagged:
            logger.info("mark_question_harness_flagged: question_id=%d", question_id)
        return flagged

    # ── 归档 / 删除 ──────────────────────────────────────────────────────────

    def archive_quiz_set(self, quiz_set_id: int) -> bool:
        """将指定 quiz_set 标为 archived；不存在返回 False。数据保留可后续查。"""
        quiz_set = self.get_quiz_set(quiz_set_id)
        if quiz_set is None:
            return False
        if quiz_set["status"] == "archived":
            return False
        now = self._now()
        with self._conn:
            self._conn.execute(
                "UPDATE quiz_sets SET status = 'archived', updated_at = ? WHERE id = ?",
                (now, quiz_set_id),
            )
        logger.info("archive_quiz_set: quiz_set_id=%d", quiz_set_id)
        return True

    def delete_quiz_set(self, quiz_set_id: int) -> bool:
        """
        硬删除 quiz_set + 级联删除其 questions（ON DELETE CASCADE）。

        给 CLI `/quiz del` 与测试清理用；常规业务请用 archive_quiz_set。
        """
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM quiz_sets WHERE id = ?", (quiz_set_id,),
            )
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("delete_quiz_set: quiz_set_id=%d", quiz_set_id)
        return deleted

    # ── question CRUD（仅内部 / 测试用） ─────────────────────────────────────

    def _get_questions(self, quiz_set_id: int) -> list[dict[str, Any]]:
        """读 quiz_set 全部 questions（按 order_idx 升序）。"""
        rows = self._conn.execute(
            "SELECT * FROM quiz_questions WHERE quiz_set_id = ? "
            "ORDER BY order_idx ASC, id ASC",
            (quiz_set_id,),
        ).fetchall()
        return [self._row_to_question(r) for r in rows]

    # ── 资源管理 ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "QuizStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── 进程内单例 helper ───────────────────────────────────────────────────────
# 给 tools.py（写）+ CLI handlers（读/查/删）共享同一连接；
# 避免不同模块各自 `QuizStore()` 导致同进程多连接 / SQLite write-lock 冲突。

_shared_store: QuizStore | None = None


def get_shared_store() -> QuizStore:
    """获取进程级共享 QuizStore；首次调用懒加载。"""
    global _shared_store
    if _shared_store is None:
        _shared_store = QuizStore()
    return _shared_store


def reset_shared_store_for_testing(store: QuizStore | None = None) -> None:
    """
    UT 专用：注入 mock store / 重置为 None（让下次 get_shared_store 懒加载真实 store）。
    生产代码不要调用。
    """
    global _shared_store
    _shared_store = store
