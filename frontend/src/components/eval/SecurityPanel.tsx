import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, ArrowDown, ArrowUp } from 'lucide-react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/button'
import {
  getSecurityRuntimeEvents,
  getSecurityRuntimeSummary,
  getSecuritySummary,
  getSecurityTrend,
  listUsers,
} from '@/api/client'
import type {
  SecurityEventPage,
  SecurityRuntimeSummary,
  SecuritySummary,
  SecurityTrend,
} from '@/types/eval'
import type { UserInfo } from '@/types/auth'

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const

// 与后端 config.DEFAULT_USER_ID 对齐：CLI / 关认证时的兜底身份，users 表里通常无对应行
const DEFAULT_USER_ID = 1

// user_id → "4 (Admin)"；兜底身份标 "(默认/CLI)"；未知则只显示 id（与数据库页一致）
function userLabel(userId: number, userMap: Record<string, string>): string {
  const name = userMap[String(userId)]
  if (name) return `${userId} (${name})`
  if (userId === DEFAULT_USER_ID) return `${userId} (默认/CLI)`
  return String(userId)
}

// 'YYYY-MM-DD' → epoch 秒（本地时区）；空串返回 undefined
function dateToEpoch(s: string, endOfDay: boolean): number | undefined {
  if (!s) return undefined
  const d = new Date(`${s}T${endOfDay ? '23:59:59' : '00:00:00'}`)
  return Number.isNaN(d.getTime()) ? undefined : Math.floor(d.getTime() / 1000)
}

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
  const [users, setUsers] = useState<UserInfo[]>([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      // 并行取拦截汇总（全局近 30 天）+ 用户列表（建 user_id → 用户名 映射，admin 接口）
      const [summary, userList] = await Promise.all([
        getSecurityRuntimeSummary(),
        listUsers().catch(() => [] as UserInfo[]),
      ])
      setData(summary)
      setUsers(userList)
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
        <RuntimeEventsTable users={users} />
      </section>
    </div>
  )
}

// 实时拦截事件表：服务端筛选（时间 / 类型 / 用户）+ 表头排序 + 分页（参考数据库页）。
function RuntimeEventsTable({ users }: { users: UserInfo[] }) {
  const userMap = useMemo(() => {
    const m: Record<string, string> = {}
    for (const u of users) m[String(u.id)] = u.username
    return m
  }, [users])

  const [eventType, setEventType] = useState('')
  const [userId, setUserId] = useState('') // '' = 全部
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  // 已提交的时间范围（点「查询」才生效；类型 / 用户即时生效）
  const [range, setRange] = useState<{ tsFrom?: number; tsTo?: number }>({})
  const [sortBy, setSortBy] = useState('created_at')
  const [desc, setDesc] = useState(true)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZE_OPTIONS[0])

  const [page, setPage] = useState<SecurityEventPage | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setPage(
        await getSecurityRuntimeEvents({
          tsFrom: range.tsFrom,
          tsTo: range.tsTo,
          eventType: eventType || undefined,
          userId: userId === '' ? undefined : Number(userId),
          sortBy,
          desc,
          limit: pageSize,
          offset,
        }),
      )
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [range, eventType, userId, sortBy, desc, pageSize, offset])

  useEffect(() => {
    void load()
  }, [load])

  // 筛选 / 排序 / 每页变化时回到第一页
  const resetTo0 = () => setOffset(0)

  const applyRange = () => {
    setRange({ tsFrom: dateToEpoch(from, false), tsTo: dateToEpoch(to, true) })
    resetTo0()
  }
  const clearFilters = () => {
    setEventType('')
    setUserId('')
    setFrom('')
    setTo('')
    setRange({})
    resetTo0()
  }

  // 点表头：同列切升降，换列默认降序
  const toggleSort = (col: string) => {
    if (sortBy === col) {
      setDesc((d) => !d)
    } else {
      setSortBy(col)
      setDesc(true)
    }
    resetTo0()
  }

  const inputCls = 'rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground'
  const items = page?.items ?? []
  const total = page?.total ?? 0

  return (
    <div className="space-y-3">
      {/* 工具条：类型 / 用户 即时生效；时间范围点查询生效 */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1 text-muted-foreground">
          类型
          <select
            value={eventType}
            onChange={(e) => {
              setEventType(e.target.value)
              resetTo0()
            }}
            className={inputCls}
          >
            <option value="">全部</option>
            <option value="scrub">注入清洗</option>
            <option value="tool">越权调用</option>
            <option value="ssrf">SSRF</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          用户
          <select
            value={userId}
            onChange={(e) => {
              setUserId(e.target.value)
              resetTo0()
            }}
            className={inputCls}
          >
            <option value="">全部</option>
            <option value={String(DEFAULT_USER_ID)}>{DEFAULT_USER_ID} (默认/CLI)</option>
            {users
              .filter((u) => u.id !== DEFAULT_USER_ID)
              .map((u) => (
                <option key={u.id} value={String(u.id)}>
                  {u.id} ({u.username})
                </option>
              ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          时间
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} />
          –
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={inputCls} />
        </label>
        <button type="button" onClick={applyRange} className="rounded-md border border-border px-2 py-1 hover:bg-muted/50">
          查询
        </button>
        <button
          type="button"
          onClick={clearFilters}
          className="rounded-md border border-border px-2 py-1 text-muted-foreground hover:bg-muted/50"
        >
          清除
        </button>
      </div>

      {items.length > 0 ? (
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <SortableTh label="时间" col="created_at" sortBy={sortBy} desc={desc} onSort={toggleSort} />
                <SortableTh label="类型" col="event_type" sortBy={sortBy} desc={desc} onSort={toggleSort} />
                <SortableTh label="用户" col="user_id" sortBy={sortBy} desc={desc} onSort={toggleSort} />
                <th className="px-3 py-2 font-medium">详情</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e, i) => (
                <tr key={`${e.created_at}-${i}`} className="border-b border-border last:border-0">
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                    {new Date(e.created_at * 1000).toLocaleString()}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {EVENT_TYPE_LABELS[e.event_type] ?? e.event_type}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                    {userLabel(e.user_id, userMap)}
                  </td>
                  <td className="break-all px-3 py-2 text-muted-foreground">{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          {loading ? '加载中…' : '无匹配的拦截记录（对话中触发防御后会自动记录到这里）。'}
        </p>
      )}

      <EventsPager total={total} offset={offset} pageSize={pageSize} onOffset={setOffset} onPageSize={(n) => { setPageSize(n); resetTo0() }} />
    </div>
  )
}

// 可排序表头单元格：点击切换升 / 降序，当前排序列显示箭头
function SortableTh({
  label,
  col,
  sortBy,
  desc,
  onSort,
}: {
  label: string
  col: string
  sortBy: string
  desc: boolean
  onSort: (col: string) => void
}) {
  const active = sortBy === col
  return (
    <th className="px-3 py-2 font-medium">
      <button
        type="button"
        onClick={() => onSort(col)}
        className={cn('flex items-center gap-1 hover:text-foreground', active && 'text-foreground')}
      >
        {label}
        {active &&
          (desc ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}
      </button>
    </th>
  )
}

// 分页条（参考数据库页）：上一页 / 区间 / 下一页 / 跳转 / 每页条数
function EventsPager({
  total,
  offset,
  pageSize,
  onOffset,
  onPageSize,
}: {
  total: number
  offset: number
  pageSize: number
  onOffset: (offset: number) => void
  onPageSize: (n: number) => void
}) {
  const [jump, setJump] = useState('')
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const pageNo = Math.floor(offset / pageSize) + 1
  const fromN = total === 0 ? 0 : offset + 1
  const toN = Math.min(offset + pageSize, total)

  const go = () => {
    const n = parseInt(jump, 10)
    if (!Number.isNaN(n)) onOffset((Math.min(Math.max(1, n), totalPages) - 1) * pageSize)
    setJump('')
  }

  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
      <button
        type="button"
        onClick={() => onOffset(Math.max(0, offset - pageSize))}
        disabled={offset <= 0}
        className="rounded-md border border-border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
      >
        上一页
      </button>
      <span>{fromN}–{toN} / {total}</span>
      <button
        type="button"
        onClick={() => onOffset(offset + pageSize)}
        disabled={toN >= total}
        className="rounded-md border border-border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
      >
        下一页
      </button>
      <span>第 {pageNo}/{totalPages} 页</span>
      <span className="flex items-center gap-1">
        跳至
        <input
          value={jump}
          inputMode="numeric"
          onChange={(e) => setJump(e.target.value.replace(/[^0-9]/g, ''))}
          onKeyDown={(e) => e.key === 'Enter' && go()}
          placeholder={String(pageNo)}
          className="w-14 rounded-md border border-border bg-background px-1.5 py-1 text-center text-foreground"
        />
        <button type="button" onClick={go} className="rounded-md border border-border px-2 py-1 hover:bg-muted/50">
          跳转
        </button>
      </span>
      <label className="ml-auto flex items-center gap-1.5">
        每页
        <select
          value={pageSize}
          onChange={(e) => onPageSize(Number(e.target.value))}
          className="rounded-md border border-border bg-background px-1.5 py-1 text-foreground"
        >
          {PAGE_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        条
      </label>
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
