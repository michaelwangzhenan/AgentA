import { useState } from 'react'

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
  const tabs: { value: Tab; label: string }[] = [
    { value: 'mine', label: '我的用量' },
    ...(isAdmin ? [{ value: 'all', label: '全员用量' } as { value: Tab; label: string }] : []),
    { value: 'savings', label: '降本' },
    ...(isAdmin
      ? ([
          { value: 'savings_all', label: '全员降本' },
          { value: 'pricing', label: '单价配置' },
        ] as { value: Tab; label: string }[])
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
                    'w-full rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
                    active === t.value
                      ? 'bg-muted font-medium text-foreground'
                      : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                  )}
                >
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
