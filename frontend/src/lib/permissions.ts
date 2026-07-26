import type { PermissionScope } from '@/types/auth'
import { useAuth } from '@/lib/auth'

/** 无写权限时控件 tooltip 文案（按 scope） */
export const WRITE_DENIED_MESSAGES: Partial<Record<PermissionScope, string>> = {
  chat: '只读体验，无法发送',
  kb: '入库需管理员权限',
  memory: '只读体验，无法修改',
  usage: '修改需管理员权限',
  quality: '修改需管理员权限',
  skills: '修改需管理员权限',
  db: '修改需管理员权限',
  backup: '修改需管理员权限',
  profile: '只读体验，无法修改',
  account: '只读体验，无法修改',
  config: '修改需管理员权限',
  users: '需要主账号权限',
}

export function writeDeniedMessage(scope: PermissionScope, role?: string): string {
  if (role === 'readonly') {
    return WRITE_DENIED_MESSAGES[scope] ?? '只读体验，无法修改'
  }
  return WRITE_DENIED_MESSAGES[scope] ?? '当前账号无修改权限'
}

const WRITE_PERMISSION_DENIED = '当前账号无修改权限'

/** 是否为后端返回的写权限拒绝（client 已统一 toast，调用方勿重复提示） */
export function isWritePermissionDenied(error: unknown): boolean {
  return error instanceof Error && error.message === WRITE_PERMISSION_DENIED
}

/** Promise.allSettled 结果是否全部为写权限拒绝 */
export function allSettledAreWritePermissionDenied(
  results: PromiseSettledResult<unknown>[],
): boolean {
  const rejected = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected')
  return rejected.length > 0 && rejected.every((r) => isWritePermissionDenied(r.reason))
}

/** 非 admin 在数据库页只能看 L1 列表，不能下钻 */
export function dbDrillDeniedMessage(): string {
  return '仅管理员可查看库内详情'
}

/** 页面内写权限：allowed + tooltip 文案 */
export function useWriteScope(scope: PermissionScope) {
  const { canWrite, user } = useAuth()
  return {
    allowed: canWrite(scope),
    tip: writeDeniedMessage(scope, user?.role),
  }
}
