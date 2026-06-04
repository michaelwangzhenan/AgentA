import { useState } from 'react'
import { MoreHorizontal, Pencil, Plus, Trash2 } from 'lucide-react'

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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

import type { Session } from '@/types/session'

export type SidebarProps = {
  sessions: Session[]
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  onRename: (id: string, title: string) => Promise<void> | void
  onDelete: (id: string) => Promise<void> | void
}

export function Sidebar(props: SidebarProps) {
  const { sessions, activeId, onSelect, onCreate, onRename, onDelete } = props

  const [renameTarget, setRenameTarget] = useState<Session | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null)

  const openRename = (s: Session) => {
    setRenameValue(s.title)
    setRenameTarget(s)
  }

  const submitRename = async () => {
    if (!renameTarget) return
    const title = renameValue.trim()
    if (!title) return
    await onRename(renameTarget.id, title)
    setRenameTarget(null)
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    await onDelete(deleteTarget.id)
    setDeleteTarget(null)
  }

  return (
    <aside className="flex h-full w-64 flex-col border-r border-border bg-muted/30">
      <div className="border-b border-border p-3">
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start gap-2"
          onClick={onCreate}
        >
          <Plus className="h-4 w-4" />
          新建会话
        </Button>
      </div>

      <nav className="flex-1 overflow-y-auto p-2">
        {sessions.length === 0 ? (
          <p className="px-2 py-4 text-center text-xs text-muted-foreground">
            暂无会话
          </p>
        ) : (
          <ul className="space-y-1">
            {sessions.map((s) => (
              <li key={s.id}>
                <div
                  className={cn(
                    'group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm',
                    'hover:bg-accent/60 cursor-pointer',
                    s.id === activeId && 'bg-accent text-accent-foreground',
                  )}
                  onClick={() => onSelect(s.id)}
                >
                  <span
                    className="flex-1 truncate"
                    title={s.title || s.id}
                  >
                    {s.title || s.id.slice(0, 8)}
                  </span>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        className="opacity-0 transition-opacity group-hover:opacity-100 hover:bg-accent rounded p-1"
                        onClick={(e) => e.stopPropagation()}
                        aria-label="会话操作"
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => openRename(s)}>
                        <Pencil className="mr-2 h-4 w-4" />
                        重命名
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        className="text-destructive focus:text-destructive"
                        onClick={() => setDeleteTarget(s)}
                      >
                        <Trash2 className="mr-2 h-4 w-4" />
                        删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </li>
            ))}
          </ul>
        )}
      </nav>

      {/* 重命名 Dialog */}
      <Dialog
        open={renameTarget !== null}
        onOpenChange={(o: boolean) => !o && setRenameTarget(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>重命名会话</DialogTitle>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitRename()
            }}
            placeholder="新标题"
            autoFocus
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameTarget(null)}>
              取消
            </Button>
            <Button onClick={submitRename} disabled={!renameValue.trim()}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 AlertDialog */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(o: boolean) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除会话？</AlertDialogTitle>
            <AlertDialogDescription>
              即将删除 "{deleteTarget?.title || deleteTarget?.id.slice(0, 8)}"
              及其所有消息记录，不可恢复。
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
    </aside>
  )
}
