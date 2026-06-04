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

export const toast = {
  success(message: string): void {
    sonner.success(message, { duration: DEFAULT_DURATION })
  },
  error(message: string): void {
    sonner.error(message, { duration: 6000 })
  },
  info(message: string): void {
    sonner(message, { duration: DEFAULT_DURATION })
  },
}
