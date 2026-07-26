import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SettingsSection } from '@/components/settings/SettingsSection'
import { updateUsername } from '@/api/client'
import { useAuth } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { useWriteScope } from '@/lib/permissions'

/** 个人信息：所有用户可用。改用户名已实现；头像 / 语言暂为占位。改密码见独立分区。 */
export function ProfileSettings() {
  const { user, refreshUser } = useAuth()
  const { allowed: canWriteProfile, tip: profileTip } = useWriteScope('profile')

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
    <div className="space-y-4">
      <SettingsSection title="用户名" description="应用内展示的名字，最长 64 字，可随时修改。">
        <div className="flex items-center gap-2">
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            maxLength={64}
            className="max-w-xs"
            readOnly={!canWriteProfile}
            disabled={!canWriteProfile}
          />
          <Button size="sm" onClick={saveUsername} disabled={!canWriteProfile || !nameChanged || savingName} title={canWriteProfile ? undefined : profileTip}>
            保存
          </Button>
        </div>
      </SettingsSection>

      <SettingsSection title="头像" className="opacity-60">
        <p className="text-xs text-muted-foreground">即将支持，敬请期待</p>
      </SettingsSection>

      <SettingsSection title="语言" className="opacity-60">
        <p className="text-xs text-muted-foreground">即将支持，敬请期待</p>
      </SettingsSection>
    </div>
  )
}
