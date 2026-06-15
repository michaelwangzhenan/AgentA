/**
 * 全局 toast 助手 —— 包装 sonner，对外提供 success / error / info 三个动作。
 *
 * 用法：
 *   import { toast } from '@/lib/toast'
 *   toast.success('已保存')
 *   toast.error(err.message)
 */
import { toast as sonner } from 'sonner'

const DEFAULT_DURATION = 4000

// 可选项：duration=Infinity 持久不消失；closeButton 显示关闭叉；action 一个操作按钮
export type ToastOpts = {
  duration?: number
  closeButton?: boolean
  action?: { label: string; onClick: () => void }
}

export const toast = {
  success(message: string, opts?: ToastOpts): void {
    sonner.success(message, { duration: DEFAULT_DURATION, ...opts })
  },
  error(message: string, opts?: ToastOpts): void {
    sonner.error(message, { duration: 6000, ...opts })
  },
  info(message: string, opts?: ToastOpts): void {
    sonner(message, { duration: DEFAULT_DURATION, ...opts })
  },
}
