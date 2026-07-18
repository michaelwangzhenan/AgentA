import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { MasteryView } from '@/components/business/MasteryView'
import { useAppLayout } from '@/routes/AppLayoutContext'
import {
  isMasteryTab,
  masteryPath,
  type MasteryTab,
} from '@/routes/paths'

export function MasteryPage() {
  const { tab: tabParam } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const tab: MasteryTab = tabParam && isMasteryTab(tabParam) ? tabParam : 'plans'

  const {
    sessions,
    activeSessionId,
    messages,
    inFlight,
    hasMoreOlder,
    loadingOlder,
    loadOlderMessages,
    send,
    stop,
    regenerate,
    editResend,
    resendUser,
    switchVersion,
    handleCreateSession,
  } = useAppLayout()

  const urlSession = searchParams.get('session')
  const expandChatFromUrl = Boolean(urlSession)

  return (
    <MasteryView
      tab={tab}
      sessionId={activeSessionId}
      sessions={sessions}
      messages={messages}
      inFlight={inFlight}
      expandChatFromUrl={expandChatFromUrl}
      onTabChange={(next) => navigate(masteryPath(next, activeSessionId))}
      onSelectSession={(id) => navigate(masteryPath(tab, id))}
      onCreateSession={handleCreateSession}
      onSend={send}
      onStop={stop}
      onRegenerate={regenerate}
      onEditResend={editResend}
      onResendUser={resendUser}
      onSwitchVersion={switchVersion}
      hasMoreOlder={hasMoreOlder}
      loadingOlder={loadingOlder}
      onLoadOlder={loadOlderMessages}
    />
  )
}

export function MasteryIndexRedirect() {
  return <Navigate to="/mastery/plans" replace />
}

export function MasteryTabGuard() {
  const { tab } = useParams()
  if (tab && isMasteryTab(tab)) {
    return <MasteryPage />
  }
  return <Navigate to="/mastery/plans" replace />
}
