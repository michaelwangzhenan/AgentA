"""在线 trace 存储层。

每次对话（一次 ``Agent.run()``）记录一条 trace + 若干 span：
    - trace：总耗时 / 分阶段耗时（检索 / LLM / tool）/ token / 成本口径 / 是否出错
    - span：单个阶段（每轮 LLM、每次 tool、每次检索）的起止与耗时，供看板瀑布图用

复用 ``usage.db``（与 ``UsageStore`` 同库不同表，见 iter_14 设计 D2）；独立 connection +
``threading.Lock``，SQLite 文件级锁保证与 UsageStore 跨连接并发写安全。

采集为旁路：写入出错只记日志、绝不抛（``record_trace`` 调用方亦应吞异常）。
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

STAGE_LLM = "llm"
STAGE_TOOL = "tool"
STAGE_RETRIEVAL = "retrieval"

# 概览 p50/p95：超过此条数改用随机采样，避免 ORDER BY + OFFSET 扫全表
_TRACE_PERCENTILE_SAMPLE_CAP = 5000
_TRACE_PERCENTILE_SAMPLE_SIZE = 2000

# 检索工具名：tool span 命中它时归类到 retrieval 阶段
_RETRIEVAL_TOOL = "search_knowledge"


class TraceStore:
    """对话 trace + span 存储（写 usage.db）。内置锁，多线程安全。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or config.USAGE_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()
        logger.info("TraceStore 初始化完成: %s", self._db_path)

    def _create_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_traces (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id          TEXT NOT NULL UNIQUE,
                    user_id           INTEGER NOT NULL,
                    session_id        TEXT,
                    created_at        INTEGER NOT NULL,
                    model_id          TEXT NOT NULL DEFAULT '',
                    thinking          INTEGER NOT NULL DEFAULT 0,
                    total_ms          REAL NOT NULL DEFAULT 0,
                    llm_ms            REAL NOT NULL DEFAULT 0,
                    tool_ms           REAL NOT NULL DEFAULT 0,
                    retrieval_ms      REAL NOT NULL DEFAULT 0,
                    llm_calls         INTEGER NOT NULL DEFAULT 0,
                    tool_calls        INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    status            TEXT NOT NULL DEFAULT 'ok',
                    error_phase       TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_trace_user_time
                    ON agent_traces(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_trace_time
                    ON agent_traces(created_at);
                CREATE TABLE IF NOT EXISTS trace_spans (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id    TEXT NOT NULL,
                    seq         INTEGER NOT NULL DEFAULT 0,
                    stage       TEXT NOT NULL,
                    name        TEXT NOT NULL DEFAULT '',
                    start_ms    REAL NOT NULL DEFAULT 0,
                    duration_ms REAL NOT NULL DEFAULT 0,
                    status      TEXT NOT NULL DEFAULT 'ok'
                );
                CREATE INDEX IF NOT EXISTS idx_span_trace ON trace_spans(trace_id);
            """)

    @staticmethod
    def _now() -> int:
        return int(time.time())

    # ── 写入 ────────────────────────────────────────────────────────────────

    def record_trace(self, trace: dict[str, Any], spans: list[dict[str, Any]]) -> None:
        """写一条 trace + 它的 spans（同一事务）。trace 必含 trace_id / user_id。"""
        t = trace
        ts = int(t.get("created_at") or self._now())
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO agent_traces"
                "(trace_id, user_id, session_id, created_at, model_id, thinking, "
                " total_ms, llm_ms, tool_ms, retrieval_ms, llm_calls, tool_calls, "
                " prompt_tokens, completion_tokens, total_tokens, status, error_phase) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(t["trace_id"]), int(t["user_id"]), t.get("session_id"),
                    ts, str(t.get("model_id") or ""), 1 if t.get("thinking") else 0,
                    float(t.get("total_ms") or 0), float(t.get("llm_ms") or 0),
                    float(t.get("tool_ms") or 0), float(t.get("retrieval_ms") or 0),
                    int(t.get("llm_calls") or 0), int(t.get("tool_calls") or 0),
                    int(t.get("prompt_tokens") or 0), int(t.get("completion_tokens") or 0),
                    int(t.get("total_tokens") or 0), str(t.get("status") or "ok"),
                    str(t.get("error_phase") or ""),
                ),
            )
            for i, sp in enumerate(spans):
                self._conn.execute(
                    "INSERT INTO trace_spans"
                    "(trace_id, seq, stage, name, start_ms, duration_ms, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(t["trace_id"]), int(sp.get("seq", i)),
                        str(sp.get("stage") or ""), str(sp.get("name") or ""),
                        float(sp.get("start_ms") or 0), float(sp.get("duration_ms") or 0),
                        str(sp.get("status") or "ok"),
                    ),
                )

    # ── 读取 ────────────────────────────────────────────────────────────────

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """单条 trace + spans（看板瀑布用）。"""
        with self._lock:
            tr = self._conn.execute(
                "SELECT * FROM agent_traces WHERE trace_id = ?", (str(trace_id),)
            ).fetchone()
            if tr is None:
                return None
            spans = self._conn.execute(
                "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY seq ASC, id ASC",
                (str(trace_id),),
            ).fetchall()
        out = self._trace_row(tr)
        out["spans"] = [
            {
                "stage": s["stage"], "name": s["name"],
                "start_ms": float(s["start_ms"]), "duration_ms": float(s["duration_ms"]),
                "status": s["status"],
            }
            for s in spans
        ]
        return out

    def list_traces(
        self,
        start_ts: int,
        end_ts: int,
        user_id: int | None = None,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """明细分页（时间倒序）。返回 (rows, total)。"""
        where = "created_at >= ? AND created_at < ?"
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(int(user_id))
        if session_id:
            where += " AND session_id = ?"
            params.append(session_id)
        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM agent_traces WHERE {where}", params
                ).fetchone()[0]
            )
            rows = self._conn.execute(
                f"SELECT * FROM agent_traces WHERE {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        return [self._trace_row(r) for r in rows], total

    def overview(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> dict[str, Any]:
        """概览：对话数、错误率、延迟 p50/p95、平均分阶段耗时（SQL 聚合，不 fetchall）。"""
        where, params = _time_where(start_ts, end_ts, user_id)
        with self._lock:
            agg = self._conn.execute(
                f"SELECT COUNT(*) AS cnt, "
                f"SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errs, "
                f"AVG(total_ms) AS avg_total, "
                f"AVG(llm_ms) AS avg_llm, "
                f"AVG(tool_ms) AS avg_tool, "
                f"AVG(retrieval_ms) AS avg_retrieval "
                f"FROM agent_traces WHERE {where}",
                params,
            ).fetchone()
            count = int(agg["cnt"] or 0)
            if count == 0:
                return _empty_overview()
            p50 = _latency_percentile(self._conn, where, params, count, 0.50)
            p95 = _latency_percentile(self._conn, where, params, count, 0.95)
        errors = int(agg["errs"] or 0)
        return {
            "count": count,
            "error_count": errors,
            "error_rate": round(errors / count, 4),
            "latency_p50_ms": round(p50, 2),
            "latency_p95_ms": round(p95, 2),
            "latency_avg_ms": round(float(agg["avg_total"] or 0), 2),
            "avg_llm_ms": round(float(agg["avg_llm"] or 0), 2),
            "avg_tool_ms": round(float(agg["avg_tool"] or 0), 2),
            "avg_retrieval_ms": round(float(agg["avg_retrieval"] or 0), 2),
        }

    def series(
        self, start_ts: int, end_ts: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """按天聚合：对话数 + 平均总耗时（趋势图原料）。"""
        where = "created_at >= ? AND created_at < ?"
        params: list[Any] = [int(start_ts), int(end_ts)]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(int(user_id))
        with self._lock:
            rows = self._conn.execute(
                "SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') AS day, "
                "COUNT(*) AS cnt, AVG(total_ms) AS avg_ms, "
                "SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errs "
                f"FROM agent_traces WHERE {where} GROUP BY day ORDER BY day ASC",
                params,
            ).fetchall()
        return [
            {
                "day": r["day"],
                "count": int(r["cnt"] or 0),
                "avg_ms": round(float(r["avg_ms"] or 0), 2),
                "error_count": int(r["errs"] or 0),
            }
            for r in rows
        ]

    def delete_all_for_user(self, user_id: int) -> int:
        """删除某用户全部 trace + span（注销 / admin 删号级联）。返回删除的 trace 条数。"""
        with self._lock, self._conn:
            ids = [
                r["trace_id"]
                for r in self._conn.execute(
                    "SELECT trace_id FROM agent_traces WHERE user_id = ?", (int(user_id),)
                ).fetchall()
            ]
            if ids:
                placeholder = ",".join("?" for _ in ids)
                self._conn.execute(
                    f"DELETE FROM trace_spans WHERE trace_id IN ({placeholder})", ids
                )
            cur = self._conn.execute(
                "DELETE FROM agent_traces WHERE user_id = ?", (int(user_id),)
            )
        return cur.rowcount

    @staticmethod
    def _trace_row(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "trace_id": r["trace_id"],
            "user_id": int(r["user_id"]),
            "session_id": r["session_id"],
            "created_at": int(r["created_at"]),
            "model_id": r["model_id"],
            "thinking": bool(r["thinking"]),
            "total_ms": float(r["total_ms"]),
            "llm_ms": float(r["llm_ms"]),
            "tool_ms": float(r["tool_ms"]),
            "retrieval_ms": float(r["retrieval_ms"]),
            "llm_calls": int(r["llm_calls"]),
            "tool_calls": int(r["tool_calls"]),
            "prompt_tokens": int(r["prompt_tokens"]),
            "completion_tokens": int(r["completion_tokens"]),
            "total_tokens": int(r["total_tokens"]),
            "status": r["status"],
            "error_phase": r["error_phase"],
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TraceStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _time_where(
    start_ts: int, end_ts: int, user_id: int | None
) -> tuple[str, list[Any]]:
    where = "created_at >= ? AND created_at < ?"
    params: list[Any] = [int(start_ts), int(end_ts)]
    if user_id is not None:
        where += " AND user_id = ?"
        params.append(int(user_id))
    return where, params


def _empty_overview() -> dict[str, Any]:
    return {
        "count": 0, "error_count": 0, "error_rate": 0.0,
        "latency_p50_ms": 0.0, "latency_p95_ms": 0.0, "latency_avg_ms": 0.0,
        "avg_llm_ms": 0.0, "avg_tool_ms": 0.0, "avg_retrieval_ms": 0.0,
    }


def _latency_percentile(
    conn: sqlite3.Connection,
    where: str,
    params: list[Any],
    count: int,
    q: float,
) -> float:
    """按 total_ms 算分位数；大行数时随机采样近似。"""
    if count <= 0:
        return 0.0
    if count == 1:
        row = conn.execute(
            f"SELECT total_ms FROM agent_traces WHERE {where} LIMIT 1",
            params,
        ).fetchone()
        return float(row["total_ms"] if row else 0.0)
    if count > _TRACE_PERCENTILE_SAMPLE_CAP:
        rows = conn.execute(
            f"SELECT total_ms FROM agent_traces WHERE {where} "
            "ORDER BY RANDOM() LIMIT ?",
            [*params, min(_TRACE_PERCENTILE_SAMPLE_SIZE, count)],
        ).fetchall()
        return _percentile(sorted(float(r["total_ms"]) for r in rows), q)
    pos = q * (count - 1)
    lo = int(pos)
    hi = min(lo + 1, count - 1)
    rows = conn.execute(
        f"SELECT total_ms FROM agent_traces WHERE {where} "
        "ORDER BY total_ms ASC LIMIT ? OFFSET ?",
        [*params, hi - lo + 1, lo],
    ).fetchall()
    vals = [float(r["total_ms"]) for r in rows]
    if len(vals) == 1:
        return vals[0]
    frac = pos - lo
    return vals[0] * (1 - frac) + vals[1] * frac


def _percentile(sorted_vals: list[float], q: float) -> float:
    """已排序列表的分位数（线性插值）；空列表返回 0。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


# ── 进程内单例 helper ───────────────────────────────────────────────────────

_shared_store: TraceStore | None = None
_shared_lock = threading.Lock()


def get_shared_store() -> TraceStore:
    """获取进程级共享 TraceStore；首次调用懒加载（双检锁）。"""
    global _shared_store
    if _shared_store is None:
        with _shared_lock:
            if _shared_store is None:
                _shared_store = TraceStore()
    return _shared_store


def reset_shared_store_for_testing(store: TraceStore | None = None) -> None:
    """UT 专用：注入 mock store / 重置为 None。生产代码不要调用。"""
    global _shared_store
    _shared_store = store


# ── trace 采集器（旁路，从 EventBus 事件重建 trace） ──────────────────────────

class TraceCollector:
    """订阅一次 run 的事件，重建分阶段 trace。

    用法（参考 chat.py 的 usage 采集）：构造后把 ``on_event`` 作为 event_callback 传给
    ``agent.run``；run 结束后调 ``build(...)`` 得到 (trace, spans) 落库。

    时间口径：以 ``agent.run.start`` 事件时间为 0 点；各 span 的 start_ms 为相对偏移。
    LLM span 来自 agent 发的 info ``llm_call`` 事件（带 duration_ms）；tool span 来自
    tool_call_start/end 配对（命中 search_knowledge 归类 retrieval）。全程吞异常软失败。
    """

    def __init__(self) -> None:
        self._start_ts: float | None = None
        self._end_ts: float | None = None
        self._spans: list[dict[str, Any]] = []
        self._pending_tools: dict[str, tuple[str, float]] = {}
        self._usage: Any = None
        self._status = "ok"
        self._error_phase = ""

    def on_event(self, event: Any) -> None:
        try:
            self._handle(event)
        except Exception:  # noqa: BLE001 — 采集旁路，绝不影响对话
            logger.debug("[trace] 事件处理异常（已忽略）", exc_info=True)

    def _handle(self, event: Any) -> None:
        etype = getattr(event, "type", None)
        ts = float(getattr(event, "ts", 0.0) or 0.0)
        payload = getattr(event, "payload", None) or {}
        if etype == "info":
            if payload.get("message") == "agent.run.start" and self._start_ts is None:
                self._start_ts = ts
        elif etype == "tool_call_start":
            cid = payload.get("call_id") or ""
            self._pending_tools[cid] = (payload.get("name") or "tool", ts)
        elif etype == "tool_call_end":
            cid = payload.get("call_id") or ""
            name, start = self._pending_tools.pop(cid, ("tool", ts))
            stage = STAGE_RETRIEVAL if name == _RETRIEVAL_TOOL else STAGE_TOOL
            self._spans.append({
                "stage": stage,
                "name": name,
                "_start_ts": start,
                "duration_ms": max(0.0, (ts - start) * 1000.0),
                "status": payload.get("status") or "ok",
            })
        elif etype == "error":
            self._status = "error"
            self._error_phase = payload.get("phase") or self._error_phase
        elif etype == "final_answer":
            self._end_ts = ts
            self._usage = payload.get("usage")
            # 各轮 LLM 耗时随 final_answer 的 trace 字段透传（不污染公共事件流）
            tr = payload.get("trace") or {}
            for r in tr.get("llm_rounds", []):
                rnd = r.get("round")
                self._spans.append({
                    "stage": STAGE_LLM,
                    "name": f"LLM 第 {rnd} 轮" if rnd else "LLM",
                    "_end_ts": float(r.get("end_ts") or ts),
                    "duration_ms": float(r.get("duration_ms") or 0.0),
                })

    def build(
        self,
        trace_id: str,
        user_id: int,
        session_id: str | None,
        model_id: str,
        thinking: bool,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """汇总成 (trace, spans)。"""
        base = self._start_ts if self._start_ts is not None else (
            self._end_ts or time.time()
        )
        total_ms = 0.0
        if self._start_ts is not None and self._end_ts is not None:
            total_ms = max(0.0, (self._end_ts - self._start_ts) * 1000.0)

        # 先算每个 span 的相对起点，再按起点排序，保证瀑布按时间先后展示
        prelim: list[dict[str, Any]] = []
        for sp in self._spans:
            if "_end_ts" in sp:
                start_ms = max(0.0, (sp["_end_ts"] - base) * 1000.0 - sp["duration_ms"])
            else:
                start_ms = max(0.0, (sp.get("_start_ts", base) - base) * 1000.0)
            prelim.append({**sp, "_start_ms": start_ms})
        prelim.sort(key=lambda s: s["_start_ms"])

        spans: list[dict[str, Any]] = []
        llm_ms = tool_ms = retrieval_ms = 0.0
        llm_calls = tool_calls = 0
        for i, sp in enumerate(prelim):
            spans.append({
                "seq": i,
                "stage": sp["stage"],
                "name": sp["name"],
                "start_ms": round(sp["_start_ms"], 2),
                "duration_ms": round(sp["duration_ms"], 2),
                "status": sp.get("status", "ok"),
            })
            if sp["stage"] == STAGE_LLM:
                llm_ms += sp["duration_ms"]
                llm_calls += 1
            elif sp["stage"] == STAGE_RETRIEVAL:
                retrieval_ms += sp["duration_ms"]
                tool_calls += 1
            else:
                tool_ms += sp["duration_ms"]
                tool_calls += 1

        usage = self._usage
        trace = {
            "trace_id": trace_id,
            "user_id": user_id,
            "session_id": session_id,
            "model_id": model_id,
            "thinking": thinking,
            "total_ms": round(total_ms, 2),
            "llm_ms": round(llm_ms, 2),
            "tool_ms": round(tool_ms, 2),
            "retrieval_ms": round(retrieval_ms, 2),
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0) if usage else 0,
            "status": self._status,
            "error_phase": self._error_phase,
        }
        return trace, spans


def record_trace_safe(
    collector: "TraceCollector",
    trace_id: str,
    user_id: int,
    session_id: str | None,
    model_id: str,
    thinking: bool,
) -> None:
    """把采集器的 trace 落库；**异常只记日志、绝不抛**（旁路，不影响对话）。"""
    if not config.TRACE_ENABLED:
        return
    try:
        trace, spans = collector.build(trace_id, user_id, session_id, model_id, thinking)
        # 没有任何阶段且总耗时为 0 的空 trace 不落库（如 run 早期异常）
        if not spans and trace["total_ms"] <= 0:
            return
        get_shared_store().record_trace(trace, spans)
    except Exception:
        logger.warning("[trace] record_trace_safe 失败（已忽略，不影响对话）", exc_info=True)
