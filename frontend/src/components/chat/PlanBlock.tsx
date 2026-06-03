import { Check, X, SkipForward, Loader2, Circle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { PlanStep, PlanStepStatus } from '@/types/chat'

type Props = {
  steps: PlanStep[]
}

export function PlanBlock({ steps }: Props) {
  if (!steps || steps.length === 0) return null

  return (
    <div className="mb-2 rounded-md border border-border bg-muted/30 px-3 py-2">
      <div className="mb-1.5 text-xs font-medium text-muted-foreground">
        📋 Plan
      </div>
      <ul className="space-y-1">
        {steps.map((s) => (
          <li
            key={s.id}
            className={cn(
              'flex items-start gap-2 text-xs',
              s.status === 'running' && 'font-medium text-foreground',
              s.status === 'pending' && 'text-muted-foreground',
              s.status === 'success' && 'text-foreground',
              s.status === 'failed' && 'text-destructive',
              s.status === 'skipped' && 'text-muted-foreground line-through',
            )}
          >
            <span className="mt-0.5 shrink-0">
              <StepIcon status={s.status} />
            </span>
            <span className="flex-1 whitespace-pre-wrap break-words">
              {s.text}
              {s.note ? (
                <span className="ml-1 text-muted-foreground">— {s.note}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function StepIcon({ status }: { status: PlanStepStatus }) {
  switch (status) {
    case 'success':
      return <Check className="h-3 w-3 text-green-600" />
    case 'failed':
      return <X className="h-3 w-3 text-destructive" />
    case 'skipped':
      return <SkipForward className="h-3 w-3 text-muted-foreground" />
    case 'running':
      return <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
    case 'pending':
    default:
      return <Circle className="h-3 w-3 text-muted-foreground" />
  }
}
