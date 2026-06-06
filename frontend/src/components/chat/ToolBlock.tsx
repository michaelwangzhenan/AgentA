import { useState } from 'react'
import {
  ChevronDown,
  Wrench,
  Check,
  X,
  Loader2,
  CircleSlash,
  Globe,
  Search,
  BookOpen,
  ExternalLink,
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

const URL_RE = /https?:\/\/[^\s)<>"']+/g

/** 人类可读的动作名 + 图标（裸 tool 名对用户不友好） */
function describe(call: ToolCallState): { label: string; Icon: typeof Wrench } {
  const a = call.args ?? {}
  const q = typeof a.query === 'string' ? a.query : ''
  switch (call.name) {
    case 'web_search':
      return { label: q ? `联网搜索 “${q}”` : '联网搜索', Icon: Search }
    case 'fetch_url':
      return { label: '抓取网页', Icon: Globe }
    case 'search_knowledge':
      return { label: q ? `检索知识库 “${q}”` : '检索知识库', Icon: BookOpen }
    default:
      return { label: call.name, Icon: Wrench }
  }
}

function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function ToolBlock({ call }: Props) {
  const [open, setOpen] = useState(false)
  const { label, Icon } = describe(call)

  const argsJson = (() => {
    try {
      return JSON.stringify(call.args, null, 2)
    } catch {
      return String(call.args)
    }
  })()

  const links = (call.preview?.match(URL_RE) ?? [])
    .filter((v, i, arr) => arr.indexOf(v) === i)
    .slice(0, 12)
  const isWebTool = call.name === 'web_search' || call.name === 'fetch_url'

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mb-1.5">
      <CollapsibleTrigger
        className={cn(
          'flex w-full items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-1.5',
          'text-xs hover:bg-muted/70 transition-colors',
        )}
      >
        <ChevronDown
          className={cn('h-3 w-3 transition-transform', !open && '-rotate-90')}
        />
        <Icon className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="truncate font-medium">{label}</span>
        <span className="ml-auto flex items-center gap-1 text-muted-foreground">
          <StatusIcon status={call.status} />
          {call.status === 'running'
            ? '进行中'
            : call.status === 'error'
              ? '失败'
              : call.status === 'empty'
                ? '空结果'
                : null}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 space-y-2 rounded-md border border-dashed border-border bg-muted/20 px-3 py-2 text-xs">
          {/* 网页工具：抽取链接成可点列表 */}
          {isWebTool && links.length > 0 ? (
            <div className="space-y-1">
              <div className="text-muted-foreground">
                {links.length} 条结果
              </div>
              <div className="max-h-48 space-y-0.5 overflow-y-auto">
                {links.map((url) => (
                  <a
                    key={url}
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-2 rounded px-1.5 py-1 hover:bg-foreground/5"
                  >
                    <img
                      src={`https://www.google.com/s2/favicons?domain=${domainOf(url)}&sz=32`}
                      alt=""
                      className="h-3.5 w-3.5 shrink-0 rounded-sm"
                    />
                    <span className="truncate text-foreground/90">{url}</span>
                    <span className="ml-auto shrink-0 text-muted-foreground">
                      {domainOf(url)}
                    </span>
                    <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
                  </a>
                ))}
              </div>
            </div>
          ) : null}

          {/* 参数 */}
          <div>
            <div className="mb-1 text-muted-foreground">参数</div>
            <pre className="overflow-x-auto rounded bg-code-surface px-2 py-1.5 font-mono text-[11px] leading-relaxed text-code-foreground">
              {argsJson}
            </pre>
          </div>

          {/* 结果预览 */}
          {call.preview ? (
            <div>
              <div className="mb-1 text-muted-foreground">结果预览</div>
              <pre className="max-h-60 overflow-auto rounded bg-code-surface px-2 py-1.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-code-foreground">
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
