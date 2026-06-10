import { useState } from 'react'
import { KeyRound, KeySquare, Route, SlidersHorizontal, User, Users, UserX } from 'lucide-react'

import { ProfileSettings } from '@/components/settings/ProfileSettings'
import { PasswordSettings } from '@/components/settings/PasswordSettings'
import { AccountDeletion } from '@/components/settings/AccountDeletion'
import { ApiKeysConfig } from '@/components/settings/ApiKeysConfig'
import { RoutingPoolConfig } from '@/components/settings/RoutingPoolConfig'
import { SettingsView } from '@/components/settings/SettingsView'
import { UserManagement } from '@/components/settings/UserManagement'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

type Section = 'profile' | 'password' | 'system' | 'apikeys' | 'routing' | 'users' | 'account'

type NavItem = {
  id: Section
  label: string
  icon: typeof User
  adminOnly?: boolean
  danger?: boolean
}

type NavGroup = {
  heading: string
  items: NavItem[]
}

// 分组顺序遵循常见习惯：先“账户”（个人高频项），再“系统”（admin 管理项，
// 相关的 API 密钥 / 模型选择相邻摆放），最后单独的“危险区域”。
const NAV_GROUPS: NavGroup[] = [
  {
    heading: '账户',
    items: [
      { id: 'profile', label: '个人信息', icon: User },
      { id: 'password', label: '密码与安全', icon: KeyRound },
    ],
  },
  {
    heading: '系统',
    items: [
      { id: 'system', label: '系统配置', icon: SlidersHorizontal, adminOnly: true },
      { id: 'apikeys', label: 'API 密钥', icon: KeySquare, adminOnly: true },
      { id: 'routing', label: '模型选择', icon: Route, adminOnly: true },
      { id: 'users', label: '用户管理', icon: Users, adminOnly: true },
    ],
  },
  {
    heading: '危险区域',
    items: [{ id: 'account', label: '注销账号', icon: UserX, danger: true }],
  },
]

/** 设置整页：占满主区域。左侧按“账户 / 系统 / 危险区域”分组导航（按权限过滤），右侧内容。 */
export function SettingsPage() {
  const { isAdmin } = useAuth()
  const [section, setSection] = useState<Section>('profile')

  const groups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((it) => !it.adminOnly || isAdmin),
  })).filter((g) => g.items.length > 0)

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">设置</h1>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 左侧分组导航 */}
        <nav className="w-44 shrink-0 space-y-3 border-r border-border p-2">
          {groups.map((g) => (
            <div key={g.heading}>
              <p className="px-2.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
                {g.heading}
              </p>
              <ul className="space-y-0.5">
                {g.items.map((it) => {
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
                            ? it.danger
                              ? 'bg-destructive/10 font-medium text-destructive'
                              : 'bg-muted font-medium text-foreground'
                            : it.danger
                              ? 'text-destructive/80 hover:bg-destructive/10 hover:text-destructive'
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
            </div>
          ))}
        </nav>

        {/* 右侧内容 */}
        <div className="min-w-0 flex-1 overflow-y-auto p-6">
          {section === 'profile' && (
            <div className="mx-auto max-w-2xl">
              <PageHeader title="个人信息" description="管理用户名、头像与语言等基础资料。" />
              <ProfileSettings />
            </div>
          )}
          {section === 'password' && (
            <div className="mx-auto max-w-2xl">
              <PageHeader title="密码与安全" description="修改登录密码，保护账号安全。" />
              <PasswordSettings />
            </div>
          )}
          {section === 'system' && isAdmin && (
            <div className="mx-auto flex h-full max-w-4xl flex-col">
              <PageHeader
                title="系统配置"
                description="调整服务端运行参数，改完立即生效并持久化。"
              />
              <div className="min-h-0 flex-1">
                <SettingsView embedded />
              </div>
            </div>
          )}
          {section === 'apikeys' && isAdmin && (
            <div className="mx-auto max-w-2xl">
              <PageHeader
                title="API 密钥"
                description="为各 LLM 厂商与 web 搜索配置密钥，保存后立即生效。"
              />
              <ApiKeysConfig />
            </div>
          )}
          {section === 'routing' && isAdmin && (
            <div className="mx-auto max-w-2xl">
              <PageHeader
                title="模型选择"
                description="选定参与自动路由的候选模型，控制成本与可用性。"
              />
              <RoutingPoolConfig />
            </div>
          )}
          {section === 'users' && isAdmin && (
            <div className="mx-auto max-w-3xl">
              <PageHeader title="用户管理" description="查看并删除用户账号及其全部数据。" />
              <UserManagement />
            </div>
          )}
          {section === 'account' && (
            <div className="mx-auto max-w-2xl">
              <PageHeader title="注销账号" description="永久删除本账号及其全部数据，不可恢复。" />
              <AccountDeletion />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** 每个设置分区右侧顶部的标题 + 一句话说明，统一风格。 */
function PageHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
      <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
    </div>
  )
}
