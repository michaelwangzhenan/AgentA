import { useState } from 'react'

import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { TraceDashboard } from './TraceDashboard'
import { GoldenManager } from './GoldenManager'
import { ReportsViewer } from './ReportsViewer'
import { SecurityPanel } from './SecurityPanel'

type Tab = 'trace' | 'security' | 'golden' | 'reports'

export function QualityView() {
  const { isAdmin } = useAuth()
  const [tab, setTab] = useState<Tab>('trace')

  const tabs: { value: Tab; label: string }[] = [
    { value: 'trace', label: '会话监控' },
    ...(isAdmin
      ? ([
          { value: 'security', label: '安全' },
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
    >
      <div className="flex min-h-0 flex-1 gap-4">
        {/* 左侧竖向导航（同设置页样式） */}
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
          {active === 'trace' && <TraceDashboard />}
          {active === 'security' && isAdmin && <SecurityPanel />}
          {active === 'golden' && isAdmin && <GoldenManager />}
          {active === 'reports' && isAdmin && <ReportsViewer />}
        </div>
      </div>
    </ResourcePage>
  )
}
