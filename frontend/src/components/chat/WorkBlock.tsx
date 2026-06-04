import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import { PlanBlock } from './PlanBlock'
import { ToolBlock } from './ToolBlock'
import type { PlanStep, ToolCallState } from '@/types/chat'

type Props = {
  plan: PlanStep[] | null
  toolCalls: ToolCallState[]
  /** 是否已有正文输出。用于"正文出现后自动折叠工作过程"。 */
  hasContent: boolean
}

/**
 * 包裹 Plan + 工具调用列表的外层折叠容器（"工作过程"）。
 *
 * 行为：
 *   - 用户未手动 toggle 过：跟随 `hasContent` —— 生成中展开、正文出现后折叠
 *   - 用户点过一次：进入手动模式，尊重用户选择，不再受 `hasContent` 影响
 */
export function WorkBlock({ plan, toolCalls, hasContent }: Props) {
  const [userToggled, setUserToggled] = useState(false)
  const [manualCollapsed, setManualCollapsed] = useState(false)

  const collapsed = userToggled ? manualCollapsed : hasContent

  const handleOpenChange = (open: boolean) => {
    setManualCollapsed(!open)
    setUserToggled(true)
  }

  const hasPlan = !!plan && plan.length > 0
  const toolCount = toolCalls.length
  if (!hasPlan && toolCount === 0) return null

  const summary = [
    hasPlan ? 'Plan' : null,
    toolCount > 0 ? `${toolCount} 个工具调用` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <Collapsible
      open={!collapsed}
      onOpenChange={handleOpenChange}
      className="mb-2"
    >
      <CollapsibleTrigger
        className={cn(
          'flex w-full items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-1.5',
          'text-xs text-muted-foreground hover:bg-muted/60 transition-colors',
        )}
      >
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 transition-transform',
            collapsed && '-rotate-90',
          )}
        />
        <span className="font-medium">工作过程</span>
        <span className="text-muted-foreground/60">·</span>
        <span>{summary}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-1">
        {hasPlan ? <PlanBlock steps={plan!} /> : null}
        {toolCalls.map((call) => (
          <ToolBlock key={call.call_id} call={call} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  )
}
