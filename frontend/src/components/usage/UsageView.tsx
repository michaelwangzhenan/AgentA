import { useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { PiggyBank, Tag, TrendingDown, User, Users } from 'lucide-react'

import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { UsageDashboard } from './UsageDashboard'
import { PricingConfig } from './PricingConfig'
import { SavingsPanel } from './SavingsPanel'

type Tab = 'mine' | 'savings' | 'all' | 'savings_all' | 'pricing'

export function UsageView() {
  const { isAdmin } = useAuth()
  const [tab, setTab] = useState<Tab>('mine')

  // 同类相邻：两个「用量」挨着、两个「降本」挨着，最后单价配置
  type TabDef = { value: Tab; label: string; icon: LucideIcon }
  const tabs: TabDef[] = [
    { value: 'mine', label: '我的用量', icon: User },
    ...(isAdmin ? [{ value: 'all', label: '全员用量', icon: Users } as TabDef] : []),
    { value: 'savings', label: '降本', icon: TrendingDown },
    ...(isAdmin
      ? ([
          { value: 'savings_all', label: '全员降本', icon: PiggyBank },
          { value: 'pricing', label: '单价配置', icon: Tag },
        ] as TabDef[])
      : []),
  ]

  // admin 关闭后兜底（理论上 isAdmin 不会动态变）
  const active = tabs.some((t) => t.value === tab) ? tab : 'mine'

  return (
    <ResourcePage title="用量" subtitle="Token 使用与成本估算（每用户独立）">
      <div className="flex min-h-0 flex-1 gap-4">
        {/* 左侧竖向导航（同质量看板样式） */}
        <nav className="sticky top-0 w-32 shrink-0 self-start">
          <ul className="space-y-0.5">
            {tabs.map((t) => (
              <li key={t.value}>
                <button
                  type="button"
                  onClick={() => setTab(t.value)}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
                    active === t.value
                      ? 'bg-muted font-medium text-foreground'
                      : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                  )}
                >
                  <t.icon className="h-4 w-4 shrink-0" />
                  {t.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        {/* 右侧内容 */}
        <div className="min-w-0 flex-1">
          {active === 'mine' && <UsageDashboard scope="mine" />}
          {active === 'savings' && <SavingsPanel scope="mine" />}
          {active === 'all' && isAdmin && <UsageDashboard scope="all" />}
          {active === 'savings_all' && isAdmin && <SavingsPanel scope="all" />}
          {active === 'pricing' && isAdmin && <PricingConfig />}
        </div>
      </div>
    </ResourcePage>
  )
}
