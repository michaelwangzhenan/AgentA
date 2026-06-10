"""评估 + 可观测端点。

- RAG golden 管理（**仅 admin**）：`/eval/golden` 增删改查 + 审核改状态 + 从 JSON 导入
- 在线 trace 可观测：`/eval/trace/*`（本人视角；admin 可 `scope=all` 看全员）
- 评估报告浏览（**仅 admin**）：`/eval/reports` 列表 + 单份内容（只读 reports 目录）

trace 数据来自 chat 链路旁路采集（见 src/memory/trace_store.py）；golden 由入库自动
生成（pending）或人工录入（approved），评估脚本默认只用 approved。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import get_current_user, get_golden_store, get_trace_store, require_admin
from src.api.schemas.eval import (
    GoldenCreateRequest,
    GoldenItem,
    GoldenList,
    GoldenUpdateRequest,
    ReportContent,
    ReportItem,
    ReportList,
    TraceDetail,
    TraceList,
    TraceListItem,
    TraceOverview,
    TraceSeries,
    TraceSeriesRow,
    TraceSpan,
)
from src.memory.golden_store import GoldenStore
from src.memory.trace_store import TraceStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/eval", tags=["eval"])

_VALID_RANGES = ("1d", "7d", "30d", "mtd", "last_month")


def _resolve_range(range_key: str) -> tuple[int, int]:
    """范围关键字 → [start, end) epoch 秒（本地时区边界），与用量页口径一致。"""
    rk = (range_key or "30d").lower()
    if rk not in _VALID_RANGES:
        rk = "30d"
    now = datetime.now()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_tomorrow = start_of_today + timedelta(days=1)
    if rk == "1d":
        start, end = start_of_today, start_of_tomorrow
    elif rk == "7d":
        start, end = start_of_today - timedelta(days=6), start_of_tomorrow
    elif rk == "30d":
        start, end = start_of_today - timedelta(days=29), start_of_tomorrow
    elif rk == "mtd":
        start, end = start_of_today.replace(day=1), start_of_tomorrow
    else:  # last_month
        first_this = start_of_today.replace(day=1)
        last_month_start = (first_this - timedelta(days=1)).replace(day=1)
        start, end = last_month_start, first_this
    return int(start.timestamp()), int(end.timestamp())


# ── RAG golden 管理（admin） ─────────────────────────────────────────────────

def _to_golden_item(d: dict) -> GoldenItem:
    return GoldenItem(**d)


@router.get("/golden", response_model=GoldenList)
def list_golden(
    status: str | None = Query(None),
    source: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> GoldenList:
    """列出 RAG golden（可按状态 / 来源过滤）+ 各状态计数。"""
    rows, total = store.list(status=status, source=source, limit=limit, offset=offset)
    return GoldenList(
        items=[_to_golden_item(r) for r in rows],
        total=total, limit=limit, offset=offset, counts=store.counts(),
    )


@router.post("/golden", response_model=GoldenItem)
def create_golden(
    req: GoldenCreateRequest,
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> GoldenItem:
    """人工新增一条 golden（来源 manual、状态 approved）。"""
    try:
        gid = store.create(
            query=req.query,
            expected_keywords=req.expected_keywords,
            expected_source_contains=req.expected_source_contains,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = store.get(gid)
    return _to_golden_item(item)  # type: ignore[arg-type]


@router.put("/golden/{golden_id}", response_model=GoldenItem)
def update_golden(
    golden_id: int,
    req: GoldenUpdateRequest,
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> GoldenItem:
    """局部更新一条 golden（含审核改状态）。"""
    fields = req.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="没有可更新的字段")
    try:
        ok = store.update(golden_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="golden 不存在")
    return _to_golden_item(store.get(golden_id))  # type: ignore[arg-type]


@router.delete("/golden/{golden_id}")
def delete_golden(
    golden_id: int,
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> dict:
    """删一条 golden。幂等：不存在返回 deleted=False。"""
    return {"deleted": store.delete(golden_id)}


@router.post("/golden/import")
def import_golden(
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> dict:
    """从 tools/rag_eval/golden.json 一键导入（幂等，按 query 去重）。"""
    import json

    path = Path(__file__).resolve().parents[3] / "tools" / "rag_eval" / "golden.json"
    if not path.exists():
        # 回退到 example 模板，便于空仓库演示
        path = path.with_name("golden.example.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail="未找到 tools/rag_eval/golden.json")
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"golden.json 解析失败：{exc}") from exc
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="golden.json 必须是 list")
    added = store.import_items(items)
    return {"added": added, "source": str(path.name)}


# ── trace 可观测 ─────────────────────────────────────────────────────────────

def _scope_user_id(scope: str, user: dict) -> int | None:
    """scope=all 且为 admin → None（全员）；否则限定当前用户。"""
    if scope == "all" and user.get("role") == "admin":
        return None
    return user["id"]


@router.get("/trace/overview", response_model=TraceOverview)
def trace_overview(
    range: str = Query("30d"),
    scope: str = Query("mine"),
    user: dict = Depends(get_current_user),
    store: TraceStore = Depends(get_trace_store),
) -> TraceOverview:
    start, end = _resolve_range(range)
    data = store.overview(start, end, user_id=_scope_user_id(scope, user))
    return TraceOverview(range=range, **data)


@router.get("/trace/series", response_model=TraceSeries)
def trace_series(
    range: str = Query("30d"),
    scope: str = Query("mine"),
    user: dict = Depends(get_current_user),
    store: TraceStore = Depends(get_trace_store),
) -> TraceSeries:
    start, end = _resolve_range(range)
    rows = store.series(start, end, user_id=_scope_user_id(scope, user))
    return TraceSeries(range=range, rows=[TraceSeriesRow(**r) for r in rows])


@router.get("/trace/list", response_model=TraceList)
def trace_list(
    range: str = Query("30d"),
    scope: str = Query("mine"),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    store: TraceStore = Depends(get_trace_store),
) -> TraceList:
    start, end = _resolve_range(range)
    rows, total = store.list_traces(
        start, end, user_id=_scope_user_id(scope, user), limit=limit, offset=offset
    )
    return TraceList(
        items=[TraceListItem(**{k: r[k] for k in TraceListItem.model_fields}) for r in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/trace/{trace_id}", response_model=TraceDetail)
def trace_detail(
    trace_id: str,
    user: dict = Depends(get_current_user),
    store: TraceStore = Depends(get_trace_store),
) -> TraceDetail:
    tr = store.get_trace(trace_id)
    if tr is None:
        raise HTTPException(status_code=404, detail="trace 不存在")
    # 归属校验：非本人 trace 仅 admin 可看（404 不泄露存在性）
    if tr["user_id"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=404, detail="trace 不存在")
    spans = [TraceSpan(**s) for s in tr.pop("spans", [])]
    fields = {k: tr[k] for k in TraceDetail.model_fields if k in tr and k != "spans"}
    return TraceDetail(spans=spans, **fields)


# ── 评估报告浏览（admin，只读 reports 目录） ──────────────────────────────────

def _report_roots() -> dict[str, Path]:
    """报告目录：标识前缀 → 绝对路径。"""
    repo = Path(__file__).resolve().parents[3]
    return {
        "agent_eval": repo / "tools" / "agent_eval" / "reports",
        "rag_eval": repo / "tools" / "rag_eval" / "reports",
    }


@router.get("/reports", response_model=ReportList)
def list_reports(_: dict = Depends(require_admin)) -> ReportList:
    """列出全部评估 Markdown 报告（按修改时间倒序）。"""
    items: list[ReportItem] = []
    for prefix, root in _report_roots().items():
        if not root.is_dir():
            continue
        for fp in root.glob("*.md"):
            try:
                st = fp.stat()
            except OSError:
                continue
            items.append(ReportItem(
                name=f"{prefix}/{fp.name}",
                size=st.st_size,
                modified_at=int(st.st_mtime),
            ))
    items.sort(key=lambda x: x.modified_at, reverse=True)
    return ReportList(reports=items)


@router.get("/reports/content", response_model=ReportContent)
def report_content(
    name: str = Query(..., description="形如 agent_eval/perf-xxx.md"),
    _: dict = Depends(require_admin),
) -> ReportContent:
    """读取单份报告内容（路径受限于 reports 目录，防目录穿越）。"""
    roots = _report_roots()
    prefix, _, fname = name.partition("/")
    root = roots.get(prefix)
    if root is None or not fname or "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(status_code=400, detail="非法报告名")
    fp = (root / fname).resolve()
    # 二次确认解析后的路径仍落在 root 内（纵深防御）
    if root.resolve() not in fp.parents or fp.suffix != ".md" or not fp.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return ReportContent(name=name, content=fp.read_text(encoding="utf-8"))
