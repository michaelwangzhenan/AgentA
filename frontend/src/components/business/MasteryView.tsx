import { useState } from 'react'
import { CalendarRange, Layers, ListChecks, X } from 'lucide-react'

import { ResourcePage } from '@/components/resources/ResourcePage'
import { PlansView } from '@/components/business/PlansView'
import { QuizzesView } from '@/components/business/QuizzesView'
import { SRSView } from '@/components/business/SRSView'
import { cn } from '@/lib/utils'

type MasteryTab = 'plans' | 'quizzes' | 'srs'

const TABS: { key: MasteryTab; label: string; icon: typeof CalendarRange }[] = [
  { key: 'plans', label: '学习计划', icon: CalendarRange },
  { key: 'quizzes', label: '测验', icon: ListChecks },
  { key: 'srs', label: '复习', icon: Layers },
]

const INTRO_DISMISSED_KEY = 'agenta:mastery:introDismissed'

export function MasteryView() {
  const [tab, setTab] = useState<MasteryTab>('plans')
  const [introDismissed, setIntroDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(INTRO_DISMISSED_KEY) === '1'
    } catch {
      return false
    }
  })

  const dismissIntro = () => {
    setIntroDismissed(true)
    try {
      localStorage.setItem(INTRO_DISMISSED_KEY, '1')
    } catch {
      // 隐私模式下忽略
    }
  }

  return (
    <ResourcePage
      title="学而时习"
      subtitle="定计划 · 做测验 · 间隔复习，三步把知识学透"
    >
      {!introDismissed && (
        <div className="relative rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
          <button
            onClick={dismissIntro}
            className="absolute right-2 top-2 rounded p-1 text-muted-foreground hover:bg-accent"
            aria-label="关闭引导"
          >
            <X className="h-4 w-4" />
          </button>
          <p className="font-medium">怎么用「学而时习」</p>
          <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-muted-foreground">
            <li><b>定计划</b>：设一个学习目标，拆成阶段任务，逐条勾掉。</li>
            <li><b>做测验</b>：在聊天里让 AI 基于知识库出题，回到这里作答、自动批改。</li>
            <li><b>间隔复习</b>：把要记的内容做成卡片，按"重来/困难/良好/容易"打分，系统自动安排下次复习时间。</li>
          </ol>
        </div>
      )}

      <div className="flex gap-1 border-b border-border">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              'flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              tab === key
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      <div>
        {tab === 'plans' && <PlansView />}
        {tab === 'quizzes' && <QuizzesView />}
        {tab === 'srs' && <SRSView />}
      </div>
    </ResourcePage>
  )
}
