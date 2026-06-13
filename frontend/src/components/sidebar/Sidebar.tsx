import type { ReactNode } from 'react'
import { useState } from 'react'
import {
  BarChart3,
  BookOpen,
  Brain,
  ChevronDown,
  GaugeCircle,
  GraduationCap,
  HardDriveDownload,
  HelpCircle,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plug,
  Plus,
  ScrollText,
  Settings,
  Sparkles,
  Trash2,
  UserCircle2,
} from 'lucide-react'

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
import { ThemeToggle } from '@/components/settings/ThemeToggle'
import { cn } from '@/lib/utils'

import type { Session } from '@/types/session'

const RECENTS_COLLAPSED_KEY = 'agenta:sidebar:recentsCollapsed'

export type ViewKind =
  | 'chat'
  | 'kb'
  | 'memory'
  | 'rules'
  | 'skills'
  | 'mcp'
  | 'mastery'
  | 'usage'
  | 'quality'
  | 'backup'
  | 'settings'

export type SidebarProps = {
  sessions: Session[]
  activeId: string | null
  activeView: ViewKind
  username: string
  isAdmin: boolean
  onLogout: () => void
  onSelect: (id: string) => void
  onCreate: () => void
  onRename: (id: string, title: string) => Promise<void> | void
  onDelete: (id: string) => Promise<void> | void
  onSwitchView: (view: ViewKind) => void
}

export function Sidebar(props: SidebarProps) {
  const {
    sessions,
    activeId,
    activeView,
    username,
    isAdmin,
    onLogout,
    onSelect,
    onCreate,
    onRename,
    onDelete,
    onSwitchView,
  } = props

  const [renameTarget, setRenameTarget] = useState<Session | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null)
  const [confirmLogout, setConfirmLogout] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [recentsCollapsed, setRecentsCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(RECENTS_COLLAPSED_KEY) === '1'
    } catch {
      return false
    }
  })

  const toggleRecents = () => {
    setRecentsCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem(RECENTS_COLLAPSED_KEY, next ? '1' : '0')
      } catch {
        // 隐私模式下 localStorage 可能不可用，忽略
      }
      return next
    })
  }

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

  const handleSelectSession = (id: string) => {
    // 切到具体 session 时同时切回 chat view
    if (activeView !== 'chat') onSwitchView('chat')
    onSelect(id)
  }

  const handleCreateAndSwitch = () => {
    if (activeView !== 'chat') onSwitchView('chat')
    onCreate()
  }

  return (
    <aside className="flex h-full w-64 flex-col border-r border-border bg-muted/30">
      <div className="border-b border-border p-3">
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start gap-2"
          onClick={handleCreateAndSwitch}
        >
          <Plus className="h-4 w-4" />
          新建会话
        </Button>
      </div>

      <div className="border-b border-border px-2 py-2">
        <ViewNavButton
          icon={<MessageSquare className="h-4 w-4" />}
          label="聊天"
          active={activeView === 'chat'}
          onClick={() => onSwitchView('chat')}
        />
        <ViewNavButton
          icon={<BookOpen className="h-4 w-4" />}
          label="知识库"
          active={activeView === 'kb'}
          onClick={() => onSwitchView('kb')}
        />
        <ViewNavButton
          icon={<Brain className="h-4 w-4" />}
          label="记忆"
          active={activeView === 'memory'}
          onClick={() => onSwitchView('memory')}
        />
        <ViewNavButton
          icon={<ScrollText className="h-4 w-4" />}
          label="Rules"
          active={activeView === 'rules'}
          onClick={() => onSwitchView('rules')}
        />
        {isAdmin && (
          <ViewNavButton
            icon={<Sparkles className="h-4 w-4" />}
            label="Skills"
            active={activeView === 'skills'}
            onClick={() => onSwitchView('skills')}
          />
        )}
        {isAdmin && (
          <ViewNavButton
            icon={<Plug className="h-4 w-4" />}
            label="MCP"
            active={activeView === 'mcp'}
            onClick={() => onSwitchView('mcp')}
          />
        )}
        <ViewNavButton
          icon={<GraduationCap className="h-4 w-4" />}
          label="学而时习"
          active={activeView === 'mastery'}
          onClick={() => onSwitchView('mastery')}
        />
        <ViewNavButton
          icon={<BarChart3 className="h-4 w-4" />}
          label="用量"
          active={activeView === 'usage'}
          onClick={() => onSwitchView('usage')}
        />
        <ViewNavButton
          icon={<GaugeCircle className="h-4 w-4" />}
          label="质量看板"
          active={activeView === 'quality'}
          onClick={() => onSwitchView('quality')}
        />
        {isAdmin && (
          <ViewNavButton
            icon={<HardDriveDownload className="h-4 w-4" />}
            label="备份与恢复"
            active={activeView === 'backup'}
            onClick={() => onSwitchView('backup')}
          />
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <button
          type="button"
          onClick={toggleRecents}
          className="flex w-full items-center justify-between px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground"
          aria-expanded={!recentsCollapsed}
          aria-controls="recents-list"
        >
          <span>Recents</span>
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 transition-transform',
              recentsCollapsed && '-rotate-90',
            )}
          />
        </button>
        {!recentsCollapsed && (
          <nav id="recents-list" className="flex-1 overflow-y-auto px-2 pb-2">
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
                        s.id === activeId &&
                          activeView === 'chat' &&
                          'bg-accent text-accent-foreground',
                      )}
                      onClick={() => handleSelectSession(s.id)}
                    >
                      <span
                        className="flex-1 truncate"
                        title={s.title || 'New Chat'}
                      >
                        {s.title || 'New Chat'}
                      </span>
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          className="opacity-0 transition-opacity group-hover:opacity-100 hover:bg-accent rounded p-1"
                          onClick={(e) => e.stopPropagation()}
                          aria-label="会话操作"
                        >
                          <MoreHorizontal className="h-4 w-4" />
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
        )}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2">
        <DropdownMenu>
          <DropdownMenuTrigger
            className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-1.5 py-1 text-left hover:bg-accent/60"
            title={username}
          >
            <UserCircle2 className="h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-sm leading-tight">{username}</span>
              <span className="truncate text-[11px] leading-tight text-muted-foreground">
                {isAdmin ? 'ADMIN' : 'User'}
              </span>
            </div>
            <ChevronDown className="ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" side="top" className="w-44">
            <DropdownMenuItem onClick={() => onSwitchView('settings')}>
              <Settings className="mr-2 h-4 w-4" />
              设置
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => setHelpOpen(true)}>
              <HelpCircle className="mr-2 h-4 w-4" />
              帮助
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              onClick={() => setConfirmLogout(true)}
            >
              <LogOut className="mr-2 h-4 w-4" />
              退出
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <ThemeToggle />
      </div>

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

      {/* 删除确认 AlertDialog —— 回车默认触发"删除" */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(o: boolean) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent
          onKeyDown={(e) => {
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.ctrlKey &&
              !e.metaKey &&
              !e.altKey
            ) {
              e.preventDefault()
              confirmDelete()
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>删除会话？</AlertDialogTitle>
            <AlertDialogDescription>
              即将删除 "{deleteTarget?.title || 'New Chat'}"
              及其所有消息记录，不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmDelete}
              autoFocus
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 退出确认 AlertDialog */}
      <AlertDialog
        open={confirmLogout}
        onOpenChange={(o: boolean) => setConfirmLogout(o)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>退出登录？</AlertDialogTitle>
            <AlertDialogDescription>
              退出后需重新登录才能继续使用。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmLogout(false)
                onLogout()
              }}
              autoFocus
            >
              退出
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 帮助（占位） */}
      <Dialog open={helpOpen} onOpenChange={(o: boolean) => setHelpOpen(o)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>帮助</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">帮助文档即将上线，敬请期待。</p>
        </DialogContent>
      </Dialog>
    </aside>
  )
}

type ViewNavButtonProps = {
  icon: ReactNode
  label: string
  active: boolean
  onClick: () => void
}

function ViewNavButton({ icon, label, active, onClick }: ViewNavButtonProps) {
  return (
    <button
      className={cn(
        'mt-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm first:mt-0',
        active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
      )}
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  )
}
