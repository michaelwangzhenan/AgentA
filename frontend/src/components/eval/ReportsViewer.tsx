import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/button'
import { MarkdownPreview } from '@/components/ui/markdown-preview'
import { getReportContent, getReports } from '@/api/client'
import type { ReportItem } from '@/types/eval'

// 文件名前缀 → 中文友好类别名。未登记的前缀直接用前缀本身兜底。
const CATEGORY_LABELS: Record<string, string> = {
  'run-all': '综合评估',
  'security-adversarial': '安全红队',
  'perf-memory': '性能·记忆召回',
  'perf-session': '性能·会话',
  mcp: 'MCP 工具',
  'plan-eval': '学习计划',
  'quiz-eval': '测验生成',
  recall: 'RAG 召回',
  'harness-eval': 'Harness',
  'skill-recall': 'Skill 召回',
  'srs-eval': 'SRS 复习',
  __rag: 'RAG 检索实验',
}

// 类别展示顺序：按功能分块 RAG → Agent 相关 → 性能 → 业务。未登记的排最后。
const CATEGORY_ORDER = [
  // RAG
  'recall',
  '__rag',
  // Agent 相关
  'run-all',
  'security-adversarial',
  'mcp',
  'harness-eval',
  'skill-recall',
  // 性能
  'perf-memory',
  'perf-session',
  // 业务
  'plan-eval',
  'quiz-eval',
  'srs-eval',
]

// agent_eval 文件名尾部的 -YYYYMMDD-HHMMSS 时间戳
const STAMP_RE = /-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/

type ParsedReport = {
  report: ReportItem
  categoryKey: string
  categoryLabel: string
}

function parseReport(report: ReportItem): ParsedReport {
  const [prefix, file = ''] = report.name.split('/')
  const base = file.replace(/\.md$/, '')

  // rag_eval 报告名无时间戳（调参实验名），整体归一类
  if (prefix === 'rag_eval') {
    return { report, categoryKey: '__rag', categoryLabel: CATEGORY_LABELS.__rag }
  }

  // agent_eval：剥掉尾部时间戳得到类型前缀作为分组键
  const m = base.match(STAMP_RE)
  const typeKey = m ? base.slice(0, m.index) : base
  const label = CATEGORY_LABELS[typeKey] ?? typeKey
  return { report, categoryKey: typeKey, categoryLabel: label }
}

type Group = {
  key: string
  label: string
  items: ParsedReport[]
  latest: number // 组内最新修改时间，用于组排序
}

function groupReports(reports: ReportItem[]): Group[] {
  const map = new Map<string, Group>()
  for (const report of reports) {
    const parsed = parseReport(report)
    let g = map.get(parsed.categoryKey)
    if (!g) {
      g = { key: parsed.categoryKey, label: parsed.categoryLabel, items: [], latest: 0 }
      map.set(parsed.categoryKey, g)
    }
    g.items.push(parsed)
    g.latest = Math.max(g.latest, report.modified_at)
  }
  const groups = [...map.values()]
  for (const g of groups) g.items.sort((a, b) => b.report.modified_at - a.report.modified_at)
  // 组按功能顺序排（CATEGORY_ORDER）；未登记类别按最新时间倒序兜底排最后
  groups.sort((a, b) => {
    const ia = CATEGORY_ORDER.indexOf(a.key)
    const ib = CATEGORY_ORDER.indexOf(b.key)
    if (ia !== -1 && ib !== -1) return ia - ib
    if (ia !== -1) return -1
    if (ib !== -1) return 1
    return b.latest - a.latest
  })
  return groups
}

export function ReportsViewer() {
  const [reports, setReports] = useState<ReportItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState('')
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setReports((await getReports()).reports)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const groups = useMemo(() => groupReports(reports), [reports])

  const allCollapsed = groups.length > 0 && groups.every((g) => collapsed[g.key])
  const toggleAll = () => {
    if (allCollapsed) setCollapsed({})
    else setCollapsed(Object.fromEntries(groups.map((g) => [g.key, true])))
  }

  const open = async (name: string) => {
    setSelected(name)
    try {
      setContent((await getReportContent(name)).content)
    } catch (e) {
      toast.error((e as Error).message)
      setContent('')
    }
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
      <div className="flex min-h-0 flex-col gap-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">报告列表</h2>
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="sm" onClick={toggleAll} disabled={groups.length === 0}>
              {allCollapsed ? '全部展开' : '全部折叠'}
            </Button>
            <Button variant="outline" size="sm" onClick={refresh}>
              刷新
            </Button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-border">
          {groups.length > 0 ? (
            <ul>
              {groups.map((g) => {
                const isCollapsed = collapsed[g.key]
                return (
                  <li key={g.key} className="border-b border-border last:border-0">
                    <button
                      onClick={() => setCollapsed((s) => ({ ...s, [g.key]: !s[g.key] }))}
                      className="flex w-full items-center gap-1 bg-muted/40 px-2 py-1.5 text-left text-xs font-medium hover:bg-muted/70"
                    >
                      {isCollapsed ? (
                        <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                      )}
                      <span className="truncate">{g.label}</span>
                      <span className="ml-auto text-[10px] text-muted-foreground">{g.items.length}</span>
                    </button>
                    {!isCollapsed && (
                      <ul>
                        {g.items.map((p) => (
                          <li key={p.report.name}>
                            <button
                              onClick={() => open(p.report.name)}
                              className={cn(
                                'w-full border-t border-border/60 py-1.5 pl-7 pr-3 text-left text-xs hover:bg-accent/40',
                                selected === p.report.name && 'bg-accent',
                              )}
                            >
                              <div className="truncate font-medium" title={p.report.name}>
                                {p.report.name.split('/').pop()}
                              </div>
                              <div className="text-[10px] text-muted-foreground">
                                {new Date(p.report.modified_at * 1000).toLocaleString()}
                              </div>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              {loading ? '加载中…' : '暂无报告（跑 python -m tools.agent_eval.run_all 后生成）'}
            </p>
          )}
        </div>
      </div>

      <div className="min-h-0 overflow-y-auto rounded-lg border border-border p-4">
        {selected ? (
          <MarkdownPreview source={content} />
        ) : (
          <p className="text-sm text-muted-foreground">从左侧选择一份报告查看</p>
        )}
      </div>
    </div>
  )
}
