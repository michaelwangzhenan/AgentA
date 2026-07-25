import { useCallback, useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { createUser, deleteUser, listUsers, updateUserRole } from '@/api/client'
import { toast } from '@/lib/toast'
import type { UserInfo, UserRole } from '@/types/auth'

function isProtectedUser(u: UserInfo): boolean {
  return u.can_manage_users === true
}

/** 用户管理：仅主账号可见。列出用户、新建、改角色、删除。 */
export function UserManagement() {
  const [users, setUsers] = useState<UserInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [target, setTarget] = useState<UserInfo | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [createUsername, setCreateUsername] = useState('')
  const [createPassword, setCreatePassword] = useState('')
  const [createRole, setCreateRole] = useState<UserRole>('user')
  const [creating, setCreating] = useState(false)
  const [roleUpdatingId, setRoleUpdatingId] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setUsers(await listUsers())
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const confirmDelete = async () => {
    if (!target) return
    setDeleting(true)
    try {
      await deleteUser(target.id)
      toast.success(`已删除用户 ${target.username}`)
      await refresh()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDeleting(false)
      setTarget(null)
    }
  }

  const resetCreateForm = () => {
    setCreateUsername('')
    setCreatePassword('')
    setCreateRole('user')
  }

  const submitCreate = async () => {
    const username = createUsername.trim()
    if (!username || !createPassword) return
    setCreating(true)
    try {
      await createUser(username, createPassword, createRole)
      toast.success(`已创建用户 ${username}`)
      setCreateOpen(false)
      resetCreateForm()
      await refresh()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setCreating(false)
    }
  }

  const handleRoleChange = async (user: UserInfo, role: UserRole) => {
    if (user.role === role || isProtectedUser(user)) return
    setRoleUpdatingId(user.id)
    try {
      await updateUserRole(user.id, role)
      toast.success(`已将 ${user.username} 设为 ${role}`)
      await refresh()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setRoleUpdatingId(null)
    }
  }

  const canSubmitCreate =
    createUsername.trim().length > 0 && createPassword.length > 0 && !creating

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          删除用户会同时清理其全部会话、记忆、学习计划、测验与 SRS 数据，不可恢复。
        </p>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          新建用户
        </Button>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">用户名</th>
                <th className="px-3 py-2 font-medium">角色</th>
                <th className="px-3 py-2 font-medium">创建时间</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const protectedUser = isProtectedUser(u)
                return (
                  <tr key={u.id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2">{u.username}</td>
                    <td className="px-3 py-2">
                      {protectedUser ? (
                        <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] font-medium uppercase text-accent-foreground">
                          SUPER
                        </span>
                      ) : (
                        <select
                          className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                          value={u.role}
                          disabled={roleUpdatingId === u.id}
                          onChange={(e) =>
                            void handleRoleChange(u, e.target.value as UserRole)
                          }
                        >
                          <option value="user">user</option>
                          <option value="admin">admin</option>
                        </select>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {u.created_at || '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-destructive"
                        disabled={protectedUser}
                        title={protectedUser ? '主账号不可删除' : '删除用户'}
                        onClick={() => setTarget(u)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) resetCreateForm()
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>新建用户</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="new-username">
                用户名
              </label>
              <Input
                id="new-username"
                value={createUsername}
                onChange={(e) => setCreateUsername(e.target.value)}
                maxLength={64}
                autoFocus
                placeholder="1–64 个字符"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="new-password">
                初始密码
              </label>
              <Input
                id="new-password"
                type="password"
                value={createPassword}
                onChange={(e) => setCreatePassword(e.target.value)}
                maxLength={128}
                placeholder="交给用户后请提醒修改"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="new-role">
                角色
              </label>
              <select
                id="new-role"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={createRole}
                onChange={(e) => setCreateRole(e.target.value as UserRole)}
              >
                <option value="user">user（默认）</option>
                <option value="admin">admin</option>
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button onClick={() => void submitCreate()} disabled={!canSubmitCreate}>
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => !open && setTarget(null)}
        title="删除用户"
        description={
          target
            ? `确定删除用户「${target.username}」？其全部业务数据将一并清除，且不可恢复。`
            : ''
        }
        loading={deleting}
        confirmLabel="删除"
        onConfirm={confirmDelete}
      />
    </div>
  )
}
