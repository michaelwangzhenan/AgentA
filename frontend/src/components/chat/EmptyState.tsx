import { useEffect, useState } from 'react'
import { BookOpen, ClipboardList, CalendarRange, Layers, MessageCircle } from 'lucide-react'
import { getConfig } from '@/api/client'
import logoUrl from '@/assets/agentA_logo.svg'

type Chip = {
  icon: typeof BookOpen
  label: string
  prompt: string
}

// 贴本项目业务的分类快捷 prompt（点击填进发送框）
const CHIPS: Chip[] = [
  {
    icon: BookOpen,
    label: '知识库提问',
    prompt: '基于我的知识库，帮我解释一下：',
  },
  {
    icon: ClipboardList,
    label: '出题测验',
    prompt: '用我的知识库给我出 5 道测验题，覆盖主题：',
  },
  {
    icon: CalendarRange,
    label: '学习计划',
    prompt: '帮我制定一个学习计划，目标是：',
  },
  {
    icon: Layers,
    label: '复习卡片',
    prompt: '把下面的内容做成复习卡片：',
  },
  { icon: MessageCircle, 
    label: '自由聊天', 
    prompt: '' },
]

function greeting(): string {
  const h = new Date().getHours()
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
}

export function EmptyState() {
  const [name, setName] = useState('Michael')

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        for (const g of cfg.groups) {
          const it = g.items.find((i) => i.key === 'USER_DISPLAY_NAME')
          if (it) setName(String(it.value || 'Michael'))
        }
      })
      .catch(() => {})
  }, [])

  return (
    <div className="flex items-center justify-center">
      <img src={logoUrl} alt="AgentA" className="h-30 w-30 opacity-100" />
      <h2 className="text-4xl font-bold text-foreground/90">
        {greeting()}，{name}
      </h2>
    </div>
  )
}


export function EmptyChips({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="mt-3 flex flex-wrap justify-center gap-2">
      {CHIPS.map((c) => (
        <button
          key={c.label}
          type="button"
          onClick={() => onPick(c.prompt)}
          className="flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-foreground/30 hover:bg-muted"
        >
          <c.icon className="h-3.5 w-3.5 text-muted-foreground" />
          {c.label}
        </button>
      ))}
    </div>
  )
}
