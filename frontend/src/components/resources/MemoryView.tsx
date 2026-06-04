import { useCallback, useEffect, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
} from '@/api/client'
import {
  CATEGORY_LABELS,
  SOURCE_LABELS,
  type MemoryItem,
} from '@/types/resources'
import { ResourcePage } from '@/components/resources/ResourcePage'

export function MemoryView() {
  const [items, setItems] = useState<MemoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [editTarget, setEditTarget] = useState<MemoryItem | null>(null)
  const [editValue, setEditValue] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<MemoryItem | null>(null)
  const [confirmClearOpen, setConfirmClearOpen] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setItems(await listMemories())
    } catch (e) {
      setError((e as Error).message)
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
      await refresh()
    } catch (e) {
      setError(`更新失败：${(e as Error).message}`)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    try {
      await deleteMemory(deleteTarget.id)
      setDeleteTarget(null)
      await refresh()
    } catch (e) {
      setError(`删除失败：${(e as Error).message}`)
    }
  }

  const confirmClear = async () => {
    try {
      await clearMemories()
      setConfirmClearOpen(false)
      await refresh()
    } catch (e) {
      setError(`清空失败：${(e as Error).message}`)
    }
  }

  return (
    <ResourcePage
      title="用户记忆"
      subtitle="LLM 自动学到的偏好 / 背景；下次回答会用到"
      toolbar={
        <Button
          variant="outline"
          size="sm"
          onClick={() => setConfirmClearOpen(true)}
          disabled={items.length === 0}
        >
          清空全部
        </Button>
      }
    >
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
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
    </ResourcePage>
  )
}
