import { useCallback, useEffect, useState } from 'react'
import { Info } from 'lucide-react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import {
  getTraceDetail,
  getTraceList,
  getTraceOverview,
  getTraceSeries,
} from '@/api/client'
import type {
  TraceDetail,
  TraceList,
  TraceOverview,
  TraceSeries,
} from '@/types/eval'
import { useUrlState } from '@/routes/useUrlState'

type Range = '1d' | '7d' | '30d' | 'mtd' | 'last_month'
const RANGES: { value: Range; label: string }[] = [
  { value: '1d', label: '今日' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: 'mtd', label: '本月' },
  { value: 'last_month', label: '上月' },
]

const STAGE_COLORS: Record<string, string> = {
  llm: 'bg-blue-500',
  tool: 'bg-amber-500',
  retrieval: 'bg-emerald-500',
}
const STAGE_LABELS: Record<string, string> = {
  llm: 'LLM',
  tool: '工具',
  retrieval: '检索',
}

const PAGE_SIZE = 20

function ms(v: number): string {
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`
  return `${v.toFixed(0)} ms`
}

export function TraceDashboard() {
  const { isAdmin } = useAuth()
  const url = useUrlState()
  const parseRange = (v: string): Range =>
    (RANGES.map((r) => r.value) as string[]).includes(v) ? (v as Range) : '30d'
  const range = parseRange(url.get('range', '30d'))
  const scope: 'mine' | 'all' = url.get('scope') === 'all' ? 'all' : 'mine'
  const page = Math.max(0, url.getInt('page', 0))
  const traceId = url.get('trace')

  const setRange = (r: Range) => url.patch({ range: r === '30d' ? null : r, page: null })
  const setScope = (s: 'mine' | 'all') =>
    url.patch({ scope: s === 'mine' ? null : s, page: null })
  const setPage = (p: number) => url.patch({ page: p <= 0 ? null : p })

  const [overview, setOverview] = useState<TraceOverview | null>(null)
  const [series, setSeries] = useState<TraceSeries | null>(null)
  const [list, setList] = useState<TraceList | null>(null)
  const [loading, setLoading] = useState(true)
  const [openTrace, setOpenTrace] = useState<TraceDetail | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [o, s, l] = await Promise.all([
        getTraceOverview(range, scope),
        getTraceSeries(range, scope),
        getTraceList(range, { scope, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
      ])
      setOverview(o)
      setSeries(s)
      setList(l)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [range, scope, page])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!traceId) {
      setOpenTrace(null)
      return
    }
    void getTraceDetail(traceId)
      .then(setOpenTrace)
      .catch((e) => toast.error((e as Error).message))
  }, [traceId])

  const maxDay = Math.max(1, ...(series?.rows.map((r) => r.avg_ms) ?? [1]))

  const openDetail = (id: string) => url.patch({ trace: id })
  const closeDetail = () => url.patch({ trace: null })

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          options={RANGES.map((r) => ({ value: r.value, label: r.label }))}
          value={range}
          onChange={(v) => setRange(v as Range)}
        />
        {isAdmin && (
          <Segmented
            options={[
              { value: 'mine', label: '我的' },
              { value: 'all', label: '全员' },
            ]}
            value={scope}
            onChange={(v) => setScope(v as 'mine' | 'all')}
          />
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard
          label="对话数"
          value={overview ? String(overview.count) : '—'}
          tip="选定时间范围内的对话总数"
        />
        <StatCard
          label="错误率"
          value={overview ? `${(overview.error_rate * 100).toFixed(1)}%` : '—'}
          hint={overview && overview.error_count > 0 ? `${overview.error_count} 次出错` : undefined}
          tip="运行中发生错误对话比例"
        />
        <StatCard
          label="延迟 P50"
          value={overview ? ms(overview.latency_p50_ms) : '—'}
          tip="对话总耗时的中位数：一半对话比它快、一半比它慢，代表典型体验"
        />
        <StatCard
          label="延迟 P95"
          value={overview ? ms(overview.latency_p95_ms) : '—'}
          tip="对话总耗时的 95 分位：只有 5% 的对话比它更慢，用来看长尾卡顿"
        />
        <StatCard
          label="平均 LLM"
          value={overview ? ms(overview.avg_llm_ms) : '—'}
          tip="每次对话里所有 LLM 调用累计耗时的平均值"
        />
        <StatCard
          label="平均检索"
          value={overview ? ms(overview.avg_retrieval_ms) : '—'}
          tip="每次对话里知识库检索累计耗时的平均值"
        />
        <StatCard
          label="平均工具"
          value={overview ? ms(overview.avg_tool_ms) : '—'}
          tip="每次对话里工具调用（不含知识库检索）累计耗时的平均值"
        />
        <StatCard
          label="平均总耗时"
          value={overview ? ms(overview.latency_avg_ms) : '—'}
          tip="每次对话端到端总耗时的平均值（从开始到出最终答案）"
        />
      </div>

      {/* 趋势：每日平均延迟 */}
      <section className="rounded-lg border border-border p-4">
        <h2 className="mb-3 text-sm font-medium">每日平均延迟</h2>
        {loading && !series ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : series && series.rows.length > 0 ? (
          <div className="space-y-1.5">
            {series.rows.map((r) => (
              <div key={r.day} className="flex items-center gap-2 text-xs">
                <span className="w-20 shrink-0 text-muted-foreground">{r.day.slice(5)}</span>
                <div className="relative h-4 flex-1 rounded bg-muted/40">
                  <div
                    className="h-4 rounded bg-blue-500/70"
                    style={{ width: `${(r.avg_ms / maxDay) * 100}%` }}
                  />
                </div>
                <span className="w-16 shrink-0 text-right tabular-nums">{ms(r.avg_ms)}</span>
                <span className="w-14 shrink-0 text-right text-muted-foreground tabular-nums">
                  {r.count} 次
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无数据</p>
        )}
      </section>

      {/* 对话明细 */}
      <section className="rounded-lg border border-border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium">对话明细（点击看阶段瀑布）</h2>
          {list && <span className="text-xs text-muted-foreground">共 {list.total} 条</span>}
        </div>
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">时间</th>
                <th className="px-3 py-2 font-medium">模型</th>
                <th className="px-3 py-2 text-right font-medium">总耗时</th>
                <th className="px-3 py-2 text-right font-medium">LLM/工具/检索</th>
                <th className="px-3 py-2 text-right font-medium">Token</th>
                <th className="px-3 py-2 text-center font-medium">状态</th>
              </tr>
            </thead>
            <tbody>
              {list && list.items.length > 0 ? (
                list.items.map((t) => (
                  <tr
                    key={t.trace_id}
                    className="cursor-pointer border-b border-border last:border-0 hover:bg-accent/40"
                    onClick={() => openDetail(t.trace_id)}
                  >
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {new Date(t.created_at * 1000).toLocaleString()}
                    </td>
                    <td className="px-3 py-2">{t.model_id || '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{ms(t.total_ms)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                      {ms(t.llm_ms)} / {ms(t.tool_ms)} / {ms(t.retrieval_ms)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{t.total_tokens}</td>
                    <td className="px-3 py-2 text-center">
                      {t.status === 'ok' ? (
                        <span className="text-emerald-600">正常</span>
                      ) : (
                        <span className="text-destructive" title={t.error_phase}>
                          出错
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-sm text-muted-foreground">
                    暂无 trace（发起几次对话后即可看到）
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {list && list.total > PAGE_SIZE && (
          <div className="mt-3 flex items-center justify-end gap-2 text-sm">
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
              上一页
            </Button>
            <span className="text-xs text-muted-foreground">
              第 {page + 1} / {Math.ceil(list.total / PAGE_SIZE)} 页
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={(page + 1) * PAGE_SIZE >= list.total}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </Button>
          </div>
        )}
      </section>

      {openTrace && <WaterfallPanel trace={openTrace} onClose={closeDetail} />}
    </div>
  )
}

function WaterfallPanel({ trace, onClose }: { trace: TraceDetail; onClose: () => void }) {
  const total = Math.max(1, trace.total_ms || Math.max(...trace.spans.map((s) => s.start_ms + s.duration_ms), 1))
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-border bg-background p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">阶段瀑布</h3>
          <Button variant="outline" size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">
          {trace.model_id} · 总 {ms(trace.total_ms)} · {trace.llm_calls} 轮 LLM · {trace.tool_calls} 次工具 ·{' '}
          {trace.total_tokens} token
        </p>
        {trace.spans.length === 0 ? (
          <p className="text-sm text-muted-foreground">无阶段数据</p>
        ) : (
          <div className="space-y-2">
            {trace.spans.map((s, i) => (
              <div key={i} className="text-xs">
                <div className="mb-0.5 flex justify-between">
                  <span>
                    <span
                      className={cn('mr-1.5 inline-block h-2 w-2 rounded-full', STAGE_COLORS[s.stage] ?? 'bg-gray-400')}
                    />
                    {STAGE_LABELS[s.stage] ?? s.stage} · {s.name}
                  </span>
                  <span className="tabular-nums text-muted-foreground">{ms(s.duration_ms)}</span>
                </div>
                <div className="relative h-3 w-full rounded bg-muted/40">
                  <div
                    className={cn('absolute h-3 rounded', STAGE_COLORS[s.stage] ?? 'bg-gray-400')}
                    style={{
                      left: `${Math.min(100, (s.start_ms / total) * 100)}%`,
                      width: `${Math.max(1, Math.min(100, (s.duration_ms / total) * 100))}%`,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
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

function StatCard({
  label,
  value,
  hint,
  tip,
}: {
  label: string
  value: string
  hint?: string
  tip?: string
}) {
  return (
    <div className={cn('rounded-lg border border-border p-3', tip && 'cursor-help')} title={tip}>
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        {tip && <Info className="h-3 w-3 opacity-50" aria-hidden />}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-[10px] text-amber-600 dark:text-amber-500">{hint}</div>}
    </div>
  )
}
