import { useCallback, useEffect, useState } from 'react'
import { AlertCircle } from 'lucide-react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/button'
import {
  getSecurityRuntimeSummary,
  getSecuritySummary,
  getSecurityTrend,
} from '@/api/client'
import type {
  SecurityRuntimeSummary,
  SecuritySummary,
  SecurityTrend,
} from '@/types/eval'

// 类别中文名（与 adversarial.py 的 kind 对齐）
const KIND_LABELS: Record<string, string> = {
  direct: '直接注入',
  indirect_rag: '间接注入（RAG）',
  indirect_web: '间接注入（Web）',
  tool_blocklist: '越权调用',
  ssrf: 'SSRF',
  info_leak: '信息泄露',
}

// 实时拦截事件类型中文名
const EVENT_TYPE_LABELS: Record<string, string> = {
  scrub: '注入清洗',
  tool: '越权调用拦截',
  ssrf: 'SSRF 拦截',
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

// 顶部：质量看板入口，先展示线上真实拦截，再展示离线红队评估。
// 实时安全监控页：对话进行中真实发生的拦截（线上实况）。
export function RuntimeMonitor() {
  const [data, setData] = useState<SecurityRuntimeSummary | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setData(await getSecurityRuntimeSummary())
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  if (loading && !data) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }

  const byType = data?.by_type ?? {}
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">实时安全监控</h2>
          <p className="text-xs text-muted-foreground">对话进行中真实发生的拦截（近 30 天）</p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          刷新
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="总拦截" value={String(data?.total ?? 0)} />
        <StatCard label="注入清洗" value={String(byType.scrub ?? 0)} />
        <StatCard label="越权调用" value={String(byType.tool ?? 0)} />
        <StatCard label="SSRF" value={String(byType.ssrf ?? 0)} />
      </div>

      <section className="rounded-lg border border-border p-4">
        <h3 className="mb-3 text-sm font-medium">最近拦截</h3>
        {data && data.recent.length > 0 ? (
          <div className="overflow-hidden rounded-md border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium">类型</th>
                  <th className="px-3 py-2 font-medium">详情</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map((e, i) => (
                  <tr key={`${e.created_at}-${i}`} className="border-b border-border last:border-0">
                    <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                      {new Date(e.created_at * 1000).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      {EVENT_TYPE_LABELS[e.event_type] ?? e.event_type}
                    </td>
                    <td className="break-all px-3 py-2 text-muted-foreground">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            近 30 天暂无拦截记录（对话中触发防御后会自动记录到这里）。
          </p>
        )}
      </section>
    </div>
  )
}

// 离线安全评估页：红队样本主动测防御，出拦截率 / 误拦率。
export function OfflineEval() {
  const [summary, setSummary] = useState<SecuritySummary | null>(null)
  const [trend, setTrend] = useState<SecurityTrend | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [s, t] = await Promise.all([getSecuritySummary(), getSecurityTrend()])
      setSummary(s)
      setTrend(t)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  if (loading && !summary) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }

  if (!summary || !summary.available) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold">离线安全评估</h2>
            <p className="text-xs text-muted-foreground">红队样本主动测防御，出拦截率 / 误拦率</p>
          </div>
          <Button variant="outline" size="sm" onClick={refresh}>
            刷新
          </Button>
        </div>
        <p className="rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
          暂无评估结果。本地跑{' '}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">
            python -m tools.agent_eval.security.adversarial
          </code>{' '}
          后生成。
        </p>
      </div>
    )
  }

  const recallOk = summary.recall >= summary.recall_threshold
  const fprOk = summary.fpr <= summary.fpr_threshold

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-sm font-semibold">离线安全评估</h2>
        <p className="text-xs text-muted-foreground">红队样本主动测防御，出拦截率 / 误拦率</p>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-muted-foreground">
          最近评估：{summary.timestamp || '—'}
          {summary.git ? ` · ${summary.git}` : ''}
          {summary.partial && (
            <span
              className="ml-2 inline-flex cursor-help items-center gap-0.5 rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600 dark:text-amber-500"
              title="只跑了部分攻击类别"
            >
              部分类别（{summary.kinds_run.join(', ') || '—'}）
              <AlertCircle className="h-3 w-3 shrink-0" />
            </span>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          刷新
        </Button>
      </div>

      {/* 核心指标 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="拦截率"
          value={pct(summary.recall)}
          hint={`${summary.attack_blocked}/${summary.attacks} 攻击 · 阈值 ≥ ${pct(summary.recall_threshold)}`}
          tone={recallOk ? 'ok' : 'bad'}
        />
        <StatCard
          label="误拦率"
          value={pct(summary.fpr)}
          hint={`${summary.benign_blocked}/${summary.benigns} 良性 · 阈值 ≤ ${pct(summary.fpr_threshold)}`}
          tone={fprOk ? 'ok' : 'bad'}
        />
        <StatCard label="总 case" value={String(summary.total)} hint={`攻击 ${summary.attacks} · 良性 ${summary.benigns}`} />
        <StatCard
          label="门禁判定"
          value={summary.passed ? '通过' : '未过'}
          tone={summary.passed ? 'ok' : 'bad'}
        />
      </div>

      {/* 逐类分项 */}
      <section className="rounded-lg border border-border p-4">
        <h2 className="mb-3 text-sm font-medium">逐类分项</h2>
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">类别</th>
                <th className="px-3 py-2 text-right font-medium">攻击拦截</th>
                <th className="px-3 py-2 text-right font-medium">类拦截率</th>
                <th className="px-3 py-2 text-right font-medium">良性误拦</th>
                <th className="px-3 py-2 text-right font-medium">类误拦率</th>
              </tr>
            </thead>
            <tbody>
              {summary.by_kind.length > 0 ? (
                summary.by_kind.map((k) => (
                  <tr key={k.kind} className="border-b border-border last:border-0">
                    <td className="px-3 py-2">{KIND_LABELS[k.kind] ?? k.kind}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                      {k.attack_blocked}/{k.attacks}
                    </td>
                    <td
                      className={cn(
                        'px-3 py-2 text-right tabular-nums',
                        k.attacks > 0 && k.recall < summary.recall_threshold && 'text-destructive',
                      )}
                    >
                      {k.attacks > 0 ? pct(k.recall) : '—'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                      {k.benign_blocked}/{k.benigns}
                    </td>
                    <td
                      className={cn(
                        'px-3 py-2 text-right tabular-nums',
                        k.benigns > 0 && k.fpr > summary.fpr_threshold && 'text-destructive',
                      )}
                    >
                      {k.benigns > 0 ? pct(k.fpr) : '—'}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-sm text-muted-foreground">
                    无分项数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* 趋势：历次拦截率 / 误拦率 */}
      <section className="rounded-lg border border-border p-4">
        <h2 className="mb-3 text-sm font-medium">历次趋势（拦截率 / 误拦率）</h2>
        {trend && trend.points.length > 0 ? (
          <div className="space-y-1.5">
            {trend.points.map((p, i) => (
              <div key={`${p.timestamp}-${i}`} className="flex items-center gap-2 text-xs">
                <span className="w-44 shrink-0 whitespace-nowrap text-muted-foreground">
                  {p.timestamp ? p.timestamp.replace('T', ' ') : '—'}
                  {p.partial && (
                    <span
                      className="ml-1 inline-flex cursor-help items-center gap-0.5 align-middle text-amber-600 dark:text-amber-500"
                      title="只跑了部分攻击类别"
                    >
                      ·部分
                      <AlertCircle className="h-3 w-3 shrink-0" />
                    </span>
                  )}
                </span>
                <div className="relative h-4 flex-1 rounded bg-muted/40">
                  <div className="h-4 rounded bg-emerald-500/70" style={{ width: `${p.recall * 100}%` }} />
                </div>
                <span className="w-28 shrink-0 text-right tabular-nums text-muted-foreground">
                  拦 {pct(p.recall)} · 误 {pct(p.fpr)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无历史数据</p>
        )}
      </section>
    </div>
  )
}

function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string
  hint?: string
  tone?: 'ok' | 'bad'
}) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={cn(
          'mt-1 text-lg font-semibold tabular-nums',
          tone === 'ok' && 'text-emerald-600 dark:text-emerald-500',
          tone === 'bad' && 'text-destructive',
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[10px] text-muted-foreground">{hint}</div>}
    </div>
  )
}
