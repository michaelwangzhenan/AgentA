import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { changePassword } from '@/api/client'
import { toast } from '@/lib/toast'

/** 修改密码：需校验旧密码，新密码两次输入一致。 */
export function PasswordSettings() {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (!oldPwd || !newPwd) return
    if (newPwd !== confirmPwd) {
      toast.error('两次输入的新密码不一致')
      return
    }
    setSaving(true)
    try {
      await changePassword(oldPwd, newPwd)
      setOldPwd('')
      setNewPwd('')
      setConfirmPwd('')
      toast.success('密码已更新')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="max-w-xs space-y-2">
      <h3 className="text-sm font-semibold">修改密码</h3>
      <Input
        type="password"
        value={oldPwd}
        onChange={(e) => setOldPwd(e.target.value)}
        placeholder="当前密码"
        autoComplete="current-password"
      />
      <Input
        type="password"
        value={newPwd}
        onChange={(e) => setNewPwd(e.target.value)}
        placeholder="新密码"
        autoComplete="new-password"
      />
      <Input
        type="password"
        value={confirmPwd}
        onChange={(e) => setConfirmPwd(e.target.value)}
        placeholder="确认新密码"
        autoComplete="new-password"
      />
      <Button
        size="sm"
        onClick={save}
        disabled={!oldPwd || !newPwd || !confirmPwd || saving}
      >
        更新密码
      </Button>
    </section>
  )
}
