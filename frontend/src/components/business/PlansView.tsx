import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Circle, MinusCircle, Star } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { getPlan, listPlans } from '@/api/client'
import { ResourcePage } from '@/components/resources/ResourcePage'
import {
  TASK_STATUS_LABELS,
  type Plan,
  type PlanSummary,
  type PlanTask,
} from '@/types/business'
import { cn } from '@/lib/utils'

function TaskStatusIcon({ status }: { status: string }) {
  if (status === 'success') return <CheckCircle2 className="h-4 w-4 text-green-600" />
  if (status === 'skipped') return <MinusCircle className="h-4 w-4 text-muted-foreground" />
  return <Circle className="h-4 w-4 text-muted-foreground" />
}

function groupByStage(tasks: PlanTask[]): Map<number, PlanTask[]> {
  const m = new Map<number, PlanTask[]>()
  for (const t of tasks) {
    const arr = m.get(t.stage_idx) ?? []
    arr.push(t)
    m.set(t.stage_idx, arr)
  }
  return m
}

export function PlansView() {
  const [plans, setPlans] = useState<PlanSummary[]>([])
  const [selected, setSelected] = useState<Plan | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 用函数式 setSelectedId 避免把 selectedId 放进 deps —— 否则每次切 plan 都会
  // 让 refreshList 引用变 → useEffect 重跑 → 重复拉一遍 list 接口。
  const refreshList = useCallback(async () => {
    setLoadingList(true)
    setError(null)
    try {
      const list = await listPlans()
      setPlans(list)
      setSelectedId((prev) => {
        if (prev !== null) return prev
        const active = list.find((p) => p.is_active) ?? list[0]
        return active?.id ?? null
      })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingList(false)
    }
  }, [])

  useEffect(() => {
    refreshList()
  }, [refreshList])

  useEffect(() => {
    if (selectedId === null) {
      setSelected(null)
      return
    }
    let cancelled = false
    ;(async () => {
      setLoadingDetail(true)
      try {
        const p = await getPlan(selectedId)
        if (!cancelled) setSelected(p)
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      } finally {
        if (!cancelled) setLoadingDetail(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const stages = useMemo(
    () => (selected ? groupByStage(selected.tasks) : new Map()),
    [selected],
  )
  const sortedStageIdxs = useMemo(
    () => Array.from(stages.keys()).sort((a, b) => a - b),
    [stages],
  )

  return (
    <ResourcePage
      title="学习计划"
      subtitle="跨 session 持久化；在 chat 里让 LLM 调 create_study_plan / update_study_progress 工具维护"
      toolbar={
        <Button
          onClick={refreshList}
          size="sm"
          variant="outline"
          disabled={loadingList}
        >
          刷新
        </Button>
      }
    >
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}
      {plans.length === 0 && !loadingList && (
        <p className="text-sm text-muted-foreground">
          暂无学习计划。去 chat 让 LLM 帮你拟一个："做一份 8 周的 ML 学习计划"
        </p>
      )}
      {plans.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[260px_minmax(0,1fr)]">
          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border px-3 py-2 text-sm font-medium">
              全部计划 ({plans.length})
            </div>
            <ul className="divide-y divide-border">
              {plans.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => setSelectedId(p.id)}
                    className={cn(
                      'flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-accent/60',
                      selectedId === p.id && 'bg-accent text-accent-foreground',
                    )}
                  >
                    {p.is_active && (
                      <Star className="mt-0.5 h-4 w-4 shrink-0 text-yellow-600" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium" title={p.goal}>
                        {p.goal}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        {p.weeks ? `${p.weeks} 周 · ` : ''}
                        {p.done_count}/{p.task_count} 任务 · {p.status}
                      </div>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="min-w-0 rounded-lg border border-border bg-card">
            {loadingDetail && !selected && (
              <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
            )}
            {selected && (
              <>
                <div className="border-b border-border px-3 py-2">
                  <div className="flex items-center gap-2">
                    <h2 className="font-semibold">{selected.goal}</h2>
                    {selected.is_active && (
                      <span className="rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] text-yellow-900 dark:bg-yellow-900 dark:text-yellow-100">
                        当前 active
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {selected.weeks ? `${selected.weeks} 周 · ` : ''}
                    {selected.status} · 创建于 {selected.created_at}
                  </div>
                </div>
                <div className="divide-y divide-border">
                  {sortedStageIdxs.length === 0 ? (
                    <p className="px-3 py-3 text-sm text-muted-foreground">
                      暂无任务
                    </p>
                  ) : (
                    sortedStageIdxs.map((idx) => (
                      <div key={idx} className="px-3 py-2">
                        <div className="text-xs font-medium text-muted-foreground">
                          Stage {idx}
                        </div>
                        <ul className="mt-1 space-y-1">
                          {(stages.get(idx) ?? []).map((t: PlanTask) => (
                            <li key={t.id} className="flex items-start gap-2 text-sm">
                              <TaskStatusIcon status={t.status} />
                              <div className="min-w-0 flex-1">
                                <div
                                  className={
                                    t.status === 'success'
                                      ? 'text-muted-foreground line-through'
                                      : ''
                                  }
                                >
                                  {t.title}
                                </div>
                                {t.note && (
                                  <div className="text-[10px] text-muted-foreground">
                                    备注：{t.note}
                                  </div>
                                )}
                              </div>
                              <span className="shrink-0 text-[10px] text-muted-foreground">
                                {TASK_STATUS_LABELS[t.status] ?? t.status}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </ResourcePage>
  )
}
