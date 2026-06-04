import type { ReactNode } from 'react'

export type ResourcePageProps = {
  title: string
  subtitle?: string
  toolbar?: ReactNode
  children: ReactNode
}

export function ResourcePage({ title, subtitle, toolbar, children }: ResourcePageProps) {
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
        <div className="mx-auto max-w-4xl space-y-4">{children}</div>
      </div>
    </div>
  )
}
