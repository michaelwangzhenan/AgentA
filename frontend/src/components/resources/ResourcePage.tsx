import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export type ResourcePageProps = {
  title: string
  subtitle?: string
  toolbar?: ReactNode
  children: ReactNode
  // 内容区最大宽度，默认 max-w-4xl；内容更宽的页（如评估报告）可单独放宽
  maxWidthClassName?: string
}

export function ResourcePage({
  title,
  subtitle,
  toolbar,
  children,
  maxWidthClassName = 'max-w-4xl',
}: ResourcePageProps) {
  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="flex items-start justify-between border-b border-border px-6 py-3">
        <div>
          <h1 className="text-base font-semibold tracking-tight">{title}</h1>
          {subtitle && (
            <p className="text-xs text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {toolbar && <div className="flex items-center gap-2">{toolbar}</div>}
      </header>
      <div className="flex-1 overflow-y-auto p-6">
        <div className={cn('mx-auto flex h-full flex-col space-y-4', maxWidthClassName)}>
          {children}
        </div>
      </div>
    </div>
  )
}
