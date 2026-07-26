import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { SettingsSection } from '@/components/settings/SettingsSection'
import { deleteOwnAccount } from '@/api/client'
import { useAuth } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { useWriteScope } from '@/lib/permissions'

type Step = 'idle' | 'confirm1' | 'confirm2'

/** 注销账号：二次确认后永久删除本账号及其全部数据，随后退出到登录页。 */
export function AccountDeletion() {
  const { user, logout } = useAuth()
  const { allowed: canWriteAccount, tip: accountTip } = useWriteScope('account')
  const [step, setStep] = useState<Step>('idle')
  const [deleting, setDeleting] = useState(false)

  const doDelete = async () => {
    setDeleting(true)
    try {
      await deleteOwnAccount()
      toast.success('账号已注销')
      setStep('idle')
      // 账号与登录态已删，清空前端用户态回到登录页
      await logout()
    } catch (e) {
      toast.error((e as Error).message)
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-3">
      <SettingsSection
        danger
        title="注销账号"
        description={
          <>
            将永久删除账号「{user?.username}」及全部数据（会话、记忆、学习计划、测验、SRS、规则），
            不可恢复。删除前请确认已备份需要的内容。
          </>
        }
      >
        <Button variant="destructive" size="sm" disabled={!canWriteAccount} title={canWriteAccount ? undefined : accountTip} onClick={() => setStep('confirm1')}>
          注销账号
        </Button>
      </SettingsSection>

      <ConfirmDialog
        open={step === 'confirm1'}
        onOpenChange={(open) => !open && setStep('idle')}
        title="确定要注销账号吗？"
        description="这会永久删除你的账号及全部数据。请谨慎操作。"
        confirmLabel="继续"
        onConfirm={() => setStep('confirm2')}
      />

      <ConfirmDialog
        open={step === 'confirm2'}
        onOpenChange={(open) => !open && !deleting && setStep('idle')}
        title="最后确认"
        description={`此操作不可恢复。确认后将立即删除账号「${user?.username}」及其所有数据。`}
        loading={deleting}
        confirmLabel="确认注销"
        onConfirm={doDelete}
      />
    </div>
  )
}
