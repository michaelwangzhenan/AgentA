import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import logoUrl from '@/assets/agentA_logo.svg'
import { buttonVariants } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'

type StaticPageShellProps = {
  children: ReactNode
  showLogo?: boolean
  title?: string
  showContactLink?: boolean
}

function PublicBrandHeader() {
  return (
    <div className="flex items-center gap-3 border-b border-border pb-6">
      <img src={logoUrl} alt="AgentA logo" className="h-10 w-10" />
      <div>
        <p className="text-lg font-semibold tracking-tight">AgentA</p>
        <p className="text-sm text-muted-foreground">个人 AI 学习助手</p>
      </div>
    </div>
  )
}

export function StaticPageShell({
  children,
  showLogo = true,
  title,
  showContactLink = true,
}: StaticPageShellProps) {
  const { user } = useAuth()

  return (
    <div className="h-full min-h-0 overflow-y-auto px-4 py-8 pb-12">
      <article className="mx-auto w-full max-w-3xl space-y-6 rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-8">
        {showLogo ? <PublicBrandHeader /> : null}

        {!showLogo && title ? (
          <header>
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          </header>
        ) : null}

        {showLogo && title ? (
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        ) : null}

        {children}

        <div className="flex flex-wrap gap-2 border-t border-border pt-6">
          {user ? (
            <Link to="/chat" className={cn(buttonVariants({ variant: 'default' }))}>
              进入应用
            </Link>
          ) : (
            <Link to="/" className={cn(buttonVariants({ variant: 'default' }))}>
              立即体验
            </Link>
          )}
          {showContactLink ? (
            <Link to="/contact" className={cn(buttonVariants({ variant: 'outline' }))}>
              联系我们
            </Link>
          ) : null}
        </div>
      </article>
    </div>
  )
}
