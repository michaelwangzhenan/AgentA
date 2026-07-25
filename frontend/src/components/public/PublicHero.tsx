import { BookOpen, Brain, Sparkles } from 'lucide-react'

import logoUrl from '@/assets/agentA_logo.svg'

const FEATURES = [
  { icon: Brain, text: 'RAG 知识库与引用溯源' },
  { icon: BookOpen, text: '学习计划、测验与间隔复习' },
  { icon: Sparkles, text: '可扩展 Skills 与 MCP 工具链' },
] as const

export function PublicHero() {
  return (
    <div className="flex flex-col justify-center">
      <div className="flex items-center gap-3">
        <img src={logoUrl} alt="AgentA logo" className="h-12 w-12 lg:h-14 lg:w-14" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight lg:text-3xl">AgentA</h1>
          <p className="text-sm text-muted-foreground">个人 AI 学习助手</p>
        </div>
      </div>

      <p className="mt-6 text-sm leading-relaxed text-muted-foreground lg:mt-8 lg:text-base">
        面向学习与研究的知识库智能体：对话、知识检索、学习规划与技能扩展，多模型可切换。
      </p>

      <ul className="mt-6 space-y-2.5 lg:mt-8 lg:space-y-3">
        {FEATURES.map(({ icon: Icon, text }) => (
          <li key={text} className="flex items-center gap-3 text-sm text-foreground/90">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Icon className="h-4 w-4" />
            </span>
            {text}
          </li>
        ))}
      </ul>
    </div>
  )
}
