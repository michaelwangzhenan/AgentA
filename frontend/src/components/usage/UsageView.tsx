import { useState } from 'react'

import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { UsageDashboard } from './UsageDashboard'
import { PricingConfig } from './PricingConfig'

type Tab = 'mine' | 'all' | 'pricing'

export function UsageView() {
  const { isAdmin } = useAuth()
  const [tab, setTab] = useState<Tab>('mine')

  const tabs: { value: Tab; label: string }[] = [
    { value: 'mine', label: '我的用量' },
    ...(isAdmin
      ? ([
          { value: 'all', label: '全员用量' },
          { value: 'pricing', label: '单价配置' },
        ] as { value: Tab; label: string }[])
      : []),
  ]

  // admin 关闭后兜底（理论上 isAdmin 不会动态变）
  const active = tabs.some((t) => t.value === tab) ? tab : 'mine'

  return (
    <ResourcePage
      title="用量"
      subtitle="Token 使用与成本估算（每用户独立）"
      toolbar={
        <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5">
          {tabs.map((t) => (
            <button
              key={t.value}
              onClick={() => setTab(t.value)}
              className={cn(
                'rounded px-3 py-1 text-xs transition-colors',
                active === t.value
                  ? 'bg-background font-medium text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      }
    >
      {active === 'mine' && <UsageDashboard scope="mine" />}
      {active === 'all' && isAdmin && <UsageDashboard scope="all" />}
      {active === 'pricing' && isAdmin && <PricingConfig />}
    </ResourcePage>
  )
}
