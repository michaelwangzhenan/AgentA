import { useCallback, useEffect, useState } from 'react'
import { Check, Plus, RotateCcw, Trash2, X } from 'lucide-react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  createGolden,
  deleteGolden,
  getGolden,
  importGolden,
  updateGolden,
} from '@/api/client'
import type { GoldenItem, GoldenList, GoldenStatus } from '@/types/eval'

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待审核' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已拒绝' },
]

const STATUS_BADGE: Record<GoldenStatus, string> = {
  pending: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  approved: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  rejected: 'bg-muted text-muted-foreground line-through',
}
const STATUS_TEXT: Record<GoldenStatus, string> = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已拒绝',
}

const PAGE_SIZE = 50

export function GoldenManager() {
  const [status, setStatus] = useState('')
  const [data, setData] = useState<GoldenList | null>(null)
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newQuery, setNewQuery] = useState('')
  const [newKeywords, setNewKeywords] = useState('')
  const [newSource, setNewSource] = useState('')
  const [newSourceExact, setNewSourceExact] = useState('')
  const [newType, setNewType] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  // 删除确认：单条或批量都走这里；null 表示对话框关闭
  const [deleteIds, setDeleteIds] = useState<number[] | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setData(await getGolden({ status: status || undefined, limit: PAGE_SIZE }))
      setSelected(new Set())
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const items = data?.items ?? []
  const allSelected = items.length > 0 && items.every((it) => selected.has(it.id))

  const toggleOne = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(items.map((it) => it.id)))
  }

  const setItemStatus = async (it: GoldenItem, s: GoldenStatus) => {
    try {
      await updateGolden(it.id, { status: s })
      toast.success(`已标记为${STATUS_TEXT[s]}`)
      void refresh()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  // 批量改状态：无批量端点，客户端并发逐条调（admin 工具量级够用）
  const batchSetStatus = async (s: GoldenStatus) => {
    const ids = [...selected]
    if (!ids.length) return
    setBusy(true)
    try {
      const rs = await Promise.allSettled(ids.map((id) => updateGolden(id, { status: s })))
      const failed = rs.filter((r) => r.status === 'rejected').length
      if (failed) toast.error(`${ids.length - failed} 条已标记为${STATUS_TEXT[s]}，${failed} 条失败`)
      else toast.success(`${ids.length} 条已标记为${STATUS_TEXT[s]}`)
      void refresh()
    } finally {
      setBusy(false)
    }
  }

  const confirmDelete = async () => {
    const ids = deleteIds ?? []
    setDeleteIds(null)
    if (!ids.length) return
    setBusy(true)
    try {
      const rs = await Promise.allSettled(ids.map((id) => deleteGolden(id)))
      const failed = rs.filter((r) => r.status === 'rejected').length
      if (failed) toast.error(`已删除 ${ids.length - failed} 条，${failed} 条失败`)
      else toast.success(`已删除 ${ids.length} 条`)
      void refresh()
    } finally {
      setBusy(false)
    }
  }

  const submitCreate = async () => {
    const q = newQuery.trim()
    if (!q) return
    try {
      await createGolden({
        query: q,
        expected_keywords: newKeywords.split(',').map((s) => s.trim()).filter(Boolean),
        expected_source: newSourceExact.trim(),
        expected_source_contains: newSource.trim(),
        type: newType.trim(),
        note: '',
      })
      setNewQuery('')
      setNewKeywords('')
      setNewSource('')
      setNewSourceExact('')
      setNewType('')
      setShowCreate(false)
      void refresh()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const doImport = async () => {
    try {
      const r = await importGolden()
      toast.success(`从 ${r.source} 导入 ${r.added} 条`)
      void refresh()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const counts = data?.counts ?? {}

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatus(f.value)}
              className={cn(
                'rounded px-2.5 py-1 text-xs transition-colors',
                status === f.value
                  ? 'bg-background font-medium text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {f.label}
              {f.value && counts[f.value] != null ? ` (${counts[f.value]})` : ''}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" onClick={doImport}>
            <RotateCcw className="h-3.5 w-3.5" />
            从 golden.json 导入
          </Button>
          <Button size="sm" className="gap-1.5" onClick={() => setShowCreate((v) => !v)}>
            <Plus className="h-3.5 w-3.5" />
            新增
          </Button>
        </div>
      </div>

      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
          <span className="text-muted-foreground">已选 {selected.size} 条</span>
          <div className="ml-auto flex items-center gap-2">
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={() => batchSetStatus('approved')}>
              <Check className="h-3.5 w-3.5 text-emerald-600" />
              批量通过
            </Button>
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={() => batchSetStatus('rejected')}>
              <X className="h-3.5 w-3.5 text-amber-600" />
              批量拒绝
            </Button>
            <Button variant="outline" size="sm" className="gap-1.5 text-destructive" disabled={busy} onClick={() => setDeleteIds([...selected])}>
              <Trash2 className="h-3.5 w-3.5" />
              批量删除
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setSelected(new Set())}>
              清除选择
            </Button>
          </div>
        </div>
      )}

      {showCreate && (
        <div className="space-y-2 rounded-lg border border-border p-3">
          <Input placeholder="问题 query（必填）" value={newQuery} onChange={(e) => setNewQuery(e.target.value)} />
          <Input placeholder="期望关键词（逗号分隔）" value={newKeywords} onChange={(e) => setNewKeywords(e.target.value)} />
          <Input placeholder="期望来源片段 expected_source_contains（子串匹配，可选）" value={newSource} onChange={(e) => setNewSource(e.target.value)} />
          <Input placeholder="期望来源 expected_source（精确匹配，可选）" value={newSourceExact} onChange={(e) => setNewSourceExact(e.target.value)} />
          <Input placeholder="分类 type（如 baseline / hyde，可选）" value={newType} onChange={(e) => setNewType(e.target.value)} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setShowCreate(false)}>
              取消
            </Button>
            <Button size="sm" onClick={submitCreate} disabled={!newQuery.trim()}>
              保存
            </Button>
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
              <th className="w-10 px-3 py-2 text-center font-medium">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 cursor-pointer align-middle accent-primary"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label="全选"
                />
              </th>
              <th className="px-3 py-2 font-medium">问题</th>
              <th className="px-3 py-2 font-medium">期望关键词</th>
              <th className="px-3 py-2 font-medium">来源</th>
              <th className="px-3 py-2 text-center font-medium whitespace-nowrap">状态</th>
              <th className="px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {data && data.items.length > 0 ? (
              data.items.map((it) => (
                <tr
                  key={it.id}
                  className={cn(
                    'border-b border-border last:border-0 align-top',
                    selected.has(it.id) && 'bg-accent/40',
                  )}
                >
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 cursor-pointer align-middle accent-primary"
                      checked={selected.has(it.id)}
                      onChange={() => toggleOne(it.id)}
                      aria-label="选择此条"
                    />
                  </td>
                  <td className="px-3 py-2 max-w-[260px]">
                    {it.query}
                    {it.type ? (
                      <span className="ml-1.5 rounded bg-muted px-1 text-[10px] text-muted-foreground">
                        {it.type}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {it.expected_keywords.join(', ') || '—'}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {it.source === 'ai' ? 'AI 生成' : '手工'}
                    {it.expected_source ? ` · =${it.expected_source}` : ''}
                    {it.expected_source_contains ? ` · ~${it.expected_source_contains}` : ''}
                  </td>
                  <td className="px-3 py-2 text-center whitespace-nowrap">
                    <span className={cn('inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[11px]', STATUS_BADGE[it.status])}>
                      {STATUS_TEXT[it.status]}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      {it.status !== 'approved' && (
                        <button
                          className="rounded p-1 text-emerald-600 hover:bg-accent"
                          title="通过"
                          onClick={() => setItemStatus(it, 'approved')}
                        >
                          <Check className="h-4 w-4" />
                        </button>
                      )}
                      {it.status !== 'rejected' && (
                        <button
                          className="rounded p-1 text-amber-600 hover:bg-accent"
                          title="拒绝"
                          onClick={() => setItemStatus(it, 'rejected')}
                        >
                          <X className="h-4 w-4" />
                        </button>
                      )}
                      <button
                        className="rounded p-1 text-destructive hover:bg-accent"
                        title="删除"
                        onClick={() => setDeleteIds([it.id])}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-sm text-muted-foreground">
                  {loading ? '加载中…' : '暂无 golden（可点「从 golden.json 导入」或「新增」）'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted-foreground">
        golden 用于 RAG 检索评估的标准基准。入库新资料时会自动生成「待审核」候选；评估脚本默认只用「已通过」的。
      </p>

      <AlertDialog open={deleteIds !== null} onOpenChange={(o: boolean) => !o && setDeleteIds(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除 golden？</AlertDialogTitle>
            <AlertDialogDescription>
              即将删除 {deleteIds?.length ?? 0} 条 golden（不可恢复）。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmDelete}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
