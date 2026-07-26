import type { LucideIcon } from 'lucide-react'
import { Activity, ClipboardCheck, ShieldAlert, Star } from 'lucide-react'

import { cn } from '@/lib/utils'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { TraceDashboard } from './TraceDashboard'
import { GoldenManager, type GoldenDocFilter } from './GoldenManager'
import { OfflineEvalView } from './OfflineEvalView'
import { RuntimeMonitor } from './SecurityPanel'
import type { QualityTab } from '@/routes/paths'

type TabDef = { value: QualityTab; label: string; icon: LucideIcon }

const TABS: TabDef[] = [
  { value: 'trace', label: '会话监控', icon: Activity },
  { value: 'security_runtime', label: '实时安全监控', icon: ShieldAlert },
  { value: 'offline', label: '离线评估', icon: ClipboardCheck },
  { value: 'golden', label: 'Golden 管理', icon: Star },
]

export function QualityView({
  tab,
  goldenFilter,
  onTabChange,
  onClearGoldenFilter,
  onBackToKb,
}: {
  tab: QualityTab
  goldenFilter?: GoldenDocFilter
  onTabChange: (tab: QualityTab) => void
  onClearGoldenFilter?: () => void
  onBackToKb?: () => void
}) {
  const active = TABS.some((t) => t.value === tab) ? tab : 'trace'

  return (
    <ResourcePage
      title="质量看板"
      subtitle="在线 trace 可观测 + RAG golden 管理 + 离线评估"
    >
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
          {active === 'trace' && <TraceDashboard />}
          {active === 'security_runtime' && <RuntimeMonitor />}
          {active === 'offline' && <OfflineEvalView />}
          {active === 'golden' && (
            <GoldenManager
              docFilter={goldenFilter}
              onClearDocFilter={onClearGoldenFilter}
              onBackToKb={goldenFilter ? onBackToKb : undefined}
            />
          )}
        </div>
      </div>
    </ResourcePage>
  )
}
