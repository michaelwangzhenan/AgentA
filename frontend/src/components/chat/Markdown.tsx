import { Children, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from './CodeBlock'
import { cn } from '@/lib/utils'

const HEX_RE = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/

/** 把 react-markdown 传进来的 children 递归拼回纯文本（用于复制代码原文） */
function toText(node: ReactNode): string {
  if (node == null || node === false) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(toText).join('')
  // React element：取它的 children
  const el = node as { props?: { children?: ReactNode } }
  if (el.props?.children !== undefined) return toText(el.props.children)
  return ''
}

// 文字配色层级：正文用柔和的 foreground/90，加粗更亮，行内代码 chip，链接蓝色。
const MD_COMPONENTS: Components = {
  p: ({ node: _node, ...props }) => (
    <p className="my-2 leading-7 text-foreground/90" {...props} />
  ),
  strong: ({ node: _node, ...props }) => (
    <strong className="font-semibold text-foreground" {...props} />
  ),
  em: ({ node: _node, ...props }) => <em className="italic" {...props} />,
  h1: ({ node: _node, ...props }) => (
    <h1 className="mt-4 mb-2 text-lg font-semibold text-foreground" {...props} />
  ),
  h2: ({ node: _node, ...props }) => (
    <h2 className="mt-4 mb-2 text-base font-semibold text-foreground" {...props} />
  ),
  h3: ({ node: _node, ...props }) => (
    <h3 className="mt-3 mb-1 text-base font-medium text-foreground" {...props} />
  ),
  ul: ({ node: _node, ...props }) => (
    <ul className="my-2 list-disc space-y-1 pl-5 text-foreground/90" {...props} />
  ),
  ol: ({ node: _node, ...props }) => (
    <ol className="my-2 list-decimal space-y-1 pl-5 text-foreground/90" {...props} />
  ),
  li: ({ node: _node, ...props }) => <li className="leading-7" {...props} />,
  a: ({ node: _node, ...props }) => (
    <a
      className="font-medium text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
      target="_blank"
      rel="noreferrer"
      {...props}
    />
  ),
  code: ({ node: _node, className, children, ...props }) => {
    const isBlock = className?.startsWith('language-')
    if (isBlock) {
      const language = className?.replace('language-', '') ?? ''
      return (
        <CodeBlock language={language} raw={toText(children)}>
          <code className={cn('font-mono', className)} {...props}>
            {children}
          </code>
        </CodeBlock>
      )
    }
    // 行内代码：若内容是 hex 颜色，前置一个色块预览
    const text = Children.toArray(children).map(toText).join('')
    const isHex = HEX_RE.test(text.trim())
    return (
      <code
        className="rounded bg-foreground/8 px-1.5 py-0.5 font-mono text-[0.85em] text-foreground"
        {...props}
      >
        {isHex ? (
          <span
            className="mr-1 inline-block h-2.5 w-2.5 translate-y-px rounded-[3px] border border-border align-baseline"
            style={{ backgroundColor: text.trim() }}
          />
        ) : null}
        {children}
      </code>
    )
  },
  // pre 透传：真正的外壳由 CodeBlock 提供，这里避免再套一层 <pre>
  pre: ({ children }) => <>{children}</>,
  table: ({ node: _node, ...props }) => (
    <div className="my-3 overflow-x-auto rounded-lg border border-border">
      <table className="min-w-full border-collapse text-sm" {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => (
    <thead className="bg-muted/60" {...props} />
  ),
  th: ({ node: _node, ...props }) => (
    <th className="border-b border-border px-3 py-1.5 text-left font-medium" {...props} />
  ),
  td: ({ node: _node, ...props }) => (
    <td className="border-b border-border/60 px-3 py-1.5 align-top" {...props} />
  ),
  blockquote: ({ node: _node, ...props }) => (
    <blockquote
      className="my-2 border-l-2 border-border pl-3 text-muted-foreground"
      {...props}
    />
  ),
  hr: ({ node: _node, ...props }) => <hr className="my-3 border-border" {...props} />,
}

export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
      {children}
    </ReactMarkdown>
  )
}
