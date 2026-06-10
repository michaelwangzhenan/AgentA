// 评估 + 可观测类型（iter_14）。与后端 src/api/schemas/eval.py 对齐。

export type GoldenStatus = 'pending' | 'approved' | 'rejected'
export type GoldenSource = 'manual' | 'ai'

export type GoldenItem = {
  id: number
  query: string
  expected_keywords: string[]
  expected_source_contains: string
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
  expected_source_contains: string
  note: string
}

export type GoldenUpdateInput = Partial<{
  query: string
  expected_keywords: string[]
  expected_source_contains: string
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
