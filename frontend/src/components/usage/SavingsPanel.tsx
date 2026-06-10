import { useCallback, useEffect, useState } from 'react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { getSavingsSeries, getSavingsSummary } from '@/api/client'
import {
  RANGE_LABELS,
  type SavingsSeries,
  type SavingsSummary,
  type UsageRange,
} from '@/types/usage'
import { formatCost, fullNumber } from './format'

type Scope = 'mine' | 'all'

const RANGES: UsageRange[] = ['1d', '7d', '30d', 'mtd', 'last_month']

export function SavingsPanel({ scope }: { scope: Scope }) {
  const [range, setRange] = useState<UsageRange>('30d')
  const [summary, setSummary] = useState<SavingsSummary | null>(null)
  const [series, setSeries] = useState<SavingsSeries | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [s, ser] = await Promise.all([
        getSavingsSummary(range, scope),
        getSavingsSeries(range, scope),
      ])
      setSummary(s)
      setSeries(ser)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [range, scope])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const currency = summary?.currency ?? '¥'

  return (
    <div className="space-y-5">
      <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5">
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={cn(
              'rounded px-2.5 py-1 text-xs transition-colors',
              range === r
                ? 'bg-background font-medium text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {RANGE_LABELS[r]}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="累计节省" value={summary ? formatCost(summary.total_saved, currency) : '—'} accent />
        <Stat label="路由降级次数" value={summary ? fullNumber(summary.route_count) : '—'} />
        <Stat label="路由节省" value={summary ? formatCost(summary.route_saved, currency) : '—'} />
        <Stat label="缓存命中次数" value={summary ? fullNumber(summary.cache_count) : '—'} />
        <Stat label="缓存节省" value={summary ? formatCost(summary.cache_saved, currency) : '—'} />
      </div>

      <section className="rounded-lg border border-border p-4">
        <h2 className="mb-3 text-sm font-medium">每日节省明细</h2>
        {loading && !series ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : series && series.rows.length > 0 ? (
          <div className="overflow-hidden rounded-md border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                  <th className="px-3 py-2 font-medium">日期</th>
                  <th className="px-3 py-2 font-medium">类型</th>
                  <th className="px-3 py-2 text-right font-medium">次数</th>
                  <th className="px-3 py-2 text-right font-medium">节省</th>
                </tr>
              </thead>
              <tbody>
                {series.rows.map((r, i) => (
                  <tr key={`${r.date}-${r.kind}-${i}`} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 text-xs text-muted-foreground">{r.date}</td>
                    <td className="px-3 py-2">{r.kind === 'route' ? '模型路由' : '语义缓存'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fullNumber(r.count)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatCost(r.saved, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无节省记录</p>
        )}
      </section>

      <p className="text-xs text-muted-foreground">
        节省为估算值：路由按"基准模型成本 − 实际模型成本"（同 token 数）计；缓存命中按
        "本应生成所需成本"（按答案长度粗估）计。仅供参考，实际以各厂商账单为准。
      </p>
    </div>
  )
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={cn(
          'mt-1 text-lg font-semibold tabular-nums',
          accent && 'text-green-600 dark:text-green-400',
        )}
      >
        {value}
      </div>
    </div>
  )
}
