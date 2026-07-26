import type { LucideIcon } from 'lucide-react'
import { PiggyBank, Tag, TrendingDown, User, Users } from 'lucide-react'

import { cn } from '@/lib/utils'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { UsageDashboard } from './UsageDashboard'
import { PricingConfig } from './PricingConfig'
import { SavingsPanel } from './SavingsPanel'
import type { UsageTab } from '@/routes/paths'

type TabDef = { value: UsageTab; label: string; icon: LucideIcon }

const TABS: TabDef[] = [
  { value: 'mine', label: '我的用量', icon: User },
  { value: 'all', label: '全员用量', icon: Users },
  { value: 'savings', label: '降本', icon: TrendingDown },
  { value: 'savings_all', label: '全员降本', icon: PiggyBank },
  { value: 'pricing', label: '单价配置', icon: Tag },
]

export function UsageView({
  tab,
  onTabChange,
}: {
  tab: UsageTab
  onTabChange: (tab: UsageTab) => void
}) {
  const active = TABS.some((t) => t.value === tab) ? tab : 'mine'

  return (
    <ResourcePage title="用量" subtitle="Token 使用与成本估算（每用户独立）">
      <div className="flex min-h-0 flex-1 gap-4">
        <nav className="sticky top-0 w-32 shrink-0 self-start">
          <ul className="space-y-0.5">
            {TABS.map((t) => (
              <li key={t.value}>
                <button
                  type="button"
                  onClick={() => onTabChange(t.value)}
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

        <div className="min-w-0 flex-1">
          {active === 'mine' && <UsageDashboard scope="mine" />}
          {active === 'savings' && <SavingsPanel scope="mine" />}
          {active === 'all' && <UsageDashboard scope="all" />}
          {active === 'savings_all' && <SavingsPanel scope="all" />}
          {active === 'pricing' && <PricingConfig />}
        </div>
      </div>
    </ResourcePage>
  )
}
