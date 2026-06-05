import { useState } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import type { PlanStep, PlanStepStatus } from '@/types/chat'

type Props = {
  steps: PlanStep[]
}

/**
 * To-dos 面板样式（参考 Cursor）：
 *   已完成 = ✓ + 灰色删除线；进行中 = → 箭头 + 文字加重；待办 = 虚线圆圈 + 常规文字。
 * 默认展开，可点标题行折叠（对齐原始需求"Plan 详情可折叠"）。
 */
export function PlanBlock({ steps }: Props) {
  const [open, setOpen] = useState(true)
  if (!steps || steps.length === 0) return null
  const done = steps.filter(
    (s) => s.status === 'success' || s.status === 'skipped',
  ).length

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="rounded-md border border-border bg-muted/30 px-3 py-2"
    >
      <CollapsibleTrigger className="flex w-full items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors">
        <ChevronDown
          className={cn('h-3 w-3 transition-transform', !open && '-rotate-90')}
        />
        <span>待办</span>
        <span className="tabular-nums">
          {done}/{steps.length}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-1.5 space-y-1">
          {steps.map((s) => (
            <li key={s.id} className="flex items-start gap-2 text-xs">
              <span className="mt-0.5 shrink-0">
                <StepIcon status={s.status} />
              </span>
              <span
                className={cn(
                  'flex-1 whitespace-pre-wrap break-words leading-5',
                  s.status === 'running' && 'font-medium text-foreground',
                  s.status === 'pending' && 'text-muted-foreground',
                  s.status === 'success' && 'text-muted-foreground line-through',
                  s.status === 'skipped' && 'text-muted-foreground line-through',
                  s.status === 'failed' && 'text-destructive',
                )}
              >
                {s.text}
                {s.note ? (
                  <span className="ml-1 text-muted-foreground/80">— {s.note}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

function StepIcon({ status }: { status: PlanStepStatus }) {
  switch (status) {
    case 'success':
      return <CheckMark />
    case 'skipped':
      return <CheckMark muted />
    case 'failed':
      return (
        <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-destructive/15 text-[9px] text-destructive">
          ✕
        </span>
      )
    case 'running':
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />
    case 'pending':
    default:
      return (
        <span className="block h-3.5 w-3.5 rounded-full border border-dashed border-muted-foreground/60" />
      )
  }
}

function CheckMark({ muted }: { muted?: boolean }) {
  return (
    <span
      className={cn(
        'flex h-3.5 w-3.5 items-center justify-center rounded-full text-[9px] text-white',
        muted ? 'bg-muted-foreground/50' : 'bg-green-600',
      )}
    >
      ✓
    </span>
  )
}
