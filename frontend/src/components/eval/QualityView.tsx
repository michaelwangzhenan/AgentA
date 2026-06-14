import { useState } from 'react'

import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { TraceDashboard } from './TraceDashboard'
import { GoldenManager } from './GoldenManager'
import { OfflineEvalView } from './OfflineEvalView'
import { RuntimeMonitor } from './SecurityPanel'

type Tab = 'trace' | 'security_runtime' | 'offline' | 'golden'

export function QualityView() {
  const { isAdmin } = useAuth()
  const [tab, setTab] = useState<Tab>('trace')

  const tabs: { value: Tab; label: string }[] = [
    { value: 'trace', label: '会话监控' },
    ...(isAdmin
      ? ([
          { value: 'security_runtime', label: '实时安全监控' },
          { value: 'offline', label: '离线评估' },
          { value: 'golden', label: 'Golden 管理' },
        ] as { value: Tab; label: string }[])
      : []),
  ]

  const active = tabs.some((t) => t.value === tab) ? tab : 'trace'

  return (
    <ResourcePage
      title="质量看板"
      subtitle="在线 trace 可观测 + RAG golden 管理 + 离线评估"
      // 离线评估含报告正文 / 卡片，放宽到 max-w-6xl；其余标签保持默认
      maxWidthClassName={active === 'offline' ? 'max-w-6xl' : 'max-w-4xl'}
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
          {active === 'security_runtime' && isAdmin && <RuntimeMonitor />}
          {active === 'offline' && isAdmin && <OfflineEvalView />}
          {active === 'golden' && isAdmin && <GoldenManager />}
        </div>
      </div>
    </ResourcePage>
  )
}
