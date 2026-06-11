// Token 用量统计类型（iter_11）。与后端 src/api/schemas/usage.py 对齐。

export type UsageRange = '1d' | '7d' | '30d' | 'mtd' | 'last_month'

export const RANGE_LABELS: Record<UsageRange, string> = {
  '1d': '今日',
  '7d': '近 7 天',
  '30d': '近 30 天',
  mtd: '本月',
  last_month: '上月',
}

export type UsageMetric = 'total_tokens' | 'cost' | 'count'

export const METRIC_LABELS: Record<UsageMetric, string> = {
  total_tokens: 'Token',
  cost: '成本',
  count: '对话次数',
}

export type UsageSummary = {
  start: number
  end: number
  range: string
  currency: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  count: number
  cost: number
  has_unpriced: boolean
}

export type SeriesRow = {
  date: string
  key: string
  key_label: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  count: number
  cost: number
}

export type UsageSeries = {
  start: number
  end: number
  range: string
  group_by: string
  currency: string
  rows: SeriesRow[]
}

export type UsageEvent = {
  id: number
  created_at: number
  model_id: string
  model_label: string
  tier: string
  thinking: boolean
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number
  session_id: string | null
  user_id: number | null
  username: string | null
}

export type UsageEvents = {
  events: UsageEvent[]
  total: number
  limit: number
  offset: number
  currency: string
}

export type UserUsage = {
  user_id: number
  username: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  count: number
  cost: number
}

export type UserUsageList = {
  users: UserUsage[]
  currency: string
}

export type PricingItem = {
  model_id: string
  label: string
  provider: string
  provider_label: string
  tier: string
  input_price: number
  output_price: number
  is_override: boolean
}

export type PricingResponse = {
  currency: string
  items: PricingItem[]
}

export type PricingUpdateItem = {
  model_id: string
  input_price: number
  output_price: number
}

// 降本看板（模型路由 + 语义缓存节省，iter_14）。对齐 src/api/routes/usage.py。

export type SavingsSummary = {
  start: number
  end: number
  range: string
  currency: string
  route_count: number
  route_saved: number
  cache_count: number
  cache_saved: number
  total_saved: number
  cache_lookups: number
  cache_hits: number
  cache_hit_rate: number
}

export type SavingsSeriesRow = {
  date: string
  kind: 'route' | 'cache'
  count: number
  saved: number
}

export type SavingsSeries = {
  start: number
  end: number
  range: string
  currency: string
  rows: SavingsSeriesRow[]
}
