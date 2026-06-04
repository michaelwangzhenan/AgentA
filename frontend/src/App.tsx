import { useCallback, useEffect, useRef, useState } from 'react'
import { Toaster } from 'sonner'
import { ChatView } from '@/components/chat/ChatView'
import { KnowledgeBaseView } from '@/components/kb/KnowledgeBaseView'
import { MCPView } from '@/components/resources/MCPView'
import { MemoryView } from '@/components/resources/MemoryView'
import { RulesView } from '@/components/resources/RulesView'
import { SkillsView } from '@/components/resources/SkillsView'
import { PlansView } from '@/components/business/PlansView'
import { QuizzesView } from '@/components/business/QuizzesView'
import { SRSView } from '@/components/business/SRSView'
import { SettingsView } from '@/components/settings/SettingsView'
import { Sidebar, type ViewKind } from '@/components/sidebar/Sidebar'
import { useTheme } from '@/lib/theme'
import {
  createSession,
  deleteSession,
  listSessions,
  loadSessionMessages,
  renameSession,
  streamChat,
} from '@/api/client'
import type {
  AssistantMessage,
  Message,
  PlanStepStatus,
  ToolCallState,
  UserMessage,
} from '@/types/chat'
import type { Session } from '@/types/session'
import { backendMessagesToFrontend } from '@/types/session'

function newAssistantMessage(): AssistantMessage {
  return {
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '',
    thinking: '',
    plan: null,
    toolCalls: [],
    error: null,
    streaming: true,
  }
}

function App() {
  const { theme } = useTheme()
  const [activeView, setActiveView] = useState<ViewKind>('chat')
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [inFlight, setInFlight] = useState(false)
  // 当前正在跑的 SSE 流的 AbortController；切 session / 组件卸载时主动 abort，
  // 避免老流继续消耗后端 token + 触发 update 到已不在 messages 的 assistantId。
  const streamCtrlRef = useRef<AbortController | null>(null)

  // ─── 首屏：拉 sessions，空则自动建一个 ─────────────────────────────────
  useEffect(() => {
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
  }, [])

  // ─── 切 active session 时拉历史 ───────────────────────────────────────
  useEffect(() => {
    // session 切换时主动取消上一个 session 的在跑 stream
    if (streamCtrlRef.current) {
      streamCtrlRef.current.abort()
      streamCtrlRef.current = null
    }
    if (!activeSessionId) {
      setMessages([])
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const resp = await loadSessionMessages(activeSessionId)
        if (cancelled) return
        setMessages(backendMessagesToFrontend(resp.messages))
      } catch (e) {
        console.error('[App] 拉 session messages 失败', e)
        if (!cancelled) setMessages([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [activeSessionId])

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

  // ─── 发消息（流式）────────────────────────────────────────────────────
  const handleSend = async (text: string) => {
    if (!activeSessionId) return
    const userMsg: UserMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
    }
    const assistantMsg = newAssistantMessage()
    const assistantId = assistantMsg.id

    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setInFlight(true)

    const ctrl = new AbortController()
    streamCtrlRef.current = ctrl

    const update = (
      updater: (m: AssistantMessage) => AssistantMessage,
    ): void => {
      setMessages((prev) =>
        prev.map((m) =>
          m.role === 'assistant' && m.id === assistantId ? updater(m) : m,
        ),
      )
    }

    try {
      await streamChat(
        text,
        {
          onEvent(ev) {
            switch (ev.type) {
              case 'thinking_chunk':
                update((m) => ({
                  ...m,
                  thinking: m.thinking + ev.payload.text,
                }))
                break
              case 'token_chunk':
                update((m) => ({ ...m, content: m.content + ev.payload.text }))
                break
              case 'tool_call_start':
                update((m) => ({
                  ...m,
                  toolCalls: [
                    ...m.toolCalls,
                    {
                      call_id: ev.payload.call_id,
                      name: ev.payload.name,
                      args: ev.payload.args,
                      status: 'running',
                    },
                  ],
                }))
                break
              case 'tool_call_end':
                update((m) => ({
                  ...m,
                  toolCalls: m.toolCalls.map((c) =>
                    c.call_id === ev.payload.call_id
                      ? {
                          ...c,
                          status: (ev.payload.status as ToolCallState['status']) ?? 'ok',
                          preview: ev.payload.preview,
                        }
                      : c,
                  ),
                }))
                break
              case 'plan_created':
                update((m) => ({
                  ...m,
                  plan: ev.payload.steps.map((s) => ({
                    id: s.id,
                    text: s.text,
                    status: 'pending' as PlanStepStatus,
                  })),
                }))
                break
              case 'plan_step_start':
                update((m) => ({
                  ...m,
                  plan:
                    m.plan?.map((s) =>
                      s.id === ev.payload.step_id
                        ? { ...s, status: 'running' as PlanStepStatus }
                        : s,
                    ) ?? null,
                }))
                break
              case 'plan_step_end':
                update((m) => ({
                  ...m,
                  plan:
                    m.plan?.map((s) =>
                      s.id === ev.payload.step_id
                        ? {
                            ...s,
                            status:
                              (ev.payload.status as PlanStepStatus) ?? 'success',
                            note: ev.payload.note,
                          }
                        : s,
                    ) ?? null,
                }))
                break
              case 'final_answer':
                update((m) => ({
                  ...m,
                  content: m.content || ev.payload.text,
                  streaming: false,
                }))
                break
              case 'error':
                update((m) => ({
                  ...m,
                  error: ev.payload.message,
                }))
                break
              case 'info':
                break
            }
          },
          onError(err) {
            update((m) => ({
              ...m,
              error: m.error ?? `连接错误：${err.message}`,
              streaming: false,
            }))
          },
          onClose() {
            update((m) => ({ ...m, streaming: false }))
          },
        },
        { sessionId: activeSessionId, signal: ctrl.signal },
      )
    } catch {
      // streamChat 抛错（fatal / abort）时 onError 已经更新过 message
    } finally {
      // 仅当当前 ref 还是本次的 controller 时才清空（避免清掉新发起的流）
      if (streamCtrlRef.current === ctrl) {
        streamCtrlRef.current = null
      }
      setInFlight(false)
      try {
        const list = await listSessions()
        setSessions(list)
      } catch (e) {
        console.error('[App] 发送后刷 sessions 失败', e)
      }
    }
  }

  return (
    <div className="flex h-screen bg-background">
      <Sidebar
        sessions={sessions}
        activeId={activeSessionId}
        activeView={activeView}
        onSelect={handleSelect}
        onCreate={handleCreate}
        onRename={handleRename}
        onDelete={handleDelete}
        onSwitchView={setActiveView}
      />
      {activeView === 'chat' && (
        <ChatView messages={messages} inFlight={inFlight} onSend={handleSend} />
      )}
      {activeView === 'kb' && <KnowledgeBaseView />}
      {activeView === 'memory' && <MemoryView />}
      {activeView === 'rules' && <RulesView />}
      {activeView === 'skills' && <SkillsView />}
      {activeView === 'mcp' && <MCPView />}
      {activeView === 'plans' && <PlansView />}
      {activeView === 'quizzes' && <QuizzesView />}
      {activeView === 'srs' && <SRSView />}
      {activeView === 'settings' && <SettingsView />}
      <Toaster
        position="bottom-right"
        richColors
        theme={theme === 'system' ? undefined : theme}
      />
    </div>
  )
}

export default App
