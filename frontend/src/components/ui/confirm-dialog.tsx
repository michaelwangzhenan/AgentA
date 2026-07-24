import type { ComponentProps, ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

export function ConfirmDialogLoadingHint() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
      处理中，请稍候…
    </div>
  )
}

export function ConfirmDialogActionLabel({
  loading,
  label,
}: {
  loading: boolean
  label: ReactNode
}) {
  if (!loading) return <>{label}</>
  return (
    <span className="inline-flex items-center gap-2">
      <Loader2 className="h-4 w-4 animate-spin" />
      处理中…
    </span>
  )
}

export type ConfirmDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description?: ReactNode
  onConfirm: () => void | Promise<void>
  loading?: boolean
  confirmLabel?: ReactNode
  cancelLabel?: string
  destructive?: boolean
  contentProps?: ComponentProps<typeof AlertDialogContent>
}

/** 带加载态的确认框：确定后显示「处理中…」+ 转圈，完成前不可关闭。 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  onConfirm,
  loading = false,
  confirmLabel = '确认',
  cancelLabel = '取消',
  destructive = true,
  contentProps,
}: ConfirmDialogProps) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        if (!loading) onOpenChange(o)
      }}
    >
      <AlertDialogContent {...contentProps}>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description != null &&
            (typeof description === 'string' ? (
              <AlertDialogDescription>{description}</AlertDialogDescription>
            ) : (
              <div className="text-sm text-balance text-muted-foreground md:text-pretty">
                {description}
              </div>
            ))}
        </AlertDialogHeader>
        {loading ? <ConfirmDialogLoadingHint /> : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            className={cn(
              destructive &&
                'bg-destructive text-destructive-foreground hover:bg-destructive/90',
            )}
            disabled={loading}
            onClick={(e) => {
              e.preventDefault()
              void onConfirm()
            }}
          >
            <ConfirmDialogActionLabel loading={loading} label={confirmLabel} />
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
