/**
 * MarkdownPreview —— react-markdown 渲染只读预览，给 SkillsView 编辑器右侧用。
 *
 * 样式靠拢 chat MessageBubble 的渲染风格（h1-h3 / 列表 / code / table），
 * 容器套 prose 类风格的 spacing，font-size 跟编辑器一致以便对照。
 */
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'

const COMPONENTS: Components = {
  h1: ({ node: _node, ...props }) => <h1 className="mt-3 mb-2 text-base font-semibold" {...props} />,
  h2: ({ node: _node, ...props }) => <h2 className="mt-3 mb-1 text-[15px] font-semibold" {...props} />,
  h3: ({ node: _node, ...props }) => <h3 className="mt-2 mb-1 text-sm font-medium" {...props} />,
  ul: ({ node: _node, ...props }) => <ul className="my-2 list-disc space-y-1 pl-5" {...props} />,
  ol: ({ node: _node, ...props }) => <ol className="my-2 list-decimal space-y-1 pl-5" {...props} />,
  li: ({ node: _node, ...props }) => <li className="leading-relaxed" {...props} />,
  p: ({ node: _node, ...props }) => <p className="my-1.5 leading-relaxed" {...props} />,
  a: ({ node: _node, ...props }) => (
    <a className="text-blue-600 underline hover:no-underline dark:text-blue-400" target="_blank" rel="noreferrer" {...props} />
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
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.9em]" {...props}>
        {children}
      </code>
    )
  },
  pre: ({ node: _node, ...props }) => (
    <pre className="my-2 overflow-x-auto rounded bg-muted p-2 text-[13px] leading-relaxed" {...props} />
  ),
  table: ({ node: _node, ...props }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-sm" {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => <thead className="bg-muted/40" {...props} />,
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

export type MarkdownPreviewProps = {
  source: string
  className?: string
}

export function MarkdownPreview({ source, className }: MarkdownPreviewProps) {
  if (!source.trim()) {
    return <p className="text-sm italic text-muted-foreground">（正文为空）</p>
  }
  return (
    <div className={cn('text-sm leading-relaxed', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {source}
      </ReactMarkdown>
    </div>
  )
}
