"""评估 + 可观测端点的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── RAG golden 管理 ─────────────────────────────────────────────────────────

class GoldenItem(BaseModel):
    id: int
    query: str
    expected_keywords: list[str]
    expected_source: str           # 精确匹配 hit.source
    expected_source_contains: str  # 子串匹配 hit.source
    type: str                      # 人工分类标签（baseline / hyde…），评估不参与，仅供切片分析
    note: str
    source: str        # manual | ai
    status: str        # pending | approved | rejected
    doc_id: str
    created_at: int
    updated_at: int


class GoldenList(BaseModel):
    items: list[GoldenItem]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class GoldenCreateRequest(BaseModel):
    query: str = Field(..., min_length=1)
    expected_keywords: list[str] = Field(default_factory=list)
    expected_source: str = ""
    expected_source_contains: str = ""
    type: str = ""
    note: str = ""


class GoldenUpdateRequest(BaseModel):
    query: str | None = None
    expected_keywords: list[str] | None = None
    expected_source: str | None = None
    expected_source_contains: str | None = None
    type: str | None = None
    note: str | None = None
    status: str | None = None


class GoldenGenerateRequest(BaseModel):
    """为某个已入库文档手动生成 golden 候选（pending/ai）。"""
    model: str = Field(..., description="库别名 en/zh/m3")
    source: str = Field(..., min_length=1, description="文档相对 web_uploads/<model> 的路径")
    doc_id: str = Field("", description="KB 文档 doc_id；用于关联 + 重生成前清旧 pending")
    golden_llm: str | None = Field(
        None, description="出题 LLM：kimi-k2.5 | deepseek-v4-flash；缺省回落 env / kimi-k2.5"
    )
    golden_max_q: int | None = Field(None, description="出题数量；缺省 EVAL_GOLDEN_MAX_Q")


class GoldenGenerateResponse(BaseModel):
    generated: int        # 本次写入的候选条数
    removed_pending: int   # 重生成前清掉的旧 pending 数


class GoldenLlmChoice(BaseModel):
    value: str
    label: str


class GoldenGenOptionsResponse(BaseModel):
    """入库 / L2 生成 golden 的下拉选项（与 golden_options 同源）。"""
    llm_choices: list[GoldenLlmChoice]
    max_q_default: int
    max_q_min: int
    max_q_max: int


# ── trace 可观测 ─────────────────────────────────────────────────────────────

class TraceOverview(BaseModel):
    range: str
    count: int
    error_count: int
    error_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_avg_ms: float
    avg_llm_ms: float
    avg_tool_ms: float
    avg_retrieval_ms: float


class TraceSeriesRow(BaseModel):
    day: str
    count: int
    avg_ms: float
    error_count: int


class TraceSeries(BaseModel):
    range: str
    rows: list[TraceSeriesRow]


class TraceListItem(BaseModel):
    trace_id: str
    session_id: str | None
    created_at: int
    model_id: str
    thinking: bool
    total_ms: float
    llm_ms: float
    tool_ms: float
    retrieval_ms: float
    llm_calls: int
    tool_calls: int
    total_tokens: int
    status: str
    error_phase: str


class TraceList(BaseModel):
    items: list[TraceListItem]
    total: int
    limit: int
    offset: int


class TraceSpan(BaseModel):
    stage: str
    name: str
    start_ms: float
    duration_ms: float
    status: str


class TraceDetail(TraceListItem):
    prompt_tokens: int
    completion_tokens: int
    spans: list[TraceSpan]


# ── 评估报告列表 ─────────────────────────────────────────────────────────────

class ReportItem(BaseModel):
    name: str          # 相对标识（含子目录），用于回查内容
    size: int
    modified_at: int


class ReportList(BaseModel):
    reports: list[ReportItem]


class ReportContent(BaseModel):
    name: str
    content: str


# ── 安全红队看板（读 security-adversarial-*.json sidecar） ────────────────────

class SecurityKindRow(BaseModel):
    kind: str            # direct | indirect_rag | indirect_web | tool_blocklist | ssrf | info_leak
    total: int
    attacks: int
    attack_blocked: int
    recall: float
    benigns: int
    benign_blocked: int
    fpr: float


class SecuritySummary(BaseModel):
    available: bool                       # 是否有可用的 sidecar（无则前端提示先跑评估）
    timestamp: str = ""
    git: str = ""
    partial: bool = False                 # 是否只跑了部分类别（如 --no-llm）
    kinds_run: list[str] = Field(default_factory=list)
    total: int = 0
    attacks: int = 0
    attack_blocked: int = 0
    benigns: int = 0
    benign_blocked: int = 0
    recall: float = 0.0
    fpr: float = 0.0
    recall_threshold: float = 0.0
    fpr_threshold: float = 0.0
    passed: bool = False
    by_kind: list[SecurityKindRow] = Field(default_factory=list)


class SecurityTrendPoint(BaseModel):
    timestamp: str
    recall: float
    fpr: float
    total: int
    partial: bool


class SecurityTrend(BaseModel):
    points: list[SecurityTrendPoint]      # 按时间升序


# ── 实时安全监控（线上拦截事件） ─────────────────────────────────────────────

class SecurityEventRow(BaseModel):
    event_type: str                       # scrub | tool | ssrf
    detail: str
    user_id: int
    created_at: int


class SecurityRuntimeSummary(BaseModel):
    range: str
    total: int
    by_type: dict[str, int]               # {scrub, tool, ssrf} → 计数
    recent: list[SecurityEventRow]        # 最近若干条（时间倒序）


class SecurityEventPage(BaseModel):
    items: list[SecurityEventRow]
    total: int
    limit: int
    offset: int


# ── 离线评估：触发 / 状态 / 通用摘要卡片 ─────────────────────────────────────

class EvalRunRequest(BaseModel):
    task: str = Field(..., description="评估任务 key，如 security")
    model: str | None = Field(None, description="测试模型 id（注入子进程 ACTIVE_MODEL）；空=用默认")
    no_llm: bool = Field(False, description="不调用 LLM（仅确定性子集 / 仅检索）")
    options: dict[str, object] = Field(
        default_factory=dict,
        description="各 eval 自有选项（如安全 kind、RAG no_rewriter/no_rerank/llm_count）；后端按 task 白名单取",
    )
    thresholds: dict[str, float] | None = Field(
        None, description="判定阈值覆盖（不持久化），如 {recall:0.9, fpr:0.1}"
    )


class EvalRunStatus(BaseModel):
    state: str = Field(..., description="idle | running | done")
    task: str | None = None
    model: str | None = None
    args: list[str] = Field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    returncode: int | None = None
    tail: str = ""                        # 日志末尾若干行


class EvalMetric(BaseModel):
    label: str
    value: str
    threshold: str = ""
    ok: bool | None = None                # None = 无判定（如性能基准）


class EvalSummary(BaseModel):
    available: bool
    task: str
    timestamp: str = ""
    git: str = ""
    passed: bool | None = None            # None = 无 pass/fail（如性能）
    partial: bool = False
    metrics: list[EvalMetric] = Field(default_factory=list)
