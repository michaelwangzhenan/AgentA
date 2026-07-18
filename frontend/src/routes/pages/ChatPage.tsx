import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { ChatView } from '@/components/chat/ChatView'
import { useAppLayout } from '@/routes/AppLayoutContext'
import { chatPath } from '@/routes/paths'

export function ChatPage() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const {
    sessionsReady,
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
  } = useAppLayout()

  useEffect(() => {
    if (!sessionsReady || sessionId || !activeSessionId) return
    navigate(chatPath(activeSessionId), { replace: true })
  }, [sessionsReady, sessionId, activeSessionId, navigate])

  if (!sessionsReady) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }

  if (!sessionId) {
    return null
  }

  return (
    <ChatView
      sessionId={activeSessionId}
      messages={messages}
      inFlight={inFlight}
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

export function ChatIndexRedirect() {
  return <ChatPage />
}
