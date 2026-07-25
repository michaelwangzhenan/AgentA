import { useCallback, useEffect, useMemo, useState } from 'react'
import { matchPath, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Toaster } from 'sonner'

import { Sidebar } from '@/components/sidebar/Sidebar'
import { useChat } from '@/hooks/useChat'
import { useAuth } from '@/lib/auth'
import { useTheme } from '@/lib/theme'
import {
  createSession,
  deleteSession,
  listSessions,
  renameSession,
} from '@/api/client'
import type { Session } from '@/types/session'
import { AppLayoutProvider } from '@/routes/AppLayoutContext'
import { chatPath, masteryPath, type MasteryTab } from '@/routes/paths'

export function AppLayout() {
  const { theme } = useTheme()
  const { user, isAdmin, isSuperAdmin, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()

  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionsReady, setSessionsReady] = useState(false)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions())
    } catch (e) {
      console.error('[AppLayout] 刷新 sessions 失败', e)
    }
  }, [])

  // 会话初始化（唯一来源）：拉列表，空则新建
  useEffect(() => {
    let cancelled = false
    setSessionsReady(false)
    ;(async () => {
      try {
        const list = await listSessions()
        if (cancelled) return
        if (list.length === 0) {
          const created = await createSession()
          if (cancelled) return
          setSessions([created])
          setActiveSessionId(created.id)
        } else {
          setSessions(list)
          setActiveSessionId(list[0].id)
        }
      } catch (e) {
        console.error('[AppLayout] 初始化 sessions 失败', e)
      } finally {
        if (!cancelled) setSessionsReady(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const {
    messages,
    inFlight,
    hasMoreOlder,
    loadingOlder,
    loadOlderMessages,
    send,
    stop,
    editResend,
    resendUser,
    regenerate,
    switchVersion,
  } = useChat({ sessionId: activeSessionId, onSettled: refreshSessions })

  // 从网址解析并校正 sessionId
  useEffect(() => {
    if (!sessionsReady || sessions.length === 0) return

    const firstId = sessions[0].id
    const chatMatch = matchPath('/chat/:sessionId', location.pathname)
    const masteryMatch = matchPath('/mastery/:tab', location.pathname)
    const querySession = new URLSearchParams(location.search).get('session')

    if (chatMatch?.params.sessionId) {
      const sid = chatMatch.params.sessionId
      if (sessions.some((s) => s.id === sid)) {
        setActiveSessionId(sid)
      } else {
        setActiveSessionId(firstId)
        navigate(chatPath(firstId), { replace: true })
      }
      return
    }

    if (masteryMatch) {
      if (querySession) {
        if (sessions.some((s) => s.id === querySession)) {
          setActiveSessionId(querySession)
        } else {
          setActiveSessionId(firstId)
          const tab = (masteryMatch.params.tab ?? 'plans') as MasteryTab
          navigate(masteryPath(tab), { replace: true })
        }
      }
      return
    }

    // 其他路径：保持内存中的 activeSessionId，若已失效则回落首条
    setActiveSessionId((prev) => {
      if (prev && sessions.some((s) => s.id === prev)) return prev
      return firstId
    })
  }, [location.pathname, location.search, sessions, sessionsReady, navigate])

  const handleSelectSession = useCallback(
    (id: string) => {
      navigate(chatPath(id))
    },
    [navigate],
  )

  const handleCreateSession = useCallback(async () => {
    const created = await createSession()
    setSessions((prev) => [created, ...prev])
    setActiveSessionId(created.id)
    navigate(chatPath(created.id))
  }, [navigate])

  const handleRenameSession = useCallback(async (id: string, title: string) => {
    const updated = await renameSession(id, title)
    setSessions((prev) => prev.map((s) => (s.id === id ? updated : s)))
  }, [])

  const handleDeleteSession = useCallback(
    async (id: string) => {
      await deleteSession(id)
      const remaining = sessions.filter((s) => s.id !== id)
      if (remaining.length === 0) {
        const created = await createSession()
        setSessions([created])
        setActiveSessionId(created.id)
        navigate(chatPath(created.id))
        return
      }
      setSessions(remaining)
      if (id === activeSessionId) {
        const nextId = remaining[0].id
        setActiveSessionId(nextId)
        navigate(chatPath(nextId))
      }
    },
    [sessions, activeSessionId, navigate],
  )

  const layoutValue = useMemo(
    () => ({
      sessions,
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
      handleSelectSession,
      handleCreateSession,
      handleRenameSession,
      handleDeleteSession,
    }),
    [
      sessions,
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
      handleSelectSession,
      handleCreateSession,
      handleRenameSession,
      handleDeleteSession,
    ],
  )

  if (!user) return null

  return (
    <AppLayoutProvider value={layoutValue}>
      <div className="flex h-full bg-background">
        <Sidebar
          sessions={sessions}
          activeId={activeSessionId}
          username={user.username}
          isAdmin={isAdmin}
          isSuperAdmin={isSuperAdmin}
          onLogout={logout}
          onSelect={handleSelectSession}
          onCreate={handleCreateSession}
          onRename={handleRenameSession}
          onDelete={handleDeleteSession}
        />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Outlet />
        </div>
      </div>
      <Toaster
        position="bottom-right"
        richColors
        theme={
          theme === 'system'
            ? undefined
            : theme === 'dark' || theme.endsWith('-dark')
              ? 'dark'
              : 'light'
        }
      />
    </AppLayoutProvider>
  )
}
