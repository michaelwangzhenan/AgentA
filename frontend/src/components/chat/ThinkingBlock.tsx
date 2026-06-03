import { useState } from 'react'
import { ChevronDown, ChevronRight, Brain } from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'

type Props = {
  text: string
  streaming?: boolean
}

export function ThinkingBlock({ text, streaming }: Props) {
  const [open, setOpen] = useState(false)
  if (!text) return null

  const charCount = text.length

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mb-2">
      <CollapsibleTrigger
        className={cn(
          'flex w-full items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-1.5',
          'text-xs text-muted-foreground hover:bg-muted/70 transition-colors',
        )}
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        <Brain className="h-3 w-3" />
        <span>
          思考过程
          {streaming ? ' · 进行中' : ''}
          {` · ${charCount} 字`}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 whitespace-pre-wrap rounded-md border border-dashed border-border bg-muted/20 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          {text}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
