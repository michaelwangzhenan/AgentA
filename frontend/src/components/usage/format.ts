import type { SeriesRow, UsageMetric } from '@/types/usage'

/** 紧凑展示大数字：1234567 → 1.23M、34567 → 34.6k、小数原样。 */
export function compactNumber(n: number): string {
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(Math.round(n))
}

/** 千分位完整数字（明细表 / tooltip 用）。 */
export function fullNumber(n: number): string {
  return Math.round(n).toLocaleString('en-US')
}

/** 成本：币种符号 + 自适应小数位（极小值多留几位）。 */
export function formatCost(cost: number, currency: string): string {
  if (cost === 0) return `${currency}0`
  const digits = cost < 0.01 ? 5 : cost < 1 ? 4 : 2
  return `${currency}${cost.toFixed(digits)}`
}

/** 从 SeriesRow 取某指标的数值。 */
export function metricValue(row: SeriesRow, metric: UsageMetric): number {
  if (metric === 'cost') return row.cost
  if (metric === 'count') return row.count
  return row.total_tokens
}

/** 按指标格式化（图例 / 轴 / tooltip 统一口径）。 */
export function formatMetric(
  value: number,
  metric: UsageMetric,
  currency: string,
): string {
  if (metric === 'cost') return formatCost(value, currency)
  if (metric === 'count') return fullNumber(value)
  return compactNumber(value)
}

/** epoch 秒 → 本地可读时间。 */
export function formatTime(epochSec: number): string {
  return new Date(epochSec * 1000).toLocaleString()
}

// 趋势图 / 图例配色（足够区分常见模型数；超出循环复用）
export const CHART_COLORS = [
  '#6366f1', '#22c55e', '#f59e0b', '#ec4899', '#06b6d4',
  '#a855f7', '#ef4444', '#14b8a6', '#eab308', '#3b82f6',
  '#f97316', '#84cc16',
]
