import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Loader2 } from 'lucide-react'
import { ThinkingBlock } from './ThinkingBlock'
import { WorkBlock } from './WorkBlock'
import { cn } from '@/lib/utils'
import type { AssistantMessage, Message } from '@/types/chat'

// 自定义 markdown 元素样式：不装 @tailwindcss/typography 插件，直接用 Tailwind class
// 覆盖各 HTML 元素，体积更小、可控性更强
const MD_COMPONENTS: Components = {
  p: ({ node: _node, ...props }) => <p className="my-2 leading-relaxed" {...props} />,
  h1: ({ node: _node, ...props }) => <h1 className="mt-3 mb-2 text-lg font-semibold" {...props} />,
  h2: ({ node: _node, ...props }) => <h2 className="mt-3 mb-2 text-base font-semibold" {...props} />,
  h3: ({ node: _node, ...props }) => <h3 className="mt-2 mb-1 text-base font-medium" {...props} />,
  ul: ({ node: _node, ...props }) => <ul className="my-2 list-disc space-y-1 pl-5" {...props} />,
  ol: ({ node: _node, ...props }) => <ol className="my-2 list-decimal space-y-1 pl-5" {...props} />,
  li: ({ node: _node, ...props }) => <li className="leading-relaxed" {...props} />,
  a: ({ node: _node, ...props }) => (
    <a className="text-blue-600 underline hover:no-underline" target="_blank" rel="noreferrer" {...props} />
  ),
  code: ({ node: _node, className, children, ...props }) => {
    const isBlock = className?.startsWith('language-')
    if (isBlock) {
      return (
        <code className={cn('block', className)} {...props}>
          {children}
        </code>
      )
    }
    return (
      <code className="rounded bg-background px-1 py-0.5 font-mono text-[0.85em]" {...props}>
        {children}
      </code>
    )
  },
  pre: ({ node: _node, ...props }) => (
    <pre className="my-2 overflow-x-auto rounded bg-background p-2 text-sm leading-relaxed" {...props} />
  ),
  table: ({ node: _node, ...props }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-sm" {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => <thead className="bg-background/60" {...props} />,
  th: ({ node: _node, ...props }) => (
    <th className="border border-border px-2 py-1 text-left font-medium" {...props} />
  ),
  td: ({ node: _node, ...props }) => (
    <td className="border border-border px-2 py-1 align-top" {...props} />
  ),
  blockquote: ({ node: _node, ...props }) => (
    <blockquote className="my-2 border-l-2 border-border pl-3 text-muted-foreground" {...props} />
  ),
  hr: ({ node: _node, ...props }) => <hr className="my-3 border-border" {...props} />,
}

export function MessageBubble({ message }: { message: Message }) {
  if (message.role === 'user') {
    return <UserBubble content={message.content} />
  }
  return <AssistantBubble message={message} />
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div
        className={cn(
          'max-w-[80%] rounded-2xl bg-primary px-4 py-2 text-sm',
          'break-words whitespace-pre-wrap text-primary-foreground',
        )}
      >
        {content}
      </div>
    </div>
  )
}

function AssistantBubble({ message }: { message: AssistantMessage }) {
  const hasAny =
    message.thinking ||
    message.plan ||
    message.toolCalls.length > 0 ||
    message.content ||
    message.error

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-1">
        {message.thinking ? (
          <ThinkingBlock text={message.thinking} streaming={message.streaming} />
        ) : null}

        <WorkBlock
          plan={message.plan}
          toolCalls={message.toolCalls}
          hasContent={!!message.content}
        />

        {message.content ? (
          <div className="rounded-2xl bg-muted px-4 py-2 text-base text-foreground break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
              {message.content}
            </ReactMarkdown>
          </div>
        ) : null}

        {message.streaming && !message.content ? (
          <div className="flex items-center gap-2 rounded-2xl bg-muted px-4 py-2 text-sm text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span>thinking…</span>
          </div>
        ) : null}

        {message.error ? (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {message.error}
          </div>
        ) : null}

        {!hasAny && !message.streaming ? (
          <div className="rounded-2xl bg-muted px-4 py-2 text-sm text-muted-foreground">
            （无文本输出）
          </div>
        ) : null}
      </div>
    </div>
  )
}
