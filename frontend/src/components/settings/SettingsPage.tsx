import { useState } from 'react'
import { KeyRound, SlidersHorizontal, User, Users, UserX } from 'lucide-react'

import { ProfileSettings } from '@/components/settings/ProfileSettings'
import { PasswordSettings } from '@/components/settings/PasswordSettings'
import { AccountDeletion } from '@/components/settings/AccountDeletion'
import { SettingsView } from '@/components/settings/SettingsView'
import { UserManagement } from '@/components/settings/UserManagement'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

type Section = 'profile' | 'password' | 'system' | 'users' | 'account'

type NavItem = {
  id: Section
  label: string
  icon: typeof User
  adminOnly?: boolean
}

const NAV: NavItem[] = [
  { id: 'profile', label: '个人信息', icon: User },
  { id: 'password', label: '修改密码', icon: KeyRound },
  { id: 'system', label: '系统配置', icon: SlidersHorizontal, adminOnly: true },
  { id: 'users', label: '用户管理', icon: Users, adminOnly: true },
  { id: 'account', label: '注销账号', icon: UserX },
]

/** 设置整页：占满主区域。左侧分区导航（按权限），右侧内容。 */
export function SettingsPage() {
  const { isAdmin } = useAuth()
  const [section, setSection] = useState<Section>('profile')

  const items = NAV.filter((it) => !it.adminOnly || isAdmin)

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">设置</h1>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 左侧分区导航 */}
        <nav className="w-44 shrink-0 border-r border-border p-2">
          <ul className="space-y-0.5">
            {items.map((it) => {
              const Icon = it.icon
              const active = section === it.id
              return (
                <li key={it.id}>
                  <button
                    type="button"
                    onClick={() => setSection(it.id)}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
                      active
                        ? 'bg-muted font-medium text-foreground'
                        : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    {it.label}
                  </button>
                </li>
              )
            })}
          </ul>
        </nav>

        {/* 右侧内容 */}
        <div className="min-w-0 flex-1 overflow-y-auto p-6">
          {section === 'profile' && (
            <div className="mx-auto max-w-2xl">
              <ProfileSettings />
            </div>
          )}
          {section === 'password' && (
            <div className="mx-auto max-w-2xl">
              <PasswordSettings />
            </div>
          )}
          {section === 'system' && isAdmin && (
            <div className="mx-auto flex h-full max-w-4xl flex-col">
              <SettingsView embedded />
            </div>
          )}
          {section === 'users' && isAdmin && (
            <div className="mx-auto max-w-3xl">
              <UserManagement />
            </div>
          )}
          {section === 'account' && (
            <div className="mx-auto max-w-2xl">
              <AccountDeletion />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
