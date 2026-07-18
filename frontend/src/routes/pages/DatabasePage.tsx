import { Navigate, useNavigate, useParams } from 'react-router-dom'

import { DatabaseView } from '@/components/admin/DatabaseView'
import { isDatabaseTab, type DatabaseTab } from '@/routes/paths'

export function DatabasePage() {
  const { tab: tabParam } = useParams()
  const navigate = useNavigate()
  const tab: DatabaseTab =
    tabParam && isDatabaseTab(tabParam) ? tabParam : 'chroma'

  return (
    <DatabaseView tab={tab} onTabChange={(next) => navigate(`/database/${next}`)} />
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
