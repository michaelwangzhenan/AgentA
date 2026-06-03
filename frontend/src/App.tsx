import { useState } from 'react'
import { Composer } from '@/components/chat/Composer'
import { MessageList } from '@/components/chat/MessageList'
import { streamChat } from '@/api/client'
import type {
  AssistantMessage,
  Message,
  PlanStepStatus,
  ToolCallState,
  UserMessage,
} from '@/types/chat'

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
  const [messages, setMessages] = useState<Message[]>([])
  const [inFlight, setInFlight] = useState(false)

  const handleSend = async (text: string) => {
    const userMsg: UserMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
    }
    const assistantMsg = newAssistantMessage()
    const assistantId = assistantMsg.id

    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setInFlight(true)

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
      await streamChat(text, {
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
      })
    } catch {
      // streamChat 抛错（fatal）时 onError 已经更新过 message，这里只兜 unhandled rejection
    } finally {
      setInFlight(false)
    }
  }

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">AgentA</h1>
        <p className="text-xs text-muted-foreground">
          Step 2 - 流式输出 + Agent 状态
        </p>
      </header>
      <MessageList messages={messages} />
      <Composer onSend={handleSend} disabled={inFlight} />
    </div>
  )
}

export default App
