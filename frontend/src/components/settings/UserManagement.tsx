import { useCallback, useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { deleteUser, listUsers } from '@/api/client'
import { useAuth } from '@/lib/auth'
import { toast } from '@/lib/toast'
import type { UserInfo } from '@/types/auth'

/** 用户管理：仅 admin 可见。列出所有用户，可删除（连带清理其全部业务数据）。 */
export function UserManagement() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<UserInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [target, setTarget] = useState<UserInfo | null>(null)
  const [deleting, setDeleting] = useState(false)

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

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        删除用户会同时清理其全部会话、记忆、学习计划、测验与 SRS 数据，不可恢复。
      </p>

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
                const isSelf = u.id === me?.id
                return (
                  <tr key={u.id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2">{u.username}</td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          u.role === 'admin'
                            ? 'rounded bg-accent px-1.5 py-0.5 text-[10px] font-medium uppercase text-accent-foreground'
                            : 'text-xs text-muted-foreground'
                        }
                      >
                        {u.role}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {u.created_at || '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-destructive"
                        disabled={isSelf}
                        title={isSelf ? '不能删除自己' : '删除用户'}
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
