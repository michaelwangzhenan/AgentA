import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Circle, MinusCircle, Plus, Star } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  abandonPlan,
  activatePlan,
  createPlan,
  getPlan,
  listPlans,
  updatePlanTask,
} from '@/api/client'
import {
  TASK_STATUS_LABELS,
  type Plan,
  type PlanSummary,
  type PlanTask,
} from '@/types/business'
import { toast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { useUrlState } from '@/routes/useUrlState'

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

// 点任务在 待办 → 已完成 → 已跳过 → 待办 间循环
const NEXT_STATUS: Record<string, string> = {
  pending: 'success',
  success: 'skipped',
  skipped: 'pending',
}

export function PlansView() {
  const url = useUrlState()
  const planParam = url.get('plan')
  const selectedId = planParam ? Number(planParam) : null
  const setSelectedId = (id: number | null) =>
    url.patch({ plan: id != null && !Number.isNaN(id) ? id : null })

  const [plans, setPlans] = useState<PlanSummary[]>([])
  const [selected, setSelected] = useState<Plan | null>(null)
  const [loadingList, setLoadingList] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [addOpen, setAddOpen] = useState(false)
  const [addGoal, setAddGoal] = useState('')
  const [addWeeks, setAddWeeks] = useState('')
  const [addTasks, setAddTasks] = useState('')
  const [adding, setAdding] = useState(false)
  const [abandonTarget, setAbandonTarget] = useState<PlanSummary | null>(null)
  const [abandonBusy, setAbandonBusy] = useState(false)

  const refreshList = useCallback(async (preferId?: number) => {
    setLoadingList(true)
    setError(null)
    try {
      const list = await listPlans()
      setPlans(list)
      let nextId: number | null = selectedId
      if (preferId !== undefined) nextId = preferId
      else if (selectedId !== null && list.some((p) => p.id === selectedId)) nextId = selectedId
      else {
        const active = list.find((p) => p.is_active) ?? list[0]
        nextId = active?.id ?? null
      }
      if (nextId !== selectedId) setSelectedId(nextId)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingList(false)
    }
  }, [selectedId])

  useEffect(() => {
    refreshList()
  }, [refreshList])

  const reloadDetail = useCallback(async (id: number) => {
    try {
      setSelected(await getPlan(id))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    if (selectedId === null) {
      setSelected(null)
      return
    }
    reloadDetail(selectedId)
  }, [selectedId, reloadDetail])

  const stages = useMemo(
    () => (selected ? groupByStage(selected.tasks) : new Map<number, PlanTask[]>()),
    [selected],
  )
  const sortedStageIdxs = useMemo(
    () => Array.from(stages.keys()).sort((a, b) => a - b),
    [stages],
  )

  const resetAddForm = () => {
    setAddGoal('')
    setAddWeeks('')
    setAddTasks('')
  }

  const submitAdd = async () => {
    const goal = addGoal.trim()
    if (!goal) return
    const weeks = Number.parseInt(addWeeks, 10)
    const lines = addTasks
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
    const tasks = lines.map((title, i) => ({
      stage_idx: 1,
      order_idx: i + 1,
      title,
    }))
    setAdding(true)
    try {
      const plan = await createPlan({
        goal,
        weeks: Number.isFinite(weeks) && weeks > 0 ? weeks : 0,
        tasks,
      })
      toast.success('已创建学习计划')
      setAddOpen(false)
      resetAddForm()
      await refreshList(plan.id)
    } catch (e) {
      toast.error(`创建失败：${(e as Error).message}`)
    } finally {
      setAdding(false)
    }
  }

  const cycleTask = async (task: PlanTask) => {
    if (!selected) return
    const next = NEXT_STATUS[task.status] ?? 'success'
    try {
      const plan = await updatePlanTask(selected.id, task.id, next)
      setSelected(plan)
      // 列表里的完成计数也要刷新
      refreshList(selected.id)
    } catch (e) {
      toast.error(`更新失败：${(e as Error).message}`)
    }
  }

  const handleActivate = async (id: number) => {
    try {
      await activatePlan(id)
      toast.success('已设为当前计划')
      await refreshList(id)
    } catch (e) {
      toast.error(`操作失败：${(e as Error).message}`)
    }
  }

  const confirmAbandon = async () => {
    if (!abandonTarget) return
    setAbandonBusy(true)
    try {
      await abandonPlan(abandonTarget.id)
      toast.success('已放弃该计划')
      setAbandonTarget(null)
      await refreshList()
    } catch (e) {
      toast.error(`操作失败：${(e as Error).message}`)
    } finally {
      setAbandonBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          给自己定个目标，拆成阶段任务，逐条勾掉。复杂目标可在聊天里说"帮我做一份 8 周的 ML 学习计划"，让 AI 自动拟好。
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <Button onClick={() => setAddOpen(true)} size="sm">
            <Plus className="mr-1 h-4 w-4" />
            新建计划
          </Button>
          <Button onClick={() => refreshList()} size="sm" variant="outline" disabled={loadingList}>
            刷新
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}

      {plans.length === 0 && !loadingList && (
        <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center">
          <p className="text-sm text-muted-foreground">还没有学习计划</p>
          <Button onClick={() => setAddOpen(true)} size="sm" className="mt-3">
            <Plus className="mr-1 h-4 w-4" />
            新建第一个计划
          </Button>
        </div>
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
            {selected && (
              <>
                <div className="flex items-start justify-between gap-2 border-b border-border px-3 py-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="truncate font-semibold" title={selected.goal}>
                        {selected.goal}
                      </h2>
                      {selected.is_active && (
                        <span className="shrink-0 rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] text-yellow-900 dark:bg-yellow-900 dark:text-yellow-100">
                          当前计划
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      {selected.weeks ? `${selected.weeks} 周 · ` : ''}
                      {selected.status} · 创建于 {selected.created_at}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {!selected.is_active && selected.status === 'active' && (
                      <Button size="sm" variant="outline" onClick={() => handleActivate(selected.id)}>
                        设为当前
                      </Button>
                    )}
                    {selected.status !== 'abandoned' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={() =>
                          setAbandonTarget(plans.find((p) => p.id === selected.id) ?? null)
                        }
                      >
                        放弃
                      </Button>
                    )}
                  </div>
                </div>
                <div className="divide-y divide-border">
                  {sortedStageIdxs.length === 0 ? (
                    <p className="px-3 py-3 text-sm text-muted-foreground">暂无任务</p>
                  ) : (
                    sortedStageIdxs.map((idx) => (
                      <div key={idx} className="px-3 py-2">
                        <div className="text-xs font-medium text-muted-foreground">
                          Stage {idx}
                        </div>
                        <ul className="mt-1 space-y-1">
                          {(stages.get(idx) ?? []).map((t: PlanTask) => (
                            <li key={t.id} className="flex items-start gap-2 text-sm">
                              <button
                                onClick={() => cycleTask(t)}
                                className="mt-0.5 shrink-0 rounded hover:bg-accent"
                                title="点击切换：待办 → 已完成 → 已跳过"
                              >
                                <TaskStatusIcon status={t.status} />
                              </button>
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

      {/* 新建计划 Dialog */}
      <Dialog
        open={addOpen}
        onOpenChange={(o: boolean) => {
          if (adding) return
          setAddOpen(o)
          if (!o) resetAddForm()
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>新建学习计划</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                目标 <span className="text-muted-foreground/70">（这次想学会什么）</span>
              </label>
              <Input
                value={addGoal}
                onChange={(e) => setAddGoal(e.target.value)}
                placeholder="8 周系统学完机器学习基础"
                autoFocus
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                周期 <span className="text-muted-foreground/70">（周数，可留空）</span>
              </label>
              <Input
                value={addWeeks}
                onChange={(e) => setAddWeeks(e.target.value)}
                placeholder="8"
                inputMode="numeric"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                任务 <span className="text-muted-foreground/70">（每行一条，可留空后续再加）</span>
              </label>
              <Textarea
                value={addTasks}
                onChange={(e) => setAddTasks(e.target.value)}
                placeholder={'复习线性代数\n学习概率论基础\n动手实现线性回归'}
                rows={4}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setAddOpen(false)
                resetAddForm()
              }}
              disabled={adding}
            >
              取消
            </Button>
            <Button onClick={submitAdd} disabled={!addGoal.trim() || adding}>
              {adding ? '创建中...' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={abandonTarget !== null}
        onOpenChange={(o) => !o && !abandonBusy && setAbandonTarget(null)}
        title="放弃该计划？"
        description={`"${abandonTarget?.goal}" 将被标记为已放弃，不再出现在列表里。已有任务记录会保留。`}
        loading={abandonBusy}
        confirmLabel="放弃"
        onConfirm={confirmAbandon}
      />
    </div>
  )
}
