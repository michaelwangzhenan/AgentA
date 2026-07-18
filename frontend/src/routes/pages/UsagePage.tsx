import { Navigate, useNavigate, useParams } from 'react-router-dom'

import { UsageView } from '@/components/usage/UsageView'
import { isUsageTab, type UsageTab } from '@/routes/paths'

export function UsagePage() {
  const { tab: tabParam } = useParams()
  const navigate = useNavigate()
  const tab: UsageTab = tabParam && isUsageTab(tabParam) ? tabParam : 'mine'

  return <UsageView tab={tab} onTabChange={(next) => navigate(`/usage/${next}`)} />
}

export function UsageIndexRedirect() {
  return <Navigate to="/usage/mine" replace />
}

export function UsageTabGuard() {
  const { tab } = useParams()
  if (tab && isUsageTab(tab)) {
    return <UsagePage />
  }
  return <Navigate to="/usage/mine" replace />
}
