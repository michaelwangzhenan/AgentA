"""Token 用量统计存储层（iter_11）。

每用户独立的 token 用量记录，独立 SQLite（默认 ``./sqlite_db/usage.db``），与会话 /
记忆等业务库分开，便于单独备份 / 清理。

口径：**一次 ``Agent.run()`` 记一行**（per-run，是 PYTHON / LANGCHAIN / AUTOGPT 三种
实现唯一一致的粒度，详 ``docs/iter_11_token.md`` §3 / §4.1）。

两张表：
    usage_events(id, user_id, created_at(epoch 秒), model_id, thinking 0/1,
                 prompt_tokens, completion_tokens, total_tokens, session_id)
    model_pricing(model_id PK, input_price, output_price, updated_at)
        -- admin 在「用量 → 单价配置」里覆盖的单价；读取时与
           ``config.MODEL_PRICING_DEFAULTS`` 合并（覆盖优先）。

成本不落库：单价会调整，存死会失真，查询时按当前合并单价实时算。
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

USAGE_DB_PATH: str = config.USAGE_DB_PATH


class UsageStore:
    """Token 用量记录 + 单价覆盖存储（CRUD 依赖层）。

    内置 ``threading.Lock``，可被多线程安全读写（与其它 store 一致）。
    """

    def __init__(self, db_path: str = USAGE_DB_PATH) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()
        logger.info("UsageStore 初始化完成: %s", self._db_path)

    def _create_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS usage_events (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id           INTEGER NOT NULL,
                    created_at        INTEGER NOT NULL,
                    model_id          TEXT    NOT NULL,
                    thinking          INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    session_id        TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_usage_user_time
                    ON usage_events(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_usage_time
                    ON usage_events(created_at);
                CREATE TABLE IF NOT EXISTS model_pricing (
                    model_id      TEXT PRIMARY KEY,
                    input_price   REAL NOT NULL DEFAULT 0,
                    output_price  REAL NOT NULL DEFAULT 0,
                    updated_at    INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saving_events (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        INTEGER NOT NULL,
                    created_at     INTEGER NOT NULL,
                    kind           TEXT    NOT NULL,   -- route | cache
                    original_model TEXT    NOT NULL DEFAULT '',
                    used_model     TEXT    NOT NULL DEFAULT '',
                    saved_cost     REAL    NOT NULL DEFAULT 0,
                    total_tokens   INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_saving_user_time
                    ON saving_events(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_saving_time
                    ON saving_events(created_at);
                CREATE TABLE IF NOT EXISTS cache_lookups (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    created_at  INTEGER NOT NULL,
                    hit         INTEGER NOT NULL DEFAULT 0,
                    saved       REAL    NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_cache_lookup_time
                    ON cache_lookups(created_at);
            """)
            # 旧库补列：cache_lookups 早期无 saved（缓存的次数/节省/命中率统一以此表为准）
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(cache_lookups)")}
            if "saved" not in cols:
                self._conn.execute(
                    "ALTER TABLE cache_lookups ADD COLUMN saved REAL NOT NULL DEFAULT 0"
                )

    @staticmethod
    def _now() -> int:
        return int(time.time())

    # ── 写入 ────────────────────────────────────────────────────────────────

    def record(
        self,
        user_id: int,
        model_id: str,
        thinking: bool,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int | None = None,
        session_id: str | None = None,
        created_at: int | None = None,
    ) -> None:
        """记录一次 run 的 token 用量（一行）。

        旁路调用：调用方应吞掉异常、不影响主链路（见 ``record_usage``）。
        """
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        t = int(total_tokens) if total_tokens is not None else (p + c)
        ts = int(created_at) if created_at is not None else self._now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO usage_events"
                "(user_id, created_at, model_id, thinking, prompt_tokens, "
                " completion_tokens, total_tokens, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(user_id), ts, str(model_id), 1 if thinking else 0,
                 p, c, t, session_id),
            )

    # ── 聚合查询 ──────────────────────────────────────────────────────────────

    def aggregate_by_model(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """按 model_id 聚合（用于概览卡片 + 成本计算）。"""
        sql = (
            "SELECT model_id, "
            "SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, "
            "SUM(total_tokens) AS total_tokens, "
            "COUNT(*) AS cnt "
            "FROM usage_events WHERE created_at >= ? AND created_at < ?"
        )
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(int(user_id))
        sql += " GROUP BY model_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._agg_row(r) for r in rows]

    def aggregate_series(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """按 (天, user_id, model_id) 聚合（趋势图原料）。

        返回每行带 day(YYYY-MM-DD, 本地时区) / user_id / model_id / tokens / cnt。
        路由层按需 rollup 到 model / user / none，并叠加单价算成本。
        """
        sql = (
            "SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') AS day, "
            "user_id, model_id, "
            "SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, "
            "SUM(total_tokens) AS total_tokens, "
            "COUNT(*) AS cnt "
            "FROM usage_events WHERE created_at >= ? AND created_at < ?"
        )
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(int(user_id))
        sql += " GROUP BY day, user_id, model_id ORDER BY day ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "day": r["day"],
                "user_id": int(r["user_id"]),
                "model_id": r["model_id"],
                "prompt_tokens": int(r["prompt_tokens"] or 0),
                "completion_tokens": int(r["completion_tokens"] or 0),
                "total_tokens": int(r["total_tokens"] or 0),
                "count": int(r["cnt"] or 0),
            }
            for r in rows
        ]

    def aggregate_by_user_model(
        self, start_ts: int, end_ts: int
    ) -> list[dict[str, Any]]:
        """按 (user_id, model_id) 聚合（全员用户排行 + 成本）。"""
        sql = (
            "SELECT user_id, model_id, "
            "SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, "
            "SUM(total_tokens) AS total_tokens, "
            "COUNT(*) AS cnt "
            "FROM usage_events WHERE created_at >= ? AND created_at < ? "
            "GROUP BY user_id, model_id"
        )
        with self._lock:
            rows = self._conn.execute(sql, [int(start_ts), int(end_ts)]).fetchall()
        return [
            {
                "user_id": int(r["user_id"]),
                "model_id": r["model_id"],
                "prompt_tokens": int(r["prompt_tokens"] or 0),
                "completion_tokens": int(r["completion_tokens"] or 0),
                "total_tokens": int(r["total_tokens"] or 0),
                "count": int(r["cnt"] or 0),
            }
            for r in rows
        ]

    @staticmethod
    def _agg_row(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "model_id": r["model_id"],
            "prompt_tokens": int(r["prompt_tokens"] or 0),
            "completion_tokens": int(r["completion_tokens"] or 0),
            "total_tokens": int(r["total_tokens"] or 0),
            "count": int(r["cnt"] or 0),
        }

    # ── 明细 ────────────────────────────────────────────────────────────────

    def list_events(
        self,
        start_ts: int,
        end_ts: int,
        user_id: int | None = None,
        model_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """明细分页（时间倒序）。返回 (rows, total)。"""
        where = "created_at >= ? AND created_at < ?"
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(int(user_id))
        if model_id:
            where += " AND model_id = ?"
            params.append(model_id)
        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM usage_events WHERE {where}", params
                ).fetchone()[0]
            )
            rows = self._conn.execute(
                f"SELECT * FROM usage_events WHERE {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        events = [
            {
                "id": int(r["id"]),
                "user_id": int(r["user_id"]),
                "created_at": int(r["created_at"]),
                "model_id": r["model_id"],
                "thinking": bool(r["thinking"]),
                "prompt_tokens": int(r["prompt_tokens"] or 0),
                "completion_tokens": int(r["completion_tokens"] or 0),
                "total_tokens": int(r["total_tokens"] or 0),
                "session_id": r["session_id"],
            }
            for r in rows
        ]
        return events, total

    # ── 单价覆盖（admin） ──────────────────────────────────────────────────────

    def get_pricing_overrides(self) -> dict[str, tuple[float, float]]:
        """读所有单价覆盖：{model_id: (input, output)}。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT model_id, input_price, output_price FROM model_pricing"
            ).fetchall()
        return {
            r["model_id"]: (float(r["input_price"]), float(r["output_price"]))
            for r in rows
        }

    def set_pricing(self, model_id: str, input_price: float, output_price: float) -> None:
        """upsert 单个模型单价覆盖。"""
        now = self._now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO model_pricing(model_id, input_price, output_price, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(model_id) DO UPDATE SET "
                "input_price = excluded.input_price, "
                "output_price = excluded.output_price, "
                "updated_at = excluded.updated_at",
                (str(model_id), float(input_price), float(output_price), now),
            )

    def set_pricing_bulk(self, pricing: dict[str, tuple[float, float]]) -> None:
        """批量 upsert 单价覆盖（保存整张单价表时用）。"""
        now = self._now()
        with self._lock, self._conn:
            for mid, (pin, pout) in pricing.items():
                self._conn.execute(
                    "INSERT INTO model_pricing(model_id, input_price, output_price, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(model_id) DO UPDATE SET "
                    "input_price = excluded.input_price, "
                    "output_price = excluded.output_price, "
                    "updated_at = excluded.updated_at",
                    (str(mid), float(pin), float(pout), now),
                )

    def delete_pricing(self, model_id: str) -> None:
        """清除某模型的单价覆盖（回落到内置默认）。"""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM model_pricing WHERE model_id = ?", (str(model_id),)
            )

    # ── 降本节省记录（路由 / 缓存命中） ──────────────────────────────────────────

    def record_saving(
        self,
        user_id: int,
        kind: str,
        original_model: str,
        used_model: str,
        saved_cost: float,
        total_tokens: int = 0,
        created_at: int | None = None,
    ) -> None:
        """记录一次降本事件（路由降级 / 缓存命中）。旁路调用，调用方吞异常。"""
        ts = int(created_at) if created_at is not None else self._now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO saving_events"
                "(user_id, created_at, kind, original_model, used_model, saved_cost, total_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(user_id), ts, str(kind), str(original_model or ""),
                 str(used_model or ""), float(saved_cost or 0.0), int(total_tokens or 0)),
            )

    def aggregate_savings(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> dict[str, Any]:
        """按 kind 汇总降本（次数 + 估算节省）。"""
        sql = (
            "SELECT kind, COUNT(*) AS cnt, SUM(saved_cost) AS saved "
            "FROM saving_events WHERE created_at >= ? AND created_at < ?"
        )
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(int(user_id))
        sql += " GROUP BY kind"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = {
            "route_count": 0, "route_saved": 0.0,
            "cache_count": 0, "cache_saved": 0.0,
        }
        for r in rows:
            if r["kind"] == "route":
                out["route_count"] = int(r["cnt"] or 0)
                out["route_saved"] = float(r["saved"] or 0.0)
            elif r["kind"] == "cache":
                out["cache_count"] = int(r["cnt"] or 0)
                out["cache_saved"] = float(r["saved"] or 0.0)
        return out

    def savings_series(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """按 (天, kind) 聚合节省金额（趋势图原料）。"""
        sql = (
            "SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') AS day, "
            "kind, COUNT(*) AS cnt, SUM(saved_cost) AS saved "
            "FROM saving_events WHERE created_at >= ? AND created_at < ?"
        )
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(int(user_id))
        sql += " GROUP BY day, kind ORDER BY day ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "day": r["day"], "kind": r["kind"],
                "count": int(r["cnt"] or 0), "saved": float(r["saved"] or 0.0),
            }
            for r in rows
        ]

    # ── 缓存命中率（语义缓存的命中 / 未命中分母） ─────────────────────────────────

    def record_cache_lookup(
        self, user_id: int, hit: bool, saved: float = 0.0, created_at: int | None = None
    ) -> None:
        """记录一次"可缓存请求"的查缓存结果（命中 / 未命中 + 命中时估算节省）。旁路调用。

        缓存的次数 / 节省 / 命中率统一只看这张表，避免与 saving_events 跨表对不上。
        """
        ts = int(created_at) if created_at is not None else self._now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cache_lookups(user_id, created_at, hit, saved) VALUES (?, ?, ?, ?)",
                (int(user_id), ts, 1 if hit else 0, float(saved or 0.0) if hit else 0.0),
            )

    def aggregate_cache_lookups(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> dict[str, Any]:
        """汇总可缓存请求总数（分母）、命中数（分子）、命中估算节省。"""
        sql = (
            "SELECT COUNT(*) AS lookups, SUM(hit) AS hits, SUM(saved) AS saved "
            "FROM cache_lookups WHERE created_at >= ? AND created_at < ?"
        )
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(int(user_id))
        with self._lock:
            r = self._conn.execute(sql, params).fetchone()
        return {
            "lookups": int(r["lookups"] or 0),
            "hits": int(r["hits"] or 0),
            "saved": float(r["saved"] or 0.0),
        }

    def cache_lookups_series(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """按天聚合缓存命中数 + 节省（趋势图原料；只统计命中行）。"""
        sql = (
            "SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') AS day, "
            "COUNT(*) AS cnt, SUM(saved) AS saved "
            "FROM cache_lookups WHERE hit = 1 AND created_at >= ? AND created_at < ?"
        )
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(int(user_id))
        sql += " GROUP BY day ORDER BY day ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {"day": r["day"], "kind": "cache",
             "count": int(r["cnt"] or 0), "saved": float(r["saved"] or 0.0)}
            for r in rows
        ]

    # ── 级联清理 ──────────────────────────────────────────────────────────────

    def delete_all_for_user(self, user_id: int) -> int:
        """删除某用户的全部用量 + 降本 + 缓存查询记录（注销 / admin 删号时级联调用）。返回 usage 删除条数。"""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM usage_events WHERE user_id = ?", (int(user_id),)
            )
            self._conn.execute(
                "DELETE FROM saving_events WHERE user_id = ?", (int(user_id),)
            )
            self._conn.execute(
                "DELETE FROM cache_lookups WHERE user_id = ?", (int(user_id),)
            )
        return cur.rowcount

    # ── 资源管理 ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "UsageStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── 进程内单例 helper ───────────────────────────────────────────────────────

_shared_store: UsageStore | None = None


def get_shared_store() -> UsageStore:
    """获取进程级共享 UsageStore；首次调用懒加载。"""
    global _shared_store
    if _shared_store is None:
        _shared_store = UsageStore()
    return _shared_store


def reset_shared_store_for_testing(store: UsageStore | None = None) -> None:
    """UT 专用：注入 mock store / 重置为 None。生产代码不要调用。"""
    global _shared_store
    _shared_store = store


# ── 单价合并 + 成本计算（默认 ← 覆盖） ──────────────────────────────────────────

def merged_pricing(store: UsageStore) -> dict[str, tuple[float, float]]:
    """合并内置默认单价与 admin 覆盖（覆盖优先），返回全部已知模型的单价。"""
    merged: dict[str, tuple[float, float]] = dict(config.MODEL_PRICING_DEFAULTS)
    merged.update(store.get_pricing_overrides())
    return merged


def cost_of(
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing: dict[str, tuple[float, float]],
) -> float:
    """按单价表算某条用量的估算成本；模型无单价返回 0。"""
    pin, pout = pricing.get(model_id, (0.0, 0.0))
    return (prompt_tokens / 1_000_000) * pin + (completion_tokens / 1_000_000) * pout


# ── 旁路记录入口（公共采集点调用） ──────────────────────────────────────────────

def record_usage(
    user_id: int,
    model_id: str,
    thinking: bool,
    usage: Any,
    session_id: str | None = None,
) -> None:
    """把一次 run 的 TokenUsage 落库；**异常只记日志、绝不抛**（旁路，不影响对话）。

    ``usage`` 期望是带 ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``
    的对象（``TokenUsage`` NamedTuple 或等价）。为 None / 空则跳过（该 run 没消耗 token）。
    """
    if usage is None:
        return
    try:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        total = getattr(usage, "total_tokens", None)
        if prompt == 0 and completion == 0 and not total:
            return
        # total 为 0 / None（部分 provider 不回总数）时回落到 prompt+completion，
        # 避免把"有输入输出但总数缺失"的 run 误存成 total=0。
        total_val = int(total) if total else None
        get_shared_store().record(
            user_id=user_id,
            model_id=model_id,
            thinking=bool(thinking),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total_val,
            session_id=session_id,
        )
    except Exception:
        logger.warning("[usage] record_usage 失败（已忽略，不影响对话）", exc_info=True)


def record_saving(
    user_id: int,
    kind: str,
    original_model: str,
    used_model: str,
    saved_cost: float,
    total_tokens: int = 0,
) -> None:
    """记录一次降本事件；**异常只记日志、绝不抛**（旁路，不影响对话）。"""
    try:
        get_shared_store().record_saving(
            user_id=user_id, kind=kind, original_model=original_model,
            used_model=used_model, saved_cost=saved_cost, total_tokens=total_tokens,
        )
    except Exception:
        logger.warning("[usage] record_saving 失败（已忽略）", exc_info=True)


def record_cache_lookup(user_id: int, hit: bool, saved: float = 0.0) -> None:
    """记录一次可缓存请求的查缓存结果（命中率分母 + 命中节省）；**异常只记日志、绝不抛**（旁路）。"""
    try:
        get_shared_store().record_cache_lookup(user_id=user_id, hit=hit, saved=saved)
    except Exception:
        logger.warning("[usage] record_cache_lookup 失败（已忽略）", exc_info=True)
