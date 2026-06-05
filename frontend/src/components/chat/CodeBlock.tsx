import { useState, type ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  /** ```lang 里的 lang，无则空串 */
  language: string
  /** 代码原文，用于复制 */
  raw: string
  children: ReactNode
}

/**
 * 带语言标签 + 复制按钮的代码块外壳。
 *
 * 不做语法高亮（见 §2.2 决策记录：高亮需引重型依赖，本期只做等宽 + 配色 + 复制），
 * 仅提供可读的语言名、一键复制、以及跟正文气泡区分的凹陷配色。
 */
export function CodeBlock({ language, raw, children }: Props) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(raw)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard 不可用（非安全上下文）时静默
    }
  }

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-code-border bg-code-surface">
      <div className="flex items-center justify-between border-b border-code-border/70 px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          {language || 'code'}
        </span>
        <button
          type="button"
          onClick={copy}
          className={cn(
            'flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground',
            'transition-colors hover:bg-foreground/10 hover:text-foreground',
          )}
          aria-label="复制代码"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-green-500" /> 已复制
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" /> 复制
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 text-[13px] leading-relaxed text-code-foreground">
        {children}
      </pre>
    </div>
  )
}
