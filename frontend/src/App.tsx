import { useCallback, useEffect, useState } from 'react'
import { Toaster } from 'sonner'
import { ChatView } from '@/components/chat/ChatView'
import { KnowledgeBaseView } from '@/components/kb/KnowledgeBaseView'
import { MCPView } from '@/components/resources/MCPView'
import { MemoryView } from '@/components/resources/MemoryView'
import { RulesView } from '@/components/resources/RulesView'
import { SkillsView } from '@/components/resources/SkillsView'
import { MasteryView } from '@/components/business/MasteryView'
import { UsageView } from '@/components/usage/UsageView'
import { QualityView } from '@/components/eval/QualityView'
import { DatabaseView } from '@/components/admin/DatabaseView'
import { BackupView } from '@/components/admin/BackupView'
import { SettingsPage } from '@/components/settings/SettingsPage'
import { LoginView } from '@/components/auth/LoginView'
import { Sidebar, type ViewKind } from '@/components/sidebar/Sidebar'
import { useTheme } from '@/lib/theme'
import { useAuth } from '@/lib/auth'
import { useChat } from '@/hooks/useChat'
import {
  createSession,
  deleteSession,
  listSessions,
  renameSession,
} from '@/api/client'
import type { Session } from '@/types/session'

function App() {
  const { theme } = useTheme()
  const { user, loading: authLoading, isAdmin, logout } = useAuth()
  const [activeView, setActiveView] = useState<ViewKind>('chat')
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  // 知识库 → 质量看板 Golden 管理跨页跳转：带上要筛选的来源文档 + 来源库（用于返回）
  const [goldenJump, setGoldenJump] = useState<
    { docId: string; label: string; fromAlias?: string } | null
  >(null)
  // 返回时让知识库重新打开到某个库的 L2（一次性，消费后清空）
  const [kbReturn, setKbReturn] = useState<string | null>(null)
  // 打开质量看板 Golden 管理 tab 的一次性信号（递增触发；入库完成 toast 点链接用）
  const [goldenTabSignal, setGoldenTabSignal] = useState(0)

  // 发送后刷新 session 列表（首条消息会回填标题、更新排序）
  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions())
    } catch (e) {
      console.error('[App] 刷新 sessions 失败', e)
    }
  }, [])

  // 消息流、收发、停止、编辑重发、重生成、版本切换全部托管给 useChat
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

  // ─── 首屏：登录后拉 sessions，空则自动建一个 ───────────────────────────
  useEffect(() => {
    if (!user) return
    let cancelled = false
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
        console.error('[App] 初始化 sessions 失败', e)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [user])

  // ─── Sidebar 回调 ─────────────────────────────────────────────────────
  const handleSelect = useCallback((id: string) => {
    setActiveSessionId(id)
  }, [])

  const handleCreate = useCallback(async () => {
    const created = await createSession()
    setSessions((prev) => [created, ...prev])
    setActiveSessionId(created.id)
  }, [])

  const handleRename = useCallback(async (id: string, title: string) => {
    const updated = await renameSession(id, title)
    setSessions((prev) => prev.map((s) => (s.id === id ? updated : s)))
  }, [])

  const handleDelete = useCallback(
    async (id: string) => {
      await deleteSession(id)
      const remaining = sessions.filter((s) => s.id !== id)
      setSessions(remaining)
      if (id === activeSessionId) {
        if (remaining.length > 0) {
          setActiveSessionId(remaining[0].id)
        } else {
          const created = await createSession()
          setSessions([created])
          setActiveSessionId(created.id)
        }
      }
    },
    [sessions, activeSessionId],
  )

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }

  if (!user) {
    return <LoginView />
  }

  return (
    <div className="flex h-screen bg-background">
      <Sidebar
        sessions={sessions}
        activeId={activeSessionId}
        activeView={activeView}
        username={user.username}
        isAdmin={isAdmin}
        onLogout={logout}
        onSelect={handleSelect}
        onCreate={handleCreate}
        onRename={handleRename}
        onDelete={handleDelete}
        onSwitchView={setActiveView}
      />
      {activeView === 'chat' && (
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
      )}
      {activeView === 'kb' && (
        <KnowledgeBaseView
          returnToAlias={kbReturn}
          onReturnConsumed={() => setKbReturn(null)}
          onOpenGolden={(docId, label, alias) => {
            setGoldenJump({ docId, label, fromAlias: alias })
            setActiveView('quality')
          }}
          onGotoGolden={() => {
            setGoldenTabSignal((s) => s + 1)
            setActiveView('quality')
          }}
        />
      )}
      {activeView === 'memory' && <MemoryView />}
      {activeView === 'rules' && <RulesView />}
      {activeView === 'skills' && isAdmin && <SkillsView />}
      {activeView === 'mcp' && isAdmin && <MCPView />}
      {activeView === 'mastery' && (
        <MasteryView
          sessionId={activeSessionId}
          sessions={sessions}
          messages={messages}
          inFlight={inFlight}
          onSelectSession={handleSelect}
          onCreateSession={handleCreate}
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
      )}
      {activeView === 'usage' && <UsageView />}
      {activeView === 'quality' && (
        <QualityView
          goldenJump={goldenJump}
          goldenTabSignal={goldenTabSignal}
          onGoldenJumpConsumed={() => setGoldenJump(null)}
          onBackToKb={() => {
            setKbReturn(goldenJump?.fromAlias ?? null)
            setGoldenJump(null)
            setActiveView('kb')
          }}
        />
      )}
      {activeView === 'dbshow' && isAdmin && <DatabaseView />}
      {activeView === 'backup' && isAdmin && <BackupView />}
      {activeView === 'settings' && <SettingsPage />}
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
    </div>
  )
}

export default App
