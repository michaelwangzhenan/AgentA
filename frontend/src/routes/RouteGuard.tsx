import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

import { ForbiddenView } from '@/components/layout/ForbiddenView'
import { useAuth } from '@/lib/auth'
import {
  routeRequiresAccountSettings,
  routeRequiresSuperAdmin,
} from '@/routes/access'

export function RouteGuard({ children }: { children: ReactNode }) {
  const { isReadonly, isSuperAdmin } = useAuth()
  const location = useLocation()

  if (routeRequiresSuperAdmin(location.pathname) && !isSuperAdmin) {
    return <ForbiddenView />
  }

  if (isReadonly && routeRequiresAccountSettings(location.pathname)) {
    return <ForbiddenView />
  }

  return children
}
