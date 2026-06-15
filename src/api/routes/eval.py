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

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.api.deps import (
    get_current_user,
    get_golden_store,
    get_security_event_store,
    get_trace_store,
    require_admin,
)
from src.api.schemas.eval import (
    EvalMetric,
    EvalRunRequest,
    EvalRunStatus,
    EvalSummary,
    GoldenCreateRequest,
    GoldenGenerateRequest,
    GoldenGenerateResponse,
    GoldenItem,
    GoldenList,
    GoldenUpdateRequest,
    ReportContent,
    ReportItem,
    ReportList,
    SecurityEventRow,
    SecurityKindRow,
    SecurityRuntimeSummary,
    SecuritySummary,
    SecurityTrend,
    SecurityTrendPoint,
    TraceDetail,
    TraceList,
    TraceListItem,
    TraceOverview,
    TraceSeries,
    TraceSeriesRow,
    TraceSpan,
)
import src.eval_runner as eval_runner
from src.memory.golden_store import GoldenStore
from src.memory.security_event_store import SecurityEventStore
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
    doc_id: str | None = Query(None, description="按关联 KB 文档筛选（来源文档）"),
    source_contains: str | None = Query(None, description="按来源文件名/路径子串过滤"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> GoldenList:
    """列出 RAG golden（可按状态 / 来源 / 文档 / 来源文件过滤）+ 各状态计数。"""
    rows, total = store.list(
        status=status, source=source, doc_id=doc_id,
        source_contains=source_contains, limit=limit, offset=offset,
    )
    return GoldenList(
        items=[_to_golden_item(r) for r in rows],
        total=total, limit=limit, offset=offset, counts=store.counts(),
    )


@router.get("/golden/export")
def export_golden(
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> Response:
    """导出全部 golden 为可下载的 json 文件。"""
    import json as _json

    payload = _json.dumps(store.export_all(), ensure_ascii=False, indent=2)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="golden-export-{ts}.json"'},
    )


@router.post("/golden/generate", response_model=GoldenGenerateResponse)
def generate_golden(
    req: GoldenGenerateRequest,
    _: dict = Depends(require_admin),
    store: GoldenStore = Depends(get_golden_store),
) -> GoldenGenerateResponse:
    """为某已入库文档手动生成 golden 候选：定位 web_uploads 物理文件 → LLM 出题 → pending。

    重生成前先清掉该文档旧的 pending 候选（approved/rejected 保留）。
    """
    import src.config as config
    from src.rag.golden_gen import run_generation_for_file

    # 定位物理文件：web_uploads/<model>/<source>，并防路径穿越（须落在该库上传根内）
    upload_root = (Path(config.WEB_UPLOAD_DIR).resolve() / req.model)
    target = (upload_root / req.source).resolve()
    if upload_root not in target.parents and target != upload_root:
        raise HTTPException(status_code=400, detail="非法文档路径")
    if not target.is_file():
        raise HTTPException(
            status_code=404,
            detail="文档物理文件不存在（仅 Web 上传的文档支持手动生成）",
        )
    removed = store.delete_pending_by_doc(req.doc_id) if req.doc_id else 0
    n = run_generation_for_file(
        file_path=target, source=req.source, doc_id=req.doc_id, force=True,
    )
    return GoldenGenerateResponse(generated=n, removed_pending=removed)


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
            expected_source=req.expected_source,
            expected_source_contains=req.expected_source_contains,
            golden_type=req.type,
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


# ── 评估报告浏览（admin，只读 tools/reports/<eval>/ 单根） ────────────────────

def _reports_root() -> Path:
    """报告统一根目录：tools/reports/（下按 eval 建子目录）。"""
    return Path(__file__).resolve().parents[3] / "tools" / "reports"


@router.get("/reports", response_model=ReportList)
def list_reports(_: dict = Depends(require_admin)) -> ReportList:
    """递归列出 tools/reports/ 下全部 Markdown 报告（按修改时间倒序）。name = 相对路径。"""
    root = _reports_root()
    items: list[ReportItem] = []
    if root.is_dir():
        for fp in root.rglob("*.md"):
            try:
                st = fp.stat()
            except OSError:
                continue
            items.append(ReportItem(
                name=fp.relative_to(root).as_posix(),
                size=st.st_size,
                modified_at=int(st.st_mtime),
            ))
    items.sort(key=lambda x: x.modified_at, reverse=True)
    return ReportList(reports=items)


@router.get("/reports/content", response_model=ReportContent)
def report_content(
    name: str = Query(..., description="相对 tools/reports 的路径，如 security/xxx.md"),
    _: dict = Depends(require_admin),
) -> ReportContent:
    """读取单份报告内容（路径受限于 tools/reports 目录，防目录穿越）。"""
    if not name or ".." in name or name.startswith(("/", "\\")) or "\\" in name:
        raise HTTPException(status_code=400, detail="非法报告名")
    root = _reports_root().resolve()
    fp = (root / name).resolve()
    # 纵深防御：解析后的路径必须仍落在 root 内，且是 .md 文件
    if root not in fp.parents or fp.suffix != ".md" or not fp.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return ReportContent(name=name, content=fp.read_text(encoding="utf-8"))


# ── 安全红队看板（admin，读 security-adversarial-*.json sidecar） ─────────────

def _security_sidecars() -> list[Path]:
    """按修改时间升序列出全部安全评估 sidecar JSON（tools/reports/security/）。"""
    root = _reports_root() / "security"
    if not root.is_dir():
        return []
    files = [fp for fp in root.glob("security-adversarial-*.json") if fp.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def _load_sidecar(fp: Path) -> dict | None:
    """读一份 sidecar JSON；解析失败返回 None（软失败，不让单份坏文件拖垮整个看板）。"""
    import json

    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError) as exc:
        logger.warning("[eval] 安全 sidecar 解析失败 %s：%s", fp.name, exc)
        return None


@router.get("/security/summary", response_model=SecuritySummary)
def security_summary(_: dict = Depends(require_admin)) -> SecuritySummary:
    """最近一次红队评估的汇总（总拦截率 / 误拦率 + 逐类分项）。无报告时 available=False。"""
    files = _security_sidecars()
    for fp in reversed(files):  # 从最新往回找第一份可解析的
        data = _load_sidecar(fp)
        if data is None:
            continue
        return SecuritySummary(
            available=True,
            timestamp=data.get("timestamp", ""),
            git=data.get("git", ""),
            partial=bool(data.get("partial", False)),
            kinds_run=list(data.get("kinds_run", [])),
            total=data.get("total", 0),
            attacks=data.get("attacks", 0),
            attack_blocked=data.get("attack_blocked", 0),
            benigns=data.get("benigns", 0),
            benign_blocked=data.get("benign_blocked", 0),
            recall=data.get("recall", 0.0),
            fpr=data.get("fpr", 0.0),
            recall_threshold=data.get("recall_threshold", 0.0),
            fpr_threshold=data.get("fpr_threshold", 0.0),
            passed=bool(data.get("passed", False)),
            by_kind=[SecurityKindRow(**kr) for kr in data.get("by_kind", [])],
        )
    return SecuritySummary(available=False)


@router.get("/security/trend", response_model=SecurityTrend)
def security_trend(
    limit: int = Query(30, ge=1, le=200),
    _: dict = Depends(require_admin),
) -> SecurityTrend:
    """历次红队评估的拦截率 / 误拦率趋势（按时间升序，取最近 limit 次）。"""
    points: list[SecurityTrendPoint] = []
    for fp in _security_sidecars():
        data = _load_sidecar(fp)
        if data is None:
            continue
        points.append(SecurityTrendPoint(
            timestamp=data.get("timestamp", ""),
            recall=data.get("recall", 0.0),
            fpr=data.get("fpr", 0.0),
            total=data.get("total", 0),
            partial=bool(data.get("partial", False)),
        ))
    return SecurityTrend(points=points[-limit:])


@router.get("/security/runtime/summary", response_model=SecurityRuntimeSummary)
def security_runtime_summary(
    range: str = Query("30d"),
    limit: int = Query(50, ge=1, le=200),
    _: dict = Depends(require_admin),
    store: SecurityEventStore = Depends(get_security_event_store),
) -> SecurityRuntimeSummary:
    """线上真实拦截统计：区间总数 + 分类型计数 + 最近若干条（全员视角，admin）。"""
    start, end = _resolve_range(range)
    s = store.summary(start, end)
    recent = store.recent(start, end, limit=limit)
    return SecurityRuntimeSummary(
        range=range,
        total=s["total"],
        by_type=s["by_type"],
        recent=[SecurityEventRow(**r) for r in recent],
    )


# ── 离线评估：触发 / 状态 / 取消（admin，单任务全局锁） ───────────────────────

_SECURITY_KINDS = {"direct", "indirect_rag", "indirect_web", "tool_blocklist"}


def _threshold(req: EvalRunRequest, key: str, hi: float = 1.0) -> float | None:
    """取并校验一个阈值（0~hi，默认 0~1）；缺省返回 None。"""
    th = req.thresholds or {}
    if key not in th:
        return None
    v = th[key]
    if not isinstance(v, (int, float)) or not (0.0 <= v <= hi):
        raise HTTPException(status_code=400, detail=f"阈值 {key} 需在 0~{hi:g} 之间")
    return float(v)


import time as _time
from pathlib import Path as _Path


def _build_eval_args(req: EvalRunRequest) -> list[str]:
    """按任务把请求里的 UI 选项拼成命令行参数（白名单，杜绝注入）。"""
    args: list[str] = []
    opts = req.options or {}
    if req.task == "security":
        if req.no_llm:
            args.append("--no-llm")
        kind = opts.get("kind")
        if kind:
            if kind not in _SECURITY_KINDS:
                raise HTTPException(status_code=400, detail=f"非法 kind：{kind}")
            args += ["--kind", str(kind)]
        recall = _threshold(req, "recall")
        if recall is not None:
            args += ["--recall-threshold", str(recall)]
        fpr = _threshold(req, "fpr")
        if fpr is not None:
            args += ["--fpr-threshold", str(fpr)]
    elif req.task == "rag":
        # 复选框正向语义：勾选=开（默认开）；取消勾选才传 --no-*
        if opts.get("rewriter", True) is False:
            args.append("--no-rewriter")
        if opts.get("rerank", True) is False:
            args.append("--no-rerank")
        # 选了模型（no_llm=False）才额外评答案质量；条数来自 llm_count
        if not req.no_llm:
            n = opts.get("llm_count", 10)
            if not isinstance(n, (int, float)) or int(n) < 0:
                raise HTTPException(status_code=400, detail="评测样本数需为非负整数")
            args += ["--llm", str(int(n))]
            judge = opts.get("judge_model")
            if judge and isinstance(judge, str):
                args += ["--judge-model", judge]
        # runner 不自带默认目录：由后端指定 -o 到 tools/reports/rag/
        ts = _time.strftime("%Y%m%d-%H%M%S")
        out = _Path("tools") / "reports" / "rag" / f"rag-{ts}.md"
        args += ["-o", out.as_posix()]
    elif req.task in ("memory", "skills", "harness", "srs"):
        pt = _threshold(req, "pass")
        if pt is not None:
            args += ["--pass-threshold", str(pt)]
    elif req.task == "mcp":
        # 选 None（no_llm）= 只跑 structural，不烧 LLM；否则含 llm-e2e。无阈值（验收"全过"判定）
        if req.no_llm:
            args.append("--no-llm")
    elif req.task == "perf":
        # 不调 LLM；UI 默认 session + memory 一起跑、合并一份报告
        args += ["--target", "all"]
        sizes = opts.get("sizes", "")
        if isinstance(sizes, str) and sizes.strip():
            # 白名单：只允许数字 + 逗号（杜绝注入），转成规范串再传
            parts = [p.strip() for p in sizes.split(",") if p.strip()]
            if not all(p.isdigit() and int(p) > 0 for p in parts):
                raise HTTPException(status_code=400, detail="数据档位需为正整数，逗号分隔")
            args += ["--sizes", ",".join(parts)]
    elif req.task == "plan":
        # 始终调 LLM 评识别；取消勾选「评 plan 结构」才关 LLM-judge 结构评分
        judge_on = opts.get("judge", True) is not False
        if not judge_on:
            args.append("--no-judge")
        recall = _threshold(req, "recall")
        if recall is not None:
            args += ["--recall-threshold", str(recall)]
        struct = _threshold(req, "struct", hi=5.0)
        if struct is not None:
            args += ["--struct-threshold", str(struct)]
        # 评委模型（仅评结构时有意义）：同 RAG 走 CLI 参数（EVAL_JUDGE_MODEL 在 .env 里，env 注入会被覆盖）
        if judge_on:
            judge = opts.get("judge_model")
            if judge and isinstance(judge, str):
                args += ["--judge-model", judge]
    elif req.task in ("learning_plan", "quiz"):
        # 同 plan：识别 + 质量 judge 两层；阈值 recall + quality（质量 0-5），可关 judge、可配评委模型
        judge_on = opts.get("judge", True) is not False
        if not judge_on:
            args.append("--no-judge")
        recall = _threshold(req, "recall")
        if recall is not None:
            args += ["--recall-threshold", str(recall)]
        quality = _threshold(req, "quality", hi=5.0)
        if quality is not None:
            args += ["--quality-threshold", str(quality)]
        if judge_on:
            judge = opts.get("judge_model")
            if judge and isinstance(judge, str):
                args += ["--judge-model", judge]
    return args


@router.post("/run", response_model=EvalRunStatus)
def run_eval(req: EvalRunRequest, _: dict = Depends(require_admin)) -> EvalRunStatus:
    """触发一个离线评估子进程；已有任务在跑返回 409。"""
    args = _build_eval_args(req)
    try:
        st = eval_runner.start(req.task, args, model=req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return EvalRunStatus(**st)


@router.get("/run/status", response_model=EvalRunStatus)
def run_status(_: dict = Depends(require_admin)) -> EvalRunStatus:
    """当前评估任务状态 + 日志末尾（前端轮询用）。"""
    return EvalRunStatus(**eval_runner.status())


@router.post("/run/cancel", response_model=EvalRunStatus)
def run_cancel(_: dict = Depends(require_admin)) -> EvalRunStatus:
    """取消当前评估任务（杀进程树）。"""
    return EvalRunStatus(**eval_runner.cancel())


# ── 离线评估：通用摘要卡片（按 task 读最近一次结构化结果） ────────────────────

def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _sidecar_for_report(report: str) -> dict | None:
    """给一个 .md 报告名，读它配对的 .json sidecar（同名换后缀）；非法 / 缺失返回 None。"""
    if not report or ".." in report or report.startswith(("/", "\\")) or "\\" in report:
        return None
    root = _reports_root().resolve()
    fp = (root / report).resolve()
    if root not in fp.parents:
        return None
    jp = fp.with_suffix(".json")
    if not jp.is_file():
        return None
    return _load_sidecar(jp)


def _sec_summary_from_data(data: dict) -> EvalSummary:
    """安全红队 sidecar dict → 通用卡片 schema。"""
    recall = data.get("recall", 0.0)
    fpr = data.get("fpr", 0.0)
    rt = data.get("recall_threshold", 0.0)
    ft = data.get("fpr_threshold", 0.0)
    return EvalSummary(
        available=True,
        task="security",
        timestamp=data.get("timestamp", ""),
        git=data.get("git", ""),
        passed=bool(data.get("passed", False)),
        partial=bool(data.get("partial", False)),
        metrics=[
            EvalMetric(
                label="拦截率",
                value=f"{_pct(recall)} ({data.get('attack_blocked', 0)}/{data.get('attacks', 0)})",
                threshold=f"≥ {_pct(rt)}",
                ok=recall >= rt,
            ),
            EvalMetric(
                label="误拦率",
                value=f"{_pct(fpr)} ({data.get('benign_blocked', 0)}/{data.get('benigns', 0)})",
                threshold=f"≤ {_pct(ft)}",
                ok=fpr <= ft,
            ),
        ],
    )


def _security_summary(report: str | None = None) -> EvalSummary:
    """安全红队卡片：给 report 则读该报告配对 sidecar，否则取最新一次。"""
    if report:
        data = _sidecar_for_report(report)
        return _sec_summary_from_data(data) if data else EvalSummary(available=False, task="security")
    for fp in reversed(_security_sidecars()):
        data = _load_sidecar(fp)
        if data is not None:
            return _sec_summary_from_data(data)
    return EvalSummary(available=False, task="security")


def _rag_summary_from_data(data: dict) -> EvalSummary:
    """RAG sidecar dict → 通用卡片（纯展示数字，无 pass/fail）。"""

    def _pct_opt(v: object) -> str:
        return _pct(v) if isinstance(v, (int, float)) else "—"

    metrics = [
        EvalMetric(label="命中率@1", value=_pct_opt(data.get("hit_either_at_1")), ok=None),
        EvalMetric(label="命中率@3", value=_pct_opt(data.get("hit_either_at_3")), ok=None),
        EvalMetric(label="命中率@k", value=_pct_opt(data.get("hit_either_at_k")), ok=None),
        EvalMetric(label="MRR", value=f"{data.get('mrr', 0.0):.4f}", ok=None),
    ]
    aq = data.get("answer_quality")
    if isinstance(aq, dict):
        af, ar = aq.get("avg_faithfulness"), aq.get("avg_relevance")
        metrics.append(EvalMetric(
            label="faithfulness",
            value=f"{af:.2f}" if isinstance(af, (int, float)) else "—",
            ok=None,
        ))
        metrics.append(EvalMetric(
            label="相关度",
            value=f"{ar:.2f}" if isinstance(ar, (int, float)) else "—",
            ok=None,
        ))
    return EvalSummary(
        available=True,
        task="rag",
        timestamp=data.get("timestamp", ""),
        git=data.get("git", ""),
        passed=None,  # RAG 检索指标无统一硬阈，纯展示
        partial=False,
        metrics=metrics,
    )


def _rag_summary(report: str | None = None) -> EvalSummary:
    """RAG 卡片：给 report 读其配对 sidecar；否则取 tools/reports/rag 下最新一份。"""
    if report:
        data = _sidecar_for_report(report)
        return _rag_summary_from_data(data) if data else EvalSummary(available=False, task="rag")
    root = _reports_root() / "rag"
    if root.is_dir():
        jsons = sorted(
            (fp for fp in root.glob("*.json") if fp.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for fp in reversed(jsons):
            data = _load_sidecar(fp)
            if data is not None:
                return _rag_summary_from_data(data)
    return EvalSummary(available=False, task="rag")


def _mcp_summary_from_data(data: dict) -> EvalSummary:
    """MCP sidecar dict → 通用卡片：验收"全过"判定（无失败即过，skipped 不算失败）。"""
    total = data.get("total", 0)
    passed = data.get("passed", 0)
    skipped = data.get("skipped", 0)
    failed = data.get("failed", total - passed - skipped)
    ok = bool(data.get("ok", failed == 0))
    val = f"{passed}/{total}"
    if skipped:
        val += f"（跳过 {skipped}）"
    return EvalSummary(
        available=True,
        task="mcp",
        timestamp=data.get("timestamp", ""),
        git=data.get("git", ""),
        passed=ok,
        # partial = 只跑了部分 case（--no-llm 跳过 llm-e2e）；选了模型跑全量时 skipped=0 → 不 partial
        partial=skipped > 0,
        metrics=[
            EvalMetric(label="通过", value=val, threshold="全过（0 失败）", ok=ok),
        ],
    )


def _mcp_summary(report: str | None = None) -> EvalSummary:
    """MCP 卡片：给 report 读其配对 sidecar；否则取 tools/reports/mcp 下最新一份。"""
    if report:
        data = _sidecar_for_report(report)
        return _mcp_summary_from_data(data) if data else EvalSummary(available=False, task="mcp")
    root = _reports_root() / "mcp"
    if root.is_dir():
        jsons = sorted(
            (fp for fp in root.glob("*.json") if fp.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for fp in reversed(jsons):
            data = _load_sidecar(fp)
            if data is not None:
                return _mcp_summary_from_data(data)
    return EvalSummary(available=False, task="mcp")


_PERF_TARGET_ZH = {"session": "会话", "memory": "记忆"}


def _perf_summary_from_data(data: dict) -> EvalSummary:
    """性能 sidecar dict → 判定型卡片：各 target 的判据逐条 + 整体全过判 pass。"""
    metrics: list[EvalMetric] = []
    targets = data.get("targets", {})
    for t, info in targets.items():
        zh = _PERF_TARGET_ZH.get(t, t)
        for c in info.get("checks", []):
            metrics.append(EvalMetric(
                label=f"{zh}·{c.get('name', '')}",
                value=str(c.get("note", "")),
                ok=bool(c.get("ok", False)),
            ))
    passed = bool(data.get("passed", False))
    return EvalSummary(
        available=True,
        task="perf",
        timestamp=data.get("timestamp", ""),
        git=data.get("git", ""),
        passed=passed,
        partial=False,  # 性能始终跑全量（session + memory），无"部分跑"
        metrics=metrics,
    )


def _perf_summary(report: str | None = None) -> EvalSummary:
    """性能卡片：给 report 读其配对 sidecar；否则取 tools/reports/perf 下最新一份。"""
    if report:
        data = _sidecar_for_report(report)
        return _perf_summary_from_data(data) if data else EvalSummary(available=False, task="perf")
    root = _reports_root() / "perf"
    if root.is_dir():
        jsons = sorted(
            (fp for fp in root.glob("*.json") if fp.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for fp in reversed(jsons):
            data = _load_sidecar(fp)
            if data is not None:
                return _perf_summary_from_data(data)
    return EvalSummary(available=False, task="perf")


def _recall_quality_summary(task: str, subdir: str, score_label: str):
    """识别通过率 + LLM-judge 质量分"双指标判定型 eval 的卡片工厂（plan / learning_plan）。

    sidecar 需含 recall / recall_threshold / recall_passed / total + struct_score / struct_threshold
    （struct_score=None 表示关了 judge，只显示识别一条）+ passed / partial。
    """

    def _from_data(data: dict) -> EvalSummary:
        rate = data.get("recall", 0.0)
        rt = data.get("recall_threshold", 0.0)
        metrics = [
            EvalMetric(
                label="识别通过率",
                value=f"{_pct(rate)} ({data.get('recall_passed', 0)}/{data.get('total', 0)})",
                threshold=f"≥ {_pct(rt)}",
                ok=rate >= rt,
            ),
        ]
        score = data.get("struct_score")
        st = data.get("struct_threshold", 0.0)
        if isinstance(score, (int, float)):
            metrics.append(EvalMetric(
                label=score_label,
                value=f"{score:.2f}/5",
                threshold=f"≥ {st}",
                ok=score >= st,
            ))
        return EvalSummary(
            available=True,
            task=task,
            timestamp=data.get("timestamp", ""),
            git=data.get("git", ""),
            passed=bool(data.get("passed", False)),
            partial=bool(data.get("partial", False)),  # --no-judge = 只跑识别层
            metrics=metrics,
        )

    def _builder(report: str | None = None) -> EvalSummary:
        if report:
            data = _sidecar_for_report(report)
            return _from_data(data) if data else EvalSummary(available=False, task=task)
        root = _reports_root() / subdir
        if root.is_dir():
            jsons = sorted(
                (fp for fp in root.glob("*.json") if fp.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
            for fp in reversed(jsons):
                data = _load_sidecar(fp)
                if data is not None:
                    return _from_data(data)
        return EvalSummary(available=False, task=task)

    return _builder


_plan_summary = _recall_quality_summary("plan", "plan", "plan 结构均分")


def _passrate_summary(task: str, subdir: str, label: str):
    """通用"通过率"型 eval 的卡片构造器工厂（如记忆 / skill / srs 等）。

    sidecar 需含 rate / pass_threshold（可选 ok）；卡片就一条"通过率"指标 + 阈值判定。
    """

    def _from_data(data: dict) -> EvalSummary:
        rate = data.get("rate", 0.0)
        th = data.get("pass_threshold", 0.0)
        return EvalSummary(
            available=True,
            task=task,
            timestamp=data.get("timestamp", ""),
            git=data.get("git", ""),
            passed=bool(data.get("ok", rate >= th)),
            partial=False,
            metrics=[
                EvalMetric(
                    label=label,
                    value=f"{_pct(rate)} ({data.get('passed', 0)}/{data.get('total', 0)})",
                    threshold=f"≥ {_pct(th)}",
                    ok=rate >= th,
                ),
            ],
        )

    def _builder(report: str | None = None) -> EvalSummary:
        if report:
            data = _sidecar_for_report(report)
            return _from_data(data) if data else EvalSummary(available=False, task=task)
        root = _reports_root() / subdir
        if root.is_dir():
            jsons = sorted(
                (fp for fp in root.glob("*.json") if fp.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
            for fp in reversed(jsons):
                data = _load_sidecar(fp)
                if data is not None:
                    return _from_data(data)
        return EvalSummary(available=False, task=task)

    return _builder


# task -> 摘要构造器（接收可选 report 名）；后续 eval 逐个补
_SUMMARY_BUILDERS = {
    "security": _security_summary,
    "rag": _rag_summary,
    "memory": _passrate_summary("memory", "memory", "通过率"),
    "skills": _passrate_summary("skills", "skills", "识别通过率"),
    "mcp": _mcp_summary,
    "perf": _perf_summary,
    "plan": _plan_summary,
    "harness": _passrate_summary("harness", "harness", "通过率"),
    "learning_plan": _recall_quality_summary("learning_plan", "learning_plan", "plan 质量均分"),
    "quiz": _recall_quality_summary("quiz", "quiz", "plan 质量均分"),
    "srs": _passrate_summary("srs", "srs", "识别通过率"),
}


@router.get("/summary", response_model=EvalSummary)
def eval_summary(
    task: str = Query(...),
    report: str | None = Query(None, description="指定历史报告的 .md 名；空=最新一次"),
    _: dict = Depends(require_admin),
) -> EvalSummary:
    """某 eval 的通用摘要卡片：给 report 则读该报告快照，否则最新。未登记 / 无结果 → available=False。"""
    builder = _SUMMARY_BUILDERS.get(task)
    if builder is None:
        return EvalSummary(available=False, task=task)
    return builder(report)
