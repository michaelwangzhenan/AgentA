import { useCallback, useEffect, useRef, useState } from 'react'
import { loadSessionMessages, streamChat, truncateSession } from '@/api/client'
import type {
  AssistantMessage,
  AssistantVersion,
  Message,
  PlanStepStatus,
  ToolCallState,
  UserMessage,
} from '@/types/chat'
import { backendMessagesToFrontend } from '@/types/session'

function newAssistantMessage(): AssistantMessage {
  return {
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '',
    thinking: '',
    thinkingMs: null,
    plan: null,
    toolCalls: [],
    error: null,
    streaming: true,
    createdAt: Date.now(),
  }
}

/** 流结束（关闭 / 出错 / 用户中止）时，把仍在"进行中"的工具收敛成失败态，避免永远转圈 */
function endRunningTools(calls: ToolCallState[]): ToolCallState[] {
  if (!calls.some((c) => c.status === 'running')) return calls
  return calls.map((c) =>
    c.status === 'running' ? { ...c, status: 'error' as const } : c,
  )
}

function snapshot(m: AssistantMessage): AssistantVersion {
  return {
    content: m.content,
    thinking: m.thinking,
    thinkingMs: m.thinkingMs,
    plan: m.plan,
    toolCalls: m.toolCalls,
    error: m.error,
  }
}

/** 统计 messages 中某条 user 消息之前有几条 user 消息（= 它的 0 基 user 序号） */
function userOrdinal(messages: Message[], userId: string): number {
  let n = 0
  for (const m of messages) {
    if (m.role === 'user') {
      if (m.id === userId) return n
      n += 1
    }
  }
  return -1
}

type Options = {
  sessionId: string | null
  /** 一轮交互结束（含失败 / 中止）后回调，App 用来刷新 session 列表 */
  onSettled?: () => void
}

export function useChat({ sessionId, onSettled }: Options) {
  const [messages, setMessages] = useState<Message[]>([])
  const [inFlight, setInFlight] = useState(false)
  const streamCtrlRef = useRef<AbortController | null>(null)
  // 始终拿到最新 messages，供 regenerate / editResend 同步读取
  const messagesRef = useRef<Message[]>([])
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  // ─── 切 session：abort 旧流 + 拉历史 ───────────────────────────────────
  useEffect(() => {
    if (streamCtrlRef.current) {
      streamCtrlRef.current.abort()
      streamCtrlRef.current = null
    }
    if (!sessionId) {
      setMessages([])
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const resp = await loadSessionMessages(sessionId)
        if (!cancelled) setMessages(backendMessagesToFrontend(resp.messages))
      } catch (e) {
        console.error('[useChat] 拉 session messages 失败', e)
        if (!cancelled) setMessages([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sessionId])

  // ─── 流式核心：把一段文本流进指定 assistant 消息 ───────────────────────
  const streamInto = useCallback(
    async (text: string, assistantId: string, sid: string) => {
      setInFlight(true)
      const ctrl = new AbortController()
      streamCtrlRef.current = ctrl

      // thinking 计时：首个 thinking_chunk 起记，流式中持续更新，结束定格
      let thinkingStart: number | null = null

      const update = (updater: (m: AssistantMessage) => AssistantMessage) => {
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
                case 'thinking_chunk': {
                  if (thinkingStart === null) thinkingStart = Date.now()
                  const elapsed = Date.now() - thinkingStart
                  update((m) => ({
                    ...m,
                    thinking: m.thinking + ev.payload.text,
                    thinkingMs: elapsed,
                  }))
                  break
                }
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
                            status:
                              (ev.payload.status as ToolCallState['status']) ?? 'ok',
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
                  update((m) => ({ ...m, error: ev.payload.message }))
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
                toolCalls: endRunningTools(m.toolCalls),
              }))
            },
            onClose() {
              update((m) => {
                const hadRunning = m.toolCalls.some((c) => c.status === 'running')
                const noOutput = !m.content && !m.error
                return {
                  ...m,
                  streaming: false,
                  toolCalls: endRunningTools(m.toolCalls),
                  // 流正常关闭却没产出任何正文 / 错误（典型：工具调用后服务端提前结束流）——
                  // 给个明确错误，避免留下一个空气泡 + 永远转圈的"进行中"工具
                  error:
                    noOutput && hadRunning
                      ? '生成未完成：工具调用未返回结果，连接已结束'
                      : m.error,
                }
              })
            },
          },
          { sessionId: sid, signal: ctrl.signal },
        )
      } catch {
        // onError 已处理
      } finally {
        if (streamCtrlRef.current === ctrl) streamCtrlRef.current = null
        setInFlight(false)
        // 流式结束后把最终态写入 versions（若该消息处于多版本模式）
        setMessages((prev) =>
          prev.map((m) => {
            if (m.role !== 'assistant' || m.id !== assistantId) return m
            if (!m.versions) return m
            const versions = [...m.versions, snapshot(m)]
            return { ...m, versions, versionIndex: versions.length - 1 }
          }),
        )
        onSettled?.()
      }
    },
    [onSettled],
  )

  // ─── 发新消息 ──────────────────────────────────────────────────────────
  const send = useCallback(
    (text: string) => {
      if (!sessionId || !text.trim()) return
      const userMsg: UserMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
        createdAt: Date.now(),
      }
      const assistantMsg = newAssistantMessage()
      setMessages((prev) => [...prev, userMsg, assistantMsg])
      void streamInto(text, assistantMsg.id, sessionId)
    },
    [sessionId, streamInto],
  )

  // ─── 停止生成 ──────────────────────────────────────────────────────────
  const stop = useCallback(() => {
    streamCtrlRef.current?.abort()
    streamCtrlRef.current = null
    setInFlight(false)
    setMessages((prev) =>
      prev.map((m) =>
        m.role === 'assistant' && m.streaming
          ? { ...m, streaming: false, toolCalls: endRunningTools(m.toolCalls) }
          : m,
      ),
    )
  }, [])

  // ─── 编辑 / 重发某条 user 消息（丢弃其后全部）────────────────────────────
  const editResend = useCallback(
    async (userId: string, newText: string) => {
      if (!sessionId || !newText.trim()) return
      const idx = messagesRef.current.findIndex((m) => m.id === userId)
      if (idx < 0) return
      const ord = userOrdinal(messagesRef.current, userId)
      if (ord < 0) return
      try {
        await truncateSession(sessionId, ord)
      } catch (e) {
        console.error('[useChat] truncate 失败', e)
        return
      }
      const assistantMsg = newAssistantMessage()
      setMessages((prev) => {
        const kept = prev.slice(0, idx)
        const editedUser: UserMessage = {
          id: crypto.randomUUID(),
          role: 'user',
          content: newText,
          createdAt: Date.now(),
        }
        return [...kept, editedUser, assistantMsg]
      })
      void streamInto(newText, assistantMsg.id, sessionId)
    },
    [sessionId, streamInto],
  )

  // ─── 重新生成某条 assistant 回答（保留旧版本可切换）──────────────────────
  const regenerate = useCallback(
    async (assistantId: string) => {
      if (!sessionId) return
      const cur = messagesRef.current
      const aIdx = cur.findIndex(
        (m) => m.role === 'assistant' && m.id === assistantId,
      )
      if (aIdx < 0) return
      // 往前找最近的 user 消息
      let uIdx = -1
      for (let i = aIdx - 1; i >= 0; i--) {
        if (cur[i].role === 'user') {
          uIdx = i
          break
        }
      }
      if (uIdx < 0) return
      const userMsg = cur[uIdx] as UserMessage
      const ord = userOrdinal(cur, userMsg.id)
      if (ord < 0) return
      try {
        await truncateSession(sessionId, ord)
      } catch (e) {
        console.error('[useChat] truncate 失败', e)
        return
      }
      // 把当前回答存进 versions（首次 regenerate 时初始化），重置正文准备重答
      setMessages((prev) => {
        const next = prev.slice(0, aIdx + 1) // 丢弃该 assistant 之后的一切
        return next.map((m) => {
          if (m.role !== 'assistant' || m.id !== assistantId) return m
          const baseVersions = m.versions ?? [snapshot(m)]
          return {
            ...m,
            versions: baseVersions,
            versionIndex: baseVersions.length - 1,
            content: '',
            thinking: '',
            thinkingMs: null,
            plan: null,
            toolCalls: [],
            error: null,
            streaming: true,
            createdAt: Date.now(),
          }
        })
      })
      void streamInto(userMsg.content, assistantId, sessionId)
    },
    [sessionId, streamInto],
  )

  // ─── 重发某条 user 消息：等同于重新生成它对应的那条 assistant 回答 ────────
  // 内容不变的"重发"语义上就是"对这轮回答 re-roll"，所以直接复用 regenerate：
  // 保留旧答案为可切换版本 + 截断该回答之后的后续轮次。
  const resendUser = useCallback(
    async (userId: string) => {
      const cur = messagesRef.current
      const uIdx = cur.findIndex((m) => m.role === 'user' && m.id === userId)
      if (uIdx < 0) return
      let assistantId: string | null = null
      for (let i = uIdx + 1; i < cur.length; i++) {
        if (cur[i].role === 'assistant') {
          assistantId = cur[i].id
          break
        }
      }
      if (!assistantId) return
      await regenerate(assistantId)
    },
    [regenerate],
  )

  // ─── 在多版本间切换（仅改变显示，不重写 DB）────────────────────────────
  const switchVersion = useCallback((assistantId: string, index: number) => {
    setMessages((prev) =>
      prev.map((m) => {
        if (m.role !== 'assistant' || m.id !== assistantId || !m.versions) return m
        if (index < 0 || index >= m.versions.length) return m
        const v = m.versions[index]
        return {
          ...m,
          ...v,
          versionIndex: index,
          streaming: false,
        }
      }),
    )
  }, [])

  return {
    messages,
    inFlight,
    send,
    stop,
    editResend,
    resendUser,
    regenerate,
    switchVersion,
  }
}
