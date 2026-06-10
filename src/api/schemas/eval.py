"""评估 + 可观测端点的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── RAG golden 管理 ─────────────────────────────────────────────────────────

class GoldenItem(BaseModel):
    id: int
    query: str
    expected_keywords: list[str]
    expected_source_contains: str
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
    expected_source_contains: str = ""
    note: str = ""


class GoldenUpdateRequest(BaseModel):
    query: str | None = None
    expected_keywords: list[str] | None = None
    expected_source_contains: str | None = None
    note: str | None = None
    status: str | None = None


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
