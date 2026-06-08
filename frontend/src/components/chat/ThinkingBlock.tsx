import { useState } from 'react'
import { ChevronDown, Brain, Loader2 } from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'

type Props = {
  text: string
  thinkingMs?: number | null
  streaming?: boolean
}

export function ThinkingBlock({ text, thinkingMs, streaming }: Props) {
  const [open, setOpen] = useState(false)
  // 只有空白（换行 / 空格）的思考段也不显示 —— 部分模型在快速决定调工具的轮次只吐
  // 极少量空白 reasoning，渲染出来是个展开后为空的折叠框，纯噪声。
  if (!text.trim()) return null

  const seconds = thinkingMs != null ? Math.max(1, Math.round(thinkingMs / 1000)) : null
  const summary =
    streaming
      ? '正在思考…'
      : seconds != null
        ? `思考了 ${seconds} 秒`
        : '思考过程'

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mb-1.5">
      <CollapsibleTrigger
        className={cn(
          'flex w-full items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-1.5',
          'text-xs text-muted-foreground hover:bg-muted/70 transition-colors',
        )}
      >
        <ChevronDown
          className={cn('h-3 w-3 transition-transform', !open && '-rotate-90')}
        />
        {streaming ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Brain className="h-3 w-3" />
        )}
        <span>{summary}</span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 whitespace-pre-wrap rounded-md border border-dashed border-border bg-muted/20 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          {text}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
