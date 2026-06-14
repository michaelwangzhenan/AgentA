import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Play,
  RefreshCw,
  Square,
} from 'lucide-react'

import {
  cancelEval,
  getEvalRunStatus,
  getEvalSummary,
  getModels,
  getReportContent,
  getReports,
  getRoutingPool,
  runEval,
} from '@/api/client'
import type { EvalRunStatus, EvalSummary, ReportItem } from '@/types/eval'
import type { RoutingModel } from '@/types/routing'
import { Button } from '@/components/ui/button'
import { MarkdownPreview } from '@/components/ui/markdown-preview'
import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'

// 一个 eval 子页的 UI 配置（选项 / 是否用 LLM / 报告前缀）
export type EvalOption =
  | { kind: 'checkbox'; key: 'no_llm'; label: string }
  | { kind: 'select'; key: 'kind'; label: string; choices: { value: string; label: string }[] }

// 说明正文：单段（string）或多行（string[]，逐行展示，便于步骤 / 指标分行）
export type IntroBody = string | string[]

// 子页顶部「说明」文案（howRead 不填则用通用默认；params 可省）
export type EvalIntro = {
  purpose: IntroBody // 目的
  how: IntroBody // 如何评估（操作步骤，建议每步一行）
  params?: IntroBody // 参数说明（LLM 以外的选项，如类别 / target / ci）
  principle: IntroBody // 工作原理
  metrics: IntroBody // 指标解读（建议每指标一行）
  cost: IntroBody // 耗时·成本
  howRead?: IntroBody // 如何看结果（通用话术，可覆盖）
  dataset: IntroBody // 数据来源
}

// 可调判定阈值（0~1 浮点），跑评估时作为参数传入、不持久化
export type EvalThreshold = {
  key: string // 后端 thresholds 字典里的键，如 recall / fpr
  label: string
  default: number
  min?: number
  max?: number
  step?: number
}

export type EvalTaskConfig = {
  key: string // 后端 task key
  label: string
  usesLlm: boolean // 显示测试模型下拉
  noneOption?: boolean // 模型下拉是否含「None（不调用 LLM）」= 只跑不用 LLM 的 case
  reportMatch: string // 历史报告按文件名包含此串过滤
  options: EvalOption[]
  thresholds?: EvalThreshold[]
  intro: EvalIntro
}

// 模型下拉里「不调用 LLM」的哨兵值
const NONE_MODEL = '__none__'

// 所有 eval 通用的"如何看结果"话术
const DEFAULT_HOW_READ =
  '上方摘要卡片给"是否过线"的结论（指标 + 阈值 + 通过/未过）；下方历史报告从新到旧，点某行即把卡片切到那次快照（高亮跟随），点「查看报告」看明细，用于定位哪里没达标、怎么改。'

function fmtTime(epoch: number): string {
  if (!epoch) return '-'
  return new Date(epoch * 1000).toLocaleString()
}

export function EvalRunner({ task }: { task: EvalTaskConfig }) {
  const [status, setStatus] = useState<EvalRunStatus | null>(null)
  const [summary, setSummary] = useState<EvalSummary | null>(null)
  const [reports, setReports] = useState<ReportItem[]>([])
  const [selectedReport, setSelectedReport] = useState<string | null>(null)
  const [models, setModels] = useState<RoutingModel[]>([])
  const [model, setModel] = useState('')
  const [activeModel, setActiveModel] = useState('')
  const [opts, setOpts] = useState<Record<string, string | boolean>>({})
  const [thresholds, setThresholds] = useState<Record<string, number>>({})
  const [viewing, setViewing] = useState<{ name: string; content: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const prevState = useRef<string>('')

  // 拉历史报告；selectNewest=true（如跑完）强制选最新，否则保留当前选中（失效则取最新）
  const refreshReports = useCallback(
    async (selectNewest = false) => {
      try {
        const all = await getReports()
        const mine = all.reports.filter((r) => r.name.includes(task.reportMatch))
        setReports(mine)
        setSelectedReport((prev) =>
          selectNewest || !(prev && mine.some((r) => r.name === prev))
            ? mine[0]?.name ?? null
            : prev,
        )
      } catch {
        // 忽略
      }
    },
    [task.reportMatch],
  )

  // 选中报告（或最新）变化 → 拉它的摘要快照（null = 最新一次）
  useEffect(() => {
    getEvalSummary(task.key, selectedReport ?? undefined)
      .then(setSummary)
      .catch(() => {})
  }, [task.key, selectedReport])

  // 切换 eval：重置选项 / 选中、拉报告 /（可用）模型（摘要由上面 effect 跟随 selectedReport）
  useEffect(() => {
    setOpts({})
    setThresholds(
      Object.fromEntries((task.thresholds ?? []).map((t) => [t.key, t.default])),
    )
    setViewing(null)
    setSelectedReport(null)
    void refreshReports()
    if (task.usesLlm) {
      // 候选 = 路由池里可用（已配 key）的模型；默认选中 = 系统当前 ACTIVE_MODEL
      Promise.all([getRoutingPool(), getModels()])
        .then(([pool, m]) => {
          const avail = pool.models.filter((x) => x.available)
          setModels(avail)
          setActiveModel(m.active)
          const hasActive = avail.some((x) => x.model_id === m.active)
          setModel(
            hasActive ? m.active : task.noneOption ? NONE_MODEL : avail[0]?.model_id ?? '',
          )
        })
        .catch(() => {
          setModels([])
          setModel(task.noneOption ? NONE_MODEL : '')
        })
    }
  }, [task.key, task.usesLlm, task.noneOption, task.thresholds, refreshReports])

  // 轮询全局任务状态（单任务锁）；跑完刷新摘要 / 报告
  useEffect(() => {
    let timer: number | undefined
    const tick = async () => {
      try {
        const st = await getEvalRunStatus()
        setStatus(st)
        if (prevState.current === 'running' && st.state !== 'running') {
          // 跑完：刷新报告并选中最新（卡片随 selectedReport 自动更新）
          void refreshReports(true)
        }
        prevState.current = st.state
      } catch {
        // 忽略单次轮询失败
      }
    }
    void tick()
    timer = window.setInterval(tick, 1500)
    return () => window.clearInterval(timer)
  }, [refreshReports])

  const runningThis = status?.state === 'running' && status.task === task.key
  const runningOther = status?.state === 'running' && status.task !== task.key

  const start = async () => {
    setBusy(true)
    try {
      const isNone = task.usesLlm && model === NONE_MODEL
      const st = await runEval({
        task: task.key,
        model: isNone ? undefined : task.usesLlm ? model || undefined : undefined,
        no_llm: isNone || opts.no_llm === true,
        kind: typeof opts.kind === 'string' && opts.kind ? opts.kind : undefined,
        thresholds: task.thresholds && task.thresholds.length > 0 ? thresholds : undefined,
      })
      setStatus(st)
      prevState.current = st.state
      toast.success('已开始评估')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    try {
      setStatus(await cancelEval())
      toast.info('已取消')
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const openReport = async (name: string) => {
    try {
      const r = await getReportContent(name)
      setViewing({ name, content: r.content })
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  // 报告正文视图
  if (viewing) {
    return (
      <div className="space-y-3">
        <button
          type="button"
          onClick={() => setViewing(null)}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          返回 {task.label}
        </button>
        <div className="rounded-lg border border-border p-4">
          <MarkdownPreview source={viewing.content} />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <IntroCard task={task} />

      {/* 运行控制区 */}
      <div className="space-y-3 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center gap-3">
          {task.usesLlm && (
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              测试模型
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={runningThis}
                className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
              >
                {task.noneOption && (
                  <option value={NONE_MODEL}>None（不调用 LLM）</option>
                )}
                {models.length === 0 && !task.noneOption && (
                  <option value="">（无可用模型）</option>
                )}
                {models.map((m) => (
                  <option key={m.model_id} value={m.model_id}>
                    {m.label}
                    {m.model_id === activeModel ? '（当前）' : ''}
                  </option>
                ))}
              </select>
            </label>
          )}

          {task.options.map((opt) =>
            opt.kind === 'checkbox' ? (
              <label key={opt.key} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={opts[opt.key] === true}
                  disabled={runningThis}
                  onChange={(e) => setOpts((p) => ({ ...p, [opt.key]: e.target.checked }))}
                  className="h-4 w-4 accent-primary"
                />
                {opt.label}
              </label>
            ) : (
              <label key={opt.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                {opt.label}
                <select
                  value={String(opts[opt.key] ?? '')}
                  disabled={runningThis}
                  onChange={(e) => setOpts((p) => ({ ...p, [opt.key]: e.target.value }))}
                  className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                >
                  {opt.choices.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
            ),
          )}

          {(task.thresholds ?? []).map((t) => (
            <label key={t.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              {t.label}
              <input
                type="number"
                value={thresholds[t.key] ?? t.default}
                min={t.min ?? 0}
                max={t.max ?? 1}
                step={t.step ?? 0.01}
                disabled={runningThis}
                onChange={(e) =>
                  setThresholds((p) => ({ ...p, [t.key]: Number(e.target.value) }))
                }
                className="w-20 rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
              />
            </label>
          ))}
        </div>

        {/* 操作按钮单独成行，右对齐（不跟控件挤同一行） */}
        <div className="flex items-center justify-end gap-2">
          {runningThis ? (
            <Button size="sm" variant="destructive" className="h-8 gap-1" onClick={stop}>
              <Square className="h-3.5 w-3.5" />
              取消
            </Button>
          ) : (
            <Button
              size="sm"
              className="h-8 gap-1"
              disabled={busy || runningOther}
              onClick={start}
            >
              <Play className="h-3.5 w-3.5" />
              开始评估
            </Button>
          )}
        </div>

        {runningOther && (
          <p className="text-xs text-amber-600 dark:text-amber-500">
            另一个评估（{status?.task}）正在运行，请等它结束或取消。
          </p>
        )}

        {runningThis && (
          <div className="space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
              运行中…
            </div>
            {status?.tail && (
              <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 text-[11px] leading-relaxed">
                {status.tail}
              </pre>
            )}
          </div>
        )}
      </div>

      {/* 摘要卡片 */}
      <SummaryCard summary={summary} selectedReport={selectedReport} />

      {/* 历史报告 */}
      <section className="rounded-lg border border-border">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <span className="text-sm font-medium">历史报告（{reports.length}）</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs text-muted-foreground"
            onClick={() => refreshReports()}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            刷新
          </Button>
        </div>
        {reports.length === 0 ? (
          <p className="px-3 py-4 text-sm text-muted-foreground">暂无报告，跑一次评估后生成。</p>
        ) : (
          <ul className="max-h-64 overflow-auto">
            {reports.map((r) => {
              // 点行 = 选中看卡片；高亮 = 当前卡片对应的那份
              const current = r.name === selectedReport
              return (
                <li
                  key={r.name}
                  className={cn(
                    'flex items-center justify-between gap-2 border-b border-border/50 px-3 py-1.5 text-sm last:border-0',
                    current ? 'bg-primary/5' : 'hover:bg-muted/40',
                  )}
                >
                  <button
                    type="button"
                    onClick={() => setSelectedReport(r.name)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    title="点击：摘要卡片切到这份"
                  >
                    <span className="truncate font-mono text-xs">
                      {r.name.split('/').pop()}
                    </span>
                    {current && (
                      <span className="shrink-0 rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                        当前卡片
                      </span>
                    )}
                  </button>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                      {fmtTime(r.modified_at)}
                    </span>
                    <button
                      type="button"
                      onClick={() => openReport(r.name)}
                      className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                    >
                      查看报告
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}

function IntroCard({ task }: { task: EvalTaskConfig }) {
  const storeKey = `agenta:eval:intro:${task.key}`
  // 首次（无记录）默认展开；之后按 eval key 记住折叠状态
  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(storeKey) !== 'closed'
    } catch {
      return true
    }
  })
  const toggle = () => {
    setOpen((prev) => {
      const next = !prev
      try {
        localStorage.setItem(storeKey, next ? 'open' : 'closed')
      } catch {
        // 隐私模式忽略
      }
      return next
    })
  }

  const intro = task.intro
  const sections: { title: string; body: IntroBody }[] = [
    { title: '目的', body: intro.purpose },
    { title: '如何评估', body: intro.how },
    ...(intro.params ? [{ title: '参数说明', body: intro.params }] : []),
    { title: '工作原理', body: intro.principle },
    { title: '指标解读', body: intro.metrics },
    { title: '耗时·成本', body: intro.cost },
    { title: '如何看结果', body: intro.howRead ?? DEFAULT_HOW_READ },
    { title: '数据来源', body: intro.dataset },
  ]

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-sm font-semibold hover:bg-muted/40"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
        {task.label}
      </button>
      {open && (
        <dl className="space-y-2 border-t border-border px-4 py-3 text-sm">
          {sections.map((s) => (
            <div key={s.title} className="grid grid-cols-[5rem_1fr] gap-2">
              <dt className="shrink-0 font-medium text-muted-foreground">{s.title}</dt>
              <dd className="space-y-0.5 leading-relaxed text-foreground">
                {(Array.isArray(s.body) ? s.body : [s.body]).map((line, i) => (
                  <div key={i}>{line}</div>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

function SummaryCard({
  summary,
  selectedReport,
}: {
  summary: EvalSummary | null
  selectedReport: string | null
}) {
  if (!summary || !summary.available) {
    // 选中了某份报告但它没有结构化摘要（多为早期报告，缺配对 JSON）→ 区别于"从未跑过"
    const msg = selectedReport
      ? '这份报告没有结构化摘要（早期报告），点右侧「查看报告」看正文。'
      : '暂无评估结果，点「开始评估」生成。'
    return (
      <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
        {msg}
      </div>
    )
  }
  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          最近评估：{summary.timestamp || '—'}
          {summary.git ? ` · ${summary.git}` : ''}
          {summary.partial && (
            <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600 dark:text-amber-500">
              部分跑
            </span>
          )}
        </span>
        {summary.passed !== null && (
          <span
            className={cn(
              'rounded px-2 py-0.5 text-xs font-medium',
              summary.passed
                ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-500'
                : 'bg-destructive/15 text-destructive',
            )}
          >
            {summary.passed ? '通过' : '未过'}
          </span>
        )}
      </div>
      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
              <th className="px-3 py-2 font-medium">指标</th>
              <th className="px-3 py-2 text-right font-medium">实测</th>
              <th className="px-3 py-2 text-right font-medium">阈值</th>
              <th className="px-3 py-2 text-center font-medium">判定</th>
            </tr>
          </thead>
          <tbody>
            {summary.metrics.map((m) => (
              <tr key={m.label} className="border-b border-border last:border-0">
                <td className="px-3 py-2">{m.label}</td>
                <td className="px-3 py-2 text-right tabular-nums">{m.value}</td>
                <td className="px-3 py-2 text-right text-muted-foreground">{m.threshold || '—'}</td>
                <td className="px-3 py-2 text-center">
                  {m.ok === null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : m.ok ? (
                    <span className="text-emerald-600 dark:text-emerald-500">✓</span>
                  ) : (
                    <span className="text-destructive">✗</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
