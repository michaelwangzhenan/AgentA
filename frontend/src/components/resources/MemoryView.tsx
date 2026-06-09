import { useCallback, useEffect, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
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
  createMemory,
  deleteMemory,
  listMemories,
  patchMemory,
} from '@/api/client'
import {
  SOURCE_LABELS,
  type MemoryItem,
} from '@/types/resources'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { toast } from '@/lib/toast'

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
  const [addText, setAddText] = useState('')
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
    setAddText('')
  }

  const submitAdd = async () => {
    const t = addText.trim()
    if (!t) return
    setAdding(true)
    try {
      // source='manual' 跟后端 SOURCE_LABELS 对齐，标记"手工"
      await createMemory(t, 'manual')
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

  const canSubmitAdd = addText.trim().length > 0

  return (
    <ResourcePage
      title="用户记忆"
      subtitle="LLM 自动学到的偏好 / 背景；也可手动添加。下次回答会用到"
    >
      {loadError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {loadError}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
            <span className="text-sm font-medium">共 {items.length} 条</span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
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
            </div>
          </div>
          {items.length === 0 ? (
            <p className="px-3 py-6 text-sm text-muted-foreground">暂无记忆条目</p>
          ) : (
          <ul className="divide-y divide-border">
            {items.map((it) => (
              <li key={it.id} className="group flex items-start gap-3 px-3 py-2">
                <div className="flex flex-col gap-0.5 pt-0.5">
                  <span className="text-[10px] text-muted-foreground">
                    {SOURCE_LABELS[it.source] ?? it.source}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="whitespace-pre-wrap break-words text-sm text-foreground/90">
                    {it.text}
                  </div>
                  <div className="mt-1 text-[10px] text-muted-foreground">
                    {it.updated_at}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    className="rounded p-1 hover:bg-accent"
                    onClick={() => {
                      setEditValue(it.text)
                      setEditTarget(it)
                    }}
                    aria-label="编辑"
                    title="编辑记忆"
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
          )}
        </div>
      )}

      <Dialog
        open={editTarget !== null}
        onOpenChange={(o: boolean) => !o && setEditTarget(null)}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>编辑记忆</DialogTitle>
          </DialogHeader>
          {editTarget && (
            <div className="space-y-2">
              <Textarea
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault()
                    submitEdit()
                  }
                }}
                rows={3}
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
              即将删除：{deleteTarget?.text}，不可恢复。
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
                htmlFor="add-mem-text"
                className="text-xs font-medium text-muted-foreground"
              >
                记忆内容 <span className="text-muted-foreground/70">（一句自然语言；Ctrl+Enter 提交）</span>
              </label>
              <Textarea
                id="add-mem-text"
                value={addText}
                onChange={(e) => setAddText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault()
                    if (canSubmitAdd && !adding) submitAdd()
                  }
                }}
                placeholder="例如：用户偏好用中文回答，代码风格简洁。"
                rows={3}
                autoFocus
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
