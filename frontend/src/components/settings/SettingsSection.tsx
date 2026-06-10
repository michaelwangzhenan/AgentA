import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** 设置页统一的"框"：带标题 + 说明的卡片，把同一类配置框在一起。
 *  danger 用于注销账号等破坏性操作的红色样式。 */
export function SettingsSection({
  title,
  description,
  danger,
  className,
  children,
}: {
  title?: string
  description?: ReactNode
  danger?: boolean
  className?: string
  children: ReactNode
}) {
  const hasHeader = Boolean(title || description)
  return (
    <section
      className={cn(
        'rounded-lg border p-4',
        danger ? 'border-destructive/40 bg-destructive/5' : 'border-border bg-card',
        className,
      )}
    >
      {title && (
        <h3 className={cn('text-sm font-semibold', danger && 'text-destructive')}>{title}</h3>
      )}
      {description && (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p>
      )}
      <div className={cn(hasHeader && 'mt-3')}>{children}</div>
    </section>
  )
}
