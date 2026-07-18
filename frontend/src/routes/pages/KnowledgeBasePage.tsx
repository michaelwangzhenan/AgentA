import { useNavigate, useParams } from 'react-router-dom'

import { KnowledgeBaseView } from '@/components/kb/KnowledgeBaseView'
import { kbPath, qualityGoldenPath } from '@/routes/paths'

export function KnowledgeBasePage() {
  const { alias } = useParams()
  const navigate = useNavigate()

  return (
    <KnowledgeBaseView
      alias={alias}
      onOpenLibrary={(a) => navigate(kbPath(a))}
      onBackToLibraries={() => navigate('/kb')}
      onOpenGolden={(docId, label, fromAlias) =>
        navigate(
          qualityGoldenPath({ docId, fromAlias, docLabel: label }),
        )
      }
      onGotoGolden={() => navigate('/quality/golden')}
    />
  )
}

export function KnowledgeBaseIndexPage() {
  return <KnowledgeBasePage />
}
