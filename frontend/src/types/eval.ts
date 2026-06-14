// 评估 + 可观测类型（iter_14）。与后端 src/api/schemas/eval.py 对齐。

export type GoldenStatus = 'pending' | 'approved' | 'rejected'
export type GoldenSource = 'manual' | 'ai'

export type GoldenItem = {
  id: number
  query: string
  expected_keywords: string[]
  expected_source: string
  expected_source_contains: string
  type: string
  note: string
  source: GoldenSource
  status: GoldenStatus
  doc_id: string
  created_at: number
  updated_at: number
}

export type GoldenList = {
  items: GoldenItem[]
  total: number
  limit: number
  offset: number
  counts: Record<string, number>
}

export type GoldenCreateInput = {
  query: string
  expected_keywords: string[]
  expected_source: string
  expected_source_contains: string
  type: string
  note: string
}

export type GoldenUpdateInput = Partial<{
  query: string
  expected_keywords: string[]
  expected_source: string
  expected_source_contains: string
  type: string
  note: string
  status: GoldenStatus
}>

export type TraceOverview = {
  range: string
  count: number
  error_count: number
  error_rate: number
  latency_p50_ms: number
  latency_p95_ms: number
  latency_avg_ms: number
  avg_llm_ms: number
  avg_tool_ms: number
  avg_retrieval_ms: number
}

export type TraceSeriesRow = {
  day: string
  count: number
  avg_ms: number
  error_count: number
}

export type TraceSeries = {
  range: string
  rows: TraceSeriesRow[]
}

export type TraceListItem = {
  trace_id: string
  session_id: string | null
  created_at: number
  model_id: string
  thinking: boolean
  total_ms: number
  llm_ms: number
  tool_ms: number
  retrieval_ms: number
  llm_calls: number
  tool_calls: number
  total_tokens: number
  status: string
  error_phase: string
}

export type TraceList = {
  items: TraceListItem[]
  total: number
  limit: number
  offset: number
}

export type TraceSpan = {
  stage: string
  name: string
  start_ms: number
  duration_ms: number
  status: string
}

export type TraceDetail = TraceListItem & {
  prompt_tokens: number
  completion_tokens: number
  spans: TraceSpan[]
}

export type ReportItem = {
  name: string
  size: number
  modified_at: number
}

export type ReportList = {
  reports: ReportItem[]
}

export type ReportContent = {
  name: string
  content: string
}

// 安全红队看板（对齐 src/api/schemas/eval.py 的 SecuritySummary / SecurityTrend）

export type SecurityKindRow = {
  kind: string
  total: number
  attacks: number
  attack_blocked: number
  recall: number
  benigns: number
  benign_blocked: number
  fpr: number
}

export type SecuritySummary = {
  available: boolean
  timestamp: string
  git: string
  partial: boolean
  kinds_run: string[]
  total: number
  attacks: number
  attack_blocked: number
  benigns: number
  benign_blocked: number
  recall: number
  fpr: number
  recall_threshold: number
  fpr_threshold: number
  passed: boolean
  by_kind: SecurityKindRow[]
}

export type SecurityTrendPoint = {
  timestamp: string
  recall: number
  fpr: number
  total: number
  partial: boolean
}

export type SecurityTrend = {
  points: SecurityTrendPoint[]
}

// 实时安全监控（线上拦截事件，对齐 SecurityRuntimeSummary）

export type SecurityEventRow = {
  event_type: string // scrub | tool | ssrf
  detail: string
  user_id: number
  created_at: number
}

export type SecurityRuntimeSummary = {
  range: string
  total: number
  by_type: Record<string, number>
  recent: SecurityEventRow[]
}

// 离线评估：触发 / 状态 / 通用摘要卡片（对齐 src/api/schemas/eval.py）

export type EvalRunRequest = {
  task: string
  model?: string | null
  no_llm?: boolean
  options?: Record<string, string | boolean | number>
  thresholds?: Record<string, number>
}

export type EvalRunState = 'idle' | 'running' | 'done'

export type EvalRunStatus = {
  state: EvalRunState
  task?: string | null
  model?: string | null
  args: string[]
  started_at?: number | null
  finished_at?: number | null
  returncode?: number | null
  tail: string
}

export type EvalMetric = {
  label: string
  value: string
  threshold: string
  ok: boolean | null // null = 无判定（如性能）
}

export type EvalSummary = {
  available: boolean
  task: string
  timestamp: string
  git: string
  passed: boolean | null // null = 无 pass/fail
  partial: boolean
  metrics: EvalMetric[]
}
