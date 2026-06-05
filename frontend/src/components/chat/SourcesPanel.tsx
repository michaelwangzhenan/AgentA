import { useState } from 'react'
import { ChevronDown, BookText } from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import type { SourceLine } from './sources'

export function SourcesPanel({ sources }: { sources: SourceLine[] }) {
  const [open, setOpen] = useState(false)
  if (sources.length === 0) return null

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-2">
      <CollapsibleTrigger
        className={cn(
          'flex w-full items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-1.5',
          'text-xs text-muted-foreground hover:bg-muted/70 transition-colors',
        )}
      >
        <ChevronDown
          className={cn('h-3 w-3 transition-transform', !open && '-rotate-90')}
        />
        <BookText className="h-3 w-3" />
        <span>来源 · {sources.length}</span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol className="mt-1 space-y-1 rounded-md border border-dashed border-border bg-muted/20 px-3 py-2 text-xs">
          {sources.map((s) => (
            <li key={s.num} className="flex gap-2">
              <span className="shrink-0 font-mono text-muted-foreground">
                [{s.num}]
              </span>
              <span className="break-words text-foreground/90">{s.text}</span>
            </li>
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  )
}
