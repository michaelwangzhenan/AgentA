import { Link } from 'react-router-dom'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { cn } from '@/lib/utils'

const linkClass =
  'font-medium text-primary underline underline-offset-2 transition-colors hover:text-primary/80'

const COMPONENTS: Components = {
  h1: ({ node: _node, ...props }) => (
    <h1 className="mt-0 mb-4 text-2xl font-semibold tracking-tight" {...props} />
  ),
  h2: ({ node: _node, ...props }) => (
    <h2 className="mt-8 mb-3 text-lg font-semibold tracking-tight" {...props} />
  ),
  h3: ({ node: _node, ...props }) => (
    <h3 className="mt-6 mb-2 text-base font-semibold" {...props} />
  ),
  ul: ({ node: _node, ...props }) => <ul className="my-3 list-disc space-y-1.5 pl-5" {...props} />,
  ol: ({ node: _node, ...props }) => (
    <ol className="my-3 list-decimal space-y-1.5 pl-5" {...props} />
  ),
  li: ({ node: _node, ...props }) => <li className="leading-relaxed" {...props} />,
  p: ({ node: _node, ...props }) => <p className="my-3 leading-relaxed text-foreground/90" {...props} />,
  a: ({ node: _node, href, children }) => {
    if (href?.startsWith('/') && !href.startsWith('//')) {
      return (
        <Link to={href} className={linkClass}>
          {children}
        </Link>
      )
    }
    return (
      <a href={href} className={linkClass} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    )
  },
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
      <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.9em]" {...props}>
        {children}
      </code>
    )
  },
  pre: ({ node: _node, ...props }) => (
    <pre className="my-4 overflow-x-auto rounded-lg bg-muted p-4 text-sm leading-relaxed" {...props} />
  ),
  table: ({ node: _node, ...props }) => (
    <div className="my-4 overflow-x-auto">
      <table className="min-w-full border-collapse text-sm" {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => <thead className="bg-muted/50" {...props} />,
  th: ({ node: _node, ...props }) => (
    <th className="border border-border px-3 py-2 text-left font-medium" {...props} />
  ),
  td: ({ node: _node, ...props }) => (
    <td className="border border-border px-3 py-2 align-top" {...props} />
  ),
  blockquote: ({ node: _node, ...props }) => (
    <blockquote className="my-4 border-l-2 border-border pl-4 text-muted-foreground" {...props} />
  ),
  hr: ({ node: _node, ...props }) => <hr className="my-6 border-border" {...props} />,
  strong: ({ node: _node, ...props }) => <strong className="font-semibold text-foreground" {...props} />,
}

export type StaticMarkdownContentProps = {
  source: string
  className?: string
}

export function StaticMarkdownContent({ source, className }: StaticMarkdownContentProps) {
  if (!source.trim()) {
    return <p className="text-sm text-muted-foreground">（正文为空）</p>
  }

  return (
    <div className={cn('text-sm leading-relaxed sm:text-[15px]', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {source}
      </ReactMarkdown>
    </div>
  )
}
