import { useState } from 'react'

import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { TraceDashboard } from './TraceDashboard'
import { GoldenManager } from './GoldenManager'
import { ReportsViewer } from './ReportsViewer'

type Tab = 'trace' | 'golden' | 'reports'

export function QualityView() {
  const { isAdmin } = useAuth()
  const [tab, setTab] = useState<Tab>('trace')

  const tabs: { value: Tab; label: string }[] = [
    { value: 'trace', label: '在线可观测' },
    ...(isAdmin
      ? ([
          { value: 'golden', label: 'Golden 管理' },
          { value: 'reports', label: '评估报告' },
        ] as { value: Tab; label: string }[])
      : []),
  ]

  const active = tabs.some((t) => t.value === tab) ? tab : 'trace'

  return (
    <ResourcePage
      title="质量看板"
      subtitle="在线 trace 可观测 + RAG golden 管理 + 离线评估报告"
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
      {active === 'trace' && <TraceDashboard />}
      {active === 'golden' && isAdmin && <GoldenManager />}
      {active === 'reports' && isAdmin && <ReportsViewer />}
    </ResourcePage>
  )
}
