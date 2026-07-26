import { Link } from 'react-router-dom'

import { useAuth } from '@/lib/auth'

/** 按 role 分档的登录后顶部提示条；admin / 主账号不显示。 */
export function RoleHintBanner() {
  const { user, isAdmin, isSuperAdmin } = useAuth()
  if (!user || isAdmin || isSuperAdmin) return null

  if (user.role === 'readonly') {
    return (
      <div
        role="status"
        className="shrink-0 border-b border-border bg-muted/40 px-4 py-2 text-center text-xs text-muted-foreground"
      >
        当前为只读体验，无法保存修改。如需使用聊天、记忆等完整功能，请
        <Link
          to="/contact"
          className="mx-1 text-foreground underline underline-offset-2 hover:text-primary"
        >
          联系我们
        </Link>
        申请账号。
      </div>
    )
  }

  return (
    <div
      role="status"
      className="shrink-0 border-b border-border bg-muted/40 px-4 py-2 text-center text-xs text-muted-foreground"
    >
      当前可编辑聊天、记忆等；知识库、Skills、系统配置、备份等仍为只读。如需入库或修改这些模块，请
      <Link
        to="/contact"
        className="mx-1 text-foreground underline underline-offset-2 hover:text-primary"
      >
        联系我们
      </Link>
      申请管理员权限。
    </div>
  )
}
