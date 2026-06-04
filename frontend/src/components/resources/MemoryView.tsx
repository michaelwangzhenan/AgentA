import { useCallback, useEffect, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'

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
  clearMemories,
  deleteMemory,
  listMemories,
  patchMemory,
  upsertMemory,
} from '@/api/client'
import {
  CATEGORY_LABELS,
  SOURCE_LABELS,
  type MemoryItem,
} from '@/types/resources'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { toast } from '@/lib/toast'

// 跟后端 src/memory/user_memory.py MEMORY_CATEGORIES 对齐
const ADD_CATEGORY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'preference', label: CATEGORY_LABELS.preference },
  { value: 'background', label: CATEGORY_LABELS.background },
  { value: 'instruction', label: CATEGORY_LABELS.instruction },
  { value: 'task', label: CATEGORY_LABELS.task },
  { value: 'correction', label: CATEGORY_LABELS.correction },
]

export function MemoryView() {
  const [items, setItems] = useState<MemoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [editTarget, setEditTarget] = useState<MemoryItem | null>(null)
  const [editValue, setEditValue] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<MemoryItem | null>(null)
  const [confirmClearOpen, setConfirmClearOpen] = useState(false)

  // 手动添加表单
  const [addOpen, setAddOpen] = useState(false)
  const [addCategory, setAddCategory] = useState<string>('preference')
  const [addKey, setAddKey] = useState('')
  const [addValue, setAddValue] = useState('')
  const [adding, setAdding] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      setItems(await listMemories())
    } catch (e) {
      setLoadError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const submitEdit = async () => {
    if (!editTarget) return
    const v = editValue.trim()
    if (!v) return
    try {
      await patchMemory(editTarget.id, v)
      setEditTarget(null)
      toast.success('已更新')
      await refresh()
    } catch (e) {
      toast.error(`更新失败：${(e as Error).message}`)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    try {
      await deleteMemory(deleteTarget.id)
      setDeleteTarget(null)
      toast.success('已删除')
      await refresh()
    } catch (e) {
      toast.error(`删除失败：${(e as Error).message}`)
    }
  }

  const confirmClear = async () => {
    try {
      const resp = await clearMemories()
      setConfirmClearOpen(false)
      toast.success(`已清空 ${resp.cleared} 条`)
      await refresh()
    } catch (e) {
      toast.error(`清空失败：${(e as Error).message}`)
    }
  }

  const resetAddForm = () => {
    setAddCategory('preference')
    setAddKey('')
    setAddValue('')
  }

  const submitAdd = async () => {
    const k = addKey.trim()
    const v = addValue.trim()
    if (!k || !v) return
    setAdding(true)
    try {
      // source='manual' 跟后端 SOURCE_LABELS 对齐，标记"手工"
      await upsertMemory(addCategory, k, v, 'manual')
      toast.success('已添加')
      resetAddForm()
      setAddOpen(false)
      await refresh()
    } catch (e) {
      toast.error(`添加失败：${(e as Error).message}`)
    } finally {
      setAdding(false)
    }
  }

  const canSubmitAdd = addKey.trim().length > 0 && addValue.trim().length > 0

  return (
    <ResourcePage
      title="用户记忆"
      subtitle="LLM 自动学到的偏好 / 背景；也可手动添加。下次回答会用到"
      toolbar={
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setAddOpen(true)}
          >
            <Plus className="mr-1 h-4 w-4" />
            添加记忆
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setConfirmClearOpen(true)}
            disabled={items.length === 0}
          >
            清空全部
          </Button>
        </>
      }
    >
      {loadError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {loadError}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无记忆条目</p>
      ) : (
        <div className="rounded-lg border border-border bg-card">
          <div className="border-b border-border px-3 py-2 text-sm font-medium">
            共 {items.length} 条
          </div>
          <ul className="divide-y divide-border">
            {items.map((it) => (
              <li key={it.id} className="group flex items-start gap-3 px-3 py-2">
                <div className="flex flex-col gap-0.5 pt-0.5">
                  <span className="inline-flex w-fit items-center rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                    {CATEGORY_LABELS[it.category] ?? it.category}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {SOURCE_LABELS[it.source] ?? it.source}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium" title={it.key}>
                    {it.key}
                  </div>
                  <div className="whitespace-pre-wrap break-words text-sm text-foreground/80">
                    {it.value}
                  </div>
                  <div className="mt-1 text-[10px] text-muted-foreground">
                    {it.created_at}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    className="rounded p-1 hover:bg-accent"
                    onClick={() => {
                      setEditValue(it.value)
                      setEditTarget(it)
                    }}
                    aria-label="编辑"
                    title="编辑 value"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    className="rounded p-1 hover:bg-accent text-destructive"
                    onClick={() => setDeleteTarget(it)}
                    aria-label="删除"
                    title="删除"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Dialog
        open={editTarget !== null}
        onOpenChange={(o: boolean) => !o && setEditTarget(null)}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>编辑 value</DialogTitle>
          </DialogHeader>
          {editTarget && (
            <div className="space-y-2">
              <div className="text-xs text-muted-foreground">
                {CATEGORY_LABELS[editTarget.category] ?? editTarget.category} ·{' '}
                {editTarget.key}
              </div>
              <Input
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') submitEdit()
                }}
                autoFocus
              />
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTarget(null)}>
              取消
            </Button>
            <Button onClick={submitEdit} disabled={!editValue.trim()}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(o: boolean) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除该条记忆？</AlertDialogTitle>
            <AlertDialogDescription>
              即将删除 {deleteTarget && (CATEGORY_LABELS[deleteTarget.category] ?? deleteTarget.category)}
              {' · '}
              {deleteTarget?.key}，不可恢复。
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

      <AlertDialog
        open={confirmClearOpen}
        onOpenChange={setConfirmClearOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>清空全部记忆？</AlertDialogTitle>
            <AlertDialogDescription>
              这会删除全部 {items.length} 条记忆，无法恢复。LLM 下次回答将失去这些上下文。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmClear}
            >
              清空
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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
            <DialogTitle>添加记忆</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label
                htmlFor="add-mem-category"
                className="text-xs font-medium text-muted-foreground"
              >
                类别
              </label>
              <select
                id="add-mem-category"
                value={addCategory}
                onChange={(e) => setAddCategory(e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring [color-scheme:light] dark:[color-scheme:dark]"
              >
                {ADD_CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}（{opt.value}）
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <label
                htmlFor="add-mem-key"
                className="text-xs font-medium text-muted-foreground"
              >
                Key <span className="text-muted-foreground/70">（短标识，例如 favorite_language）</span>
              </label>
              <Input
                id="add-mem-key"
                value={addKey}
                onChange={(e) => setAddKey(e.target.value)}
                placeholder="favorite_language"
                autoFocus
              />
            </div>

            <div className="space-y-1">
              <label
                htmlFor="add-mem-value"
                className="text-xs font-medium text-muted-foreground"
              >
                Value <span className="text-muted-foreground/70">（具体内容；Ctrl+Enter 提交）</span>
              </label>
              <Textarea
                id="add-mem-value"
                value={addValue}
                onChange={(e) => setAddValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault()
                    if (canSubmitAdd && !adding) submitAdd()
                  }
                }}
                placeholder="Python"
                rows={3}
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
            <Button onClick={submitAdd} disabled={!canSubmitAdd || adding}>
              {adding ? '添加中...' : '添加'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ResourcePage>
  )
}
