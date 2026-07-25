import { Link } from 'react-router-dom'

import { useAuth } from '@/lib/auth'

/** role=user 登录后顶部提示：引导申请 admin 权限体验完整功能。 */
export function UserFeatureBanner() {
  const { isAdmin } = useAuth()
  if (isAdmin) return null

  return (
    <div
      role="status"
      className="shrink-0 border-b border-border bg-muted/40 px-4 py-2 text-center text-xs text-muted-foreground"
    >
      如需体验完整功能，请
      <Link
        to="/contact"
        className="mx-1 text-foreground underline underline-offset-2 hover:text-primary"
      >
        联系我们
      </Link>
      申请管理员权限
    </div>
  )
}
