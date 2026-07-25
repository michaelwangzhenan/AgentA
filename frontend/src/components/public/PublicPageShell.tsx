import type { ReactNode } from 'react'

type PublicPageShellProps = {
  aside: ReactNode
  children: ReactNode
}

/** 登录 / 联系页共用外壳：单卡片双栏，整体居中。 */
export function PublicPageShell({ aside, children }: PublicPageShellProps) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center overflow-y-auto px-4 py-8 pb-12">
      <div className="w-full max-w-4xl overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
        <div className="grid lg:grid-cols-2">
          <div className="border-b border-border bg-muted/20 p-8 lg:border-r lg:border-b-0 lg:p-10">
            {aside}
          </div>
          <div className="flex items-center p-8 lg:p-10">{children}</div>
        </div>
      </div>
    </div>
  )
}
