import { useCallback, useEffect, useState } from 'react'
import { Download } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import {
  getUsageEvents,
  getUsageSeries,
  getUsageSummary,
  getUsageUsers,
  usageEventsCsvUrl,
} from '@/api/client'
import {
  METRIC_LABELS,
  RANGE_LABELS,
  type UsageEvents,
  type UsageMetric,
  type UsageRange,
  type UsageSeries,
  type UsageSummary,
  type UserUsageList,
} from '@/types/usage'
import { TrendChart } from './TrendChart'
import { compactNumber, formatCost, formatTime, fullNumber } from './format'

type Scope = 'mine' | 'all'

const RANGES: UsageRange[] = ['1d', '7d', '30d', 'mtd', 'last_month']
const METRICS: UsageMetric[] = ['total_tokens', 'cost', 'count']
const PAGE_SIZE = 20

type DashboardProps = { scope: Scope }

export function UsageDashboard({ scope }: DashboardProps) {
  const [range, setRange] = useState<UsageRange>('30d')
  const [metric, setMetric] = useState<UsageMetric>('total_tokens')
  const [groupBy, setGroupBy] = useState<string>('model')

  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [series, setSeries] = useState<UsageSeries | null>(null)
  const [users, setUsers] = useState<UserUsageList | null>(null)
  const [events, setEvents] = useState<UsageEvents | null>(null)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)

  const groupOptions =
    scope === 'all'
      ? [
          { value: 'model', label: '按模型' },
          { value: 'user', label: '按用户' },
          { value: 'none', label: '合计' },
        ]
      : [
          { value: 'model', label: '按模型' },
          { value: 'none', label: '合计' },
        ]

  const refreshTop = useCallback(async () => {
    setLoading(true)
    try {
      const [s, ser] = await Promise.all([
        getUsageSummary(range, scope),
        getUsageSeries(range, groupBy, scope),
      ])
      setSummary(s)
      setSeries(ser)
      if (scope === 'all') setUsers(await getUsageUsers(range))
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [range, groupBy, scope])

  const refreshEvents = useCallback(async () => {
    try {
      setEvents(
        await getUsageEvents(range, { scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
      )
    } catch (e) {
      toast.error((e as Error).message)
    }
  }, [range, scope, page])

  useEffect(() => {
    void refreshTop()
  }, [refreshTop])

  useEffect(() => {
    void refreshEvents()
  }, [refreshEvents])

  // 切范围时回到第一页
  useEffect(() => {
    setPage(0)
  }, [range, scope])

  const currency = summary?.currency ?? '$'
  const csvUrl = usageEventsCsvUrl(range, { scope })

  return (
    <div className="space-y-5">
      {/* 工具条 */}
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          options={RANGES.map((r) => ({ value: r, label: RANGE_LABELS[r] }))}
          value={range}
          onChange={(v) => setRange(v as UsageRange)}
        />
        <div className="ml-auto flex items-center gap-3">
          <Segmented
            options={METRICS.map((m) => ({ value: m, label: METRIC_LABELS[m] }))}
            value={metric}
            onChange={(v) => setMetric(v as UsageMetric)}
          />
          <Segmented options={groupOptions} value={groupBy} onChange={setGroupBy} />
          <a href={csvUrl} download>
            <Button variant="outline" size="sm" className="gap-1.5">
              <Download className="h-3.5 w-3.5" />
              导出 CSV
            </Button>
          </a>
        </div>
      </div>

      {/* 概览卡片 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="总 Token" value={summary ? compactNumber(summary.total_tokens) : '—'} />
        <StatCard label="输入" value={summary ? compactNumber(summary.prompt_tokens) : '—'} />
        <StatCard label="输出" value={summary ? compactNumber(summary.completion_tokens) : '—'} />
        <StatCard label="对话次数" value={summary ? fullNumber(summary.count) : '—'} />
        <StatCard
          label="估算成本"
          value={summary ? formatCost(summary.cost, currency) : '—'}
          hint={summary?.has_unpriced ? '部分模型无单价，成本偏低' : undefined}
        />
      </div>

      {/* 趋势图 */}
      <section className="rounded-lg border border-border p-4">
        <h2 className="mb-3 text-sm font-medium">
          趋势 · {METRIC_LABELS[metric]}（{groupOptions.find((o) => o.value === groupBy)?.label}）
        </h2>
        {loading && !series ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : (
          <TrendChart rows={series?.rows ?? []} metric={metric} currency={currency} />
        )}
      </section>

      {/* 用户排行（仅全员视图） */}
      {scope === 'all' && (
        <section className="rounded-lg border border-border p-4">
          <h2 className="mb-3 text-sm font-medium">用户排行</h2>
          {users && users.users.length > 0 ? (
            <div className="overflow-hidden rounded-md border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                    <th className="px-3 py-2 font-medium">用户</th>
                    <th className="px-3 py-2 text-right font-medium">总 Token</th>
                    <th className="px-3 py-2 text-right font-medium">输入 / 输出</th>
                    <th className="px-3 py-2 text-right font-medium">对话次数</th>
                    <th className="px-3 py-2 text-right font-medium">成本</th>
                  </tr>
                </thead>
                <tbody>
                  {users.users.map((u) => (
                    <tr key={u.user_id} className="border-b border-border last:border-0">
                      <td className="px-3 py-2">{u.username}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{fullNumber(u.total_tokens)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {compactNumber(u.prompt_tokens)} / {compactNumber(u.completion_tokens)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{fullNumber(u.count)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatCost(u.cost, users.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">暂无数据</p>
          )}
        </section>
      )}

      {/* 明细 */}
      <section className="rounded-lg border border-border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium">对话明细</h2>
          {events && (
            <span className="text-xs text-muted-foreground">共 {events.total} 条</span>
          )}
        </div>
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">时间</th>
                {scope === 'all' && <th className="px-3 py-2 font-medium">用户</th>}
                <th className="px-3 py-2 font-medium">模型</th>
                <th className="px-3 py-2 text-right font-medium">输入</th>
                <th className="px-3 py-2 text-right font-medium">输出</th>
                <th className="px-3 py-2 text-right font-medium">合计</th>
                <th className="px-3 py-2 text-right font-medium">成本</th>
              </tr>
            </thead>
            <tbody>
              {events && events.events.length > 0 ? (
                events.events.map((e) => (
                  <tr key={e.id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 text-xs text-muted-foreground">{formatTime(e.created_at)}</td>
                    {scope === 'all' && <td className="px-3 py-2">{e.username ?? `#${e.user_id}`}</td>}
                    <td className="px-3 py-2">
                      {e.model_label}
                      {e.thinking && (
                        <span className="ml-1.5 rounded bg-accent px-1 py-0.5 text-[10px] text-accent-foreground">
                          思考
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{fullNumber(e.prompt_tokens)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fullNumber(e.completion_tokens)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fullNumber(e.total_tokens)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatCost(e.cost, currency)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={scope === 'all' ? 7 : 6} className="px-3 py-6 text-center text-sm text-muted-foreground">
                    暂无明细
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {events && events.total > PAGE_SIZE && (
          <div className="mt-3 flex items-center justify-end gap-2 text-sm">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              上一页
            </Button>
            <span className="text-xs text-muted-foreground">
              第 {page + 1} / {Math.ceil(events.total / PAGE_SIZE)} 页
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={(page + 1) * PAGE_SIZE >= events.total}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        )}
      </section>

      <p className="text-xs text-muted-foreground">
        统计口径：一次对话（Agent 运行）记一条。成本为按当前单价的估算值，仅供参考，
        实际以各厂商账单为准；可在「单价配置」中调整。
      </p>
    </div>
  )
}

type SegmentedProps = {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
}

function Segmented({ options, value, onChange }: SegmentedProps) {
  return (
    <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded px-2.5 py-1 text-xs transition-colors',
            value === o.value
              ? 'bg-background font-medium text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-[10px] text-amber-600 dark:text-amber-500">{hint}</div>}
    </div>
  )
}
