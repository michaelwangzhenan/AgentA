import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

import { ForbiddenView } from '@/components/layout/ForbiddenView'
import { useAuth } from '@/lib/auth'
import { routeRequiresAdmin, routeRequiresSuperAdmin } from '@/routes/access'

export function RouteGuard({ children }: { children: ReactNode }) {
  const { isAdmin, isSuperAdmin } = useAuth()
  const location = useLocation()

  if (routeRequiresSuperAdmin(location.pathname) && !isSuperAdmin) {
    return <ForbiddenView />
  }

  if (!isAdmin && routeRequiresAdmin(location.pathname)) {
    return <ForbiddenView />
  }

  return children
}
