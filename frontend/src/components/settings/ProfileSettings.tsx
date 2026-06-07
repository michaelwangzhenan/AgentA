import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { updateUsername } from '@/api/client'
import { useAuth } from '@/lib/auth'
import { toast } from '@/lib/toast'

/** 个人信息：所有用户可用。改用户名已实现；头像 / 语言暂为占位。改密码见独立分区。 */
export function ProfileSettings() {
  const { user, refreshUser } = useAuth()

  const [username, setUsername] = useState(user?.username ?? '')
  const [savingName, setSavingName] = useState(false)

  const nameChanged = username.trim().length > 0 && username.trim() !== user?.username

  const saveUsername = async () => {
    const next = username.trim()
    if (!next || next === user?.username) return
    setSavingName(true)
    try {
      await updateUsername(next)
      await refreshUser()
      toast.success('用户名已更新')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSavingName(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* 用户名 */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">用户名</h3>
        <div className="flex items-center gap-2">
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            maxLength={64}
            className="max-w-xs"
          />
          <Button size="sm" onClick={saveUsername} disabled={!nameChanged || savingName}>
            保存
          </Button>
        </div>
      </section>

      {/* 头像（占位，未实现） */}
      <section className="space-y-2 opacity-60">
        <h3 className="text-sm font-semibold">头像</h3>
        <p className="text-xs text-muted-foreground">即将支持，敬请期待</p>
      </section>

      {/* 语言（占位，未实现） */}
      <section className="space-y-2 opacity-60">
        <h3 className="text-sm font-semibold">语言</h3>
        <p className="text-xs text-muted-foreground">即将支持，敬请期待</p>
      </section>
    </div>
  )
}
