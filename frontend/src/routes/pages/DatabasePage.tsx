import { Navigate, useNavigate, useParams } from 'react-router-dom'

import { DatabaseView } from '@/components/admin/DatabaseView'
import { databasePath, isDatabaseTab, type DatabaseTab } from '@/routes/paths'

export function DatabasePage() {
  const { tab: tabParam, '*': rest } = useParams()
  const navigate = useNavigate()
  const tab: DatabaseTab =
    tabParam && isDatabaseTab(tabParam) ? tabParam : 'chroma'

  const parts = (rest ?? '').split('/').filter(Boolean)
  const seg1 = parts[0] ? decodeURIComponent(parts[0]) : undefined
  const seg2 = parts[1] ? decodeURIComponent(parts[1]) : undefined

  return (
    <DatabaseView
      tab={tab}
      seg1={seg1}
      seg2={seg2}
      onTabChange={(next) => navigate(databasePath(next))}
      onPathChange={(path) => navigate(path)}
    />
  )
}

export function DatabaseIndexRedirect() {
  return <Navigate to="/database/chroma" replace />
}

export function DatabaseTabGuard() {
  const { tab } = useParams()
  if (tab && isDatabaseTab(tab)) {
    return <DatabasePage />
  }
  return <Navigate to="/database/chroma" replace />
}
