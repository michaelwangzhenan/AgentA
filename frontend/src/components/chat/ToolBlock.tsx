import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Wrench,
  Check,
  X,
  Loader2,
  CircleSlash,
} from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import type { ToolCallState } from '@/types/chat'

type Props = {
  call: ToolCallState
}

export function ToolBlock({ call }: Props) {
  const [open, setOpen] = useState(false)

  const argsJson = (() => {
    try {
      return JSON.stringify(call.args, null, 2)
    } catch {
      return String(call.args)
    }
  })()

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mb-2">
      <CollapsibleTrigger
        className={cn(
          'flex w-full items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-1.5',
          'text-xs hover:bg-muted/70 transition-colors',
        )}
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        <Wrench className="h-3 w-3" />
        <span className="font-mono font-medium">{call.name}</span>
        <StatusIcon status={call.status} />
        <span className="ml-auto text-muted-foreground">
          {statusLabel(call.status)}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 space-y-2 rounded-md border border-dashed border-border bg-muted/20 px-3 py-2 text-xs">
          <div>
            <div className="mb-1 text-muted-foreground">参数</div>
            <pre className="overflow-x-auto rounded bg-background px-2 py-1.5 text-[11px] leading-relaxed">
              {argsJson}
            </pre>
          </div>
          {call.preview ? (
            <div>
              <div className="mb-1 text-muted-foreground">结果预览</div>
              <pre className="overflow-x-auto rounded bg-background px-2 py-1.5 text-[11px] leading-relaxed whitespace-pre-wrap">
                {call.preview}
              </pre>
            </div>
          ) : null}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function StatusIcon({ status }: { status: ToolCallState['status'] }) {
  switch (status) {
    case 'ok':
      return <Check className="h-3 w-3 text-green-600" />
    case 'error':
      return <X className="h-3 w-3 text-destructive" />
    case 'empty':
      return <CircleSlash className="h-3 w-3 text-muted-foreground" />
    case 'running':
    default:
      return <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
  }
}

function statusLabel(status: ToolCallState['status']) {
  switch (status) {
    case 'ok':
      return '成功'
    case 'error':
      return '失败'
    case 'empty':
      return '空结果'
    case 'running':
    default:
      return '进行中'
  }
}
