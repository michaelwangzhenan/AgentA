import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { QualityView } from '@/components/eval/QualityView'
import type { GoldenDocFilter } from '@/components/eval/GoldenManager'
import { isQualityTab, kbPath, type QualityTab } from '@/routes/paths'

export function QualityPage() {
  const { tab: tabParam } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const tab: QualityTab =
    tabParam && isQualityTab(tabParam) ? tabParam : 'trace'

  const docId = searchParams.get('docId')
  const fromAlias = searchParams.get('fromAlias')
  const docLabel = searchParams.get('docLabel')

  const goldenFilter: GoldenDocFilter | undefined = docId
    ? {
        docId,
        label: docLabel ?? docId,
        fromAlias: fromAlias ?? undefined,
      }
    : undefined

  return (
    <QualityView
      tab={tab}
      goldenFilter={goldenFilter}
      onTabChange={(next) => navigate(`/quality/${next}`)}
      onClearGoldenFilter={() => navigate('/quality/golden')}
      onBackToKb={
        fromAlias ? () => navigate(kbPath(fromAlias)) : undefined
      }
    />
  )
}

export function QualityIndexRedirect() {
  return <Navigate to="/quality/trace" replace />
}

export function QualityTabGuard() {
  const { tab } = useParams()
  if (tab && isQualityTab(tab)) {
    return <QualityPage />
  }
  return <Navigate to="/quality/trace" replace />
}
