import { useCallback, useEffect, useRef, useState } from 'react'
import { loadSessionMessages, SESSION_MESSAGES_PAGE_SIZE, streamChat, truncateSession } from '@/api/client'
import type {
  AssistantMessage,
  AssistantVersion,
  ChatMode,
  Message,
  PlanStep,
  PlanStepStatus,
  ResearchAction,
  ResearchState,
  TimelineItem,
  ToolCallState,
  UserMessage,
} from '@/types/chat'
import { backendMessagesToFrontend } from '@/types/session'
import { parseUserMessage } from '@/lib/attachments'
import { generateId } from '@/lib/id'

const MAX_ASSISTANT_VERSIONS = 5
const STREAM_FLUSH_MS = 50

function newResearchState(): ResearchState {
  return { phase: 'planning', query: '', subquestions: [], subagents: [], reflect: null }
}

function newAssistantMessage(mode?: ChatMode): AssistantMessage {
  return {
    id: generateId(),
    role: 'assistant',
    content: '',
    plan: null,
    timeline: [],
    error: null,
    streaming: true,
    createdAt: Date.now(),
    research: mode === 'deep_research' ? newResearchState() : null,
  }
}

/** 流结束（关闭 / 出错 / 用户中止）时，把仍在"进行中"的工具收敛成失败态，避免永远转圈 */
function endRunningTools(timeline: TimelineItem[]): TimelineItem[] {
  if (!timeline.some((it) => it.kind === 'tool' && it.call.status === 'running')) {
    return timeline
  }
  return timeline.map((it) =>
    it.kind === 'tool' && it.call.status === 'running'
      ? { ...it, call: { ...it.call, status: 'error' as const } }
      : it,
  )
}

function hasRunningTool(timeline: TimelineItem[]): boolean {
  return timeline.some((it) => it.kind === 'tool' && it.call.status === 'running')
}

/**
 * 流结束时把仍在"进行中"的 plan 步骤收敛到终态，避免最后一步永远转圈。
 * 后端正常出最终答案时会补发 plan_step_end；这里是连接中断 / 出错 / 用户中止
 * 等后端来不及补发场景的前端兜底。
 */
function endRunningPlan(
  plan: PlanStep[] | null,
  status: PlanStepStatus,
): PlanStep[] | null {
  if (!plan || !plan.some((s) => s.status === 'running')) return plan
  return plan.map((s) => (s.status === 'running' ? { ...s, status } : s))
}

function snapshot(m: AssistantMessage): AssistantVersion {
  return {
    content: m.content,
    plan: m.plan,
    timeline: [],
    error: m.error,
    model: m.model,
    cached: m.cached,
    downgraded: m.downgraded,
  }
}

function appendVersion(
  versions: AssistantVersion[],
  snap: AssistantVersion,
): AssistantVersion[] {
  return [...versions, snap].slice(-MAX_ASSISTANT_VERSIONS)
}

/** 统计 messages 中某条 user 消息的全局 0 基 user 序号（编辑重发 / 截断用） */
function userOrdinal(messages: Message[], userId: string): number {
  const target = messages.find((m) => m.id === userId && m.role === 'user') as
    | UserMessage
    | undefined
  if (target?.userIndex != null) return target.userIndex
  let n = 0
  for (const m of messages) {
    if (m.role === 'user') {
      if (m.id === userId) return n
      n += 1
    }
  }
  return -1
}

function nextUserIndex(messages: Message[]): number {
  let max = -1
  for (const m of messages) {
    if (m.role === 'user' && m.userIndex != null) {
      max = Math.max(max, m.userIndex)
    }
  }
  return max + 1
}

type Options = {
  sessionId: string | null
  /** 一轮交互结束（含失败 / 中止）后回调，App 用来刷新 session 列表 */
  onSettled?: () => void
}

export function useChat({ sessionId, onSettled }: Options) {
  const [messages, setMessages] = useState<Message[]>([])
  const [inFlight, setInFlight] = useState(false)
  const [hasMoreOlder, setHasMoreOlder] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const streamCtrlRef = useRef<AbortController | null>(null)
  const oldestIdRef = useRef<number | null>(null)
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
      setHasMoreOlder(false)
      oldestIdRef.current = null
      return
    }
    let cancelled = false
    setHasMoreOlder(false)
    oldestIdRef.current = null
    ;(async () => {
      try {
        const resp = await loadSessionMessages(sessionId, {
          limit: SESSION_MESSAGES_PAGE_SIZE,
        })
        if (!cancelled) {
          setMessages(backendMessagesToFrontend(resp.messages))
          setHasMoreOlder(resp.has_more)
          oldestIdRef.current = resp.oldest_id
        }
      } catch (e) {
        console.error('[useChat] 拉 session messages 失败', e)
        if (!cancelled) {
          setMessages([])
          setHasMoreOlder(false)
          oldestIdRef.current = null
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const loadOlderMessages = useCallback(async () => {
    if (!sessionId || !hasMoreOlder || loadingOlder || oldestIdRef.current == null) return
    setLoadingOlder(true)
    try {
      const resp = await loadSessionMessages(sessionId, {
        limit: SESSION_MESSAGES_PAGE_SIZE,
        beforeId: oldestIdRef.current,
      })
      const older = backendMessagesToFrontend(resp.messages)
      setMessages((prev) => [...older, ...prev])
      setHasMoreOlder(resp.has_more)
      oldestIdRef.current = resp.oldest_id ?? oldestIdRef.current
    } catch (e) {
      console.error('[useChat] 加载更早消息失败', e)
    } finally {
      setLoadingOlder(false)
    }
  }, [sessionId, hasMoreOlder, loadingOlder])

  // ─── 流式核心：把一段文本流进指定 assistant 消息 ───────────────────────
  const streamInto = useCallback(
    async (
      text: string,
      assistantId: string,
      sid: string,
      mode?: ChatMode,
      skipCache?: boolean,
    ) => {
      setInFlight(true)
      const ctrl = new AbortController()
      streamCtrlRef.current = ctrl

      // 每次 agent 循环的 thinking 单独成段：thinking_chunk 连续到达时累进同一段，
      // 一旦插入工具调用就把 currentThinkingId 清空，下一批 thinking 另起一段。
      let thinkingStart: number | null = null
      let currentThinkingId: string | null = null
      let contentBuf = ''
      let flushTimer: ReturnType<typeof setTimeout> | null = null

      const flushContent = () => {
        if (!contentBuf) return
        const chunk = contentBuf
        contentBuf = ''
        update((m) => ({ ...m, content: m.content + chunk }))
      }

      const scheduleContentFlush = () => {
        if (flushTimer != null) return
        flushTimer = setTimeout(() => {
          flushTimer = null
          flushContent()
        }, STREAM_FLUSH_MS)
      }

      const update = (updater: (m: AssistantMessage) => AssistantMessage) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.role === 'assistant' && m.id === assistantId ? updater(m) : m,
          ),
        )
      }

      // 深度研究：把更新合并进 research 状态（缺省时先建基础态，兼容历史回看消息）
      const updateResearch = (fn: (r: ResearchState) => ResearchState) => {
        update((m) => ({ ...m, research: fn(m.research ?? newResearchState()) }))
      }

      try {
        await streamChat(
          text,
          {
            onEvent(ev) {
              switch (ev.type) {
                case 'thinking_chunk': {
                  const now = Date.now()
                  if (currentThinkingId === null) {
                    const segId = generateId()
                    currentThinkingId = segId
                    thinkingStart = now
                    update((m) => ({
                      ...m,
                      timeline: [
                        ...m.timeline,
                        {
                          kind: 'thinking',
                          id: segId,
                          text: ev.payload.text,
                          thinkingMs: 0,
                        },
                      ],
                    }))
                  } else {
                    const segId = currentThinkingId
                    const elapsed = thinkingStart != null ? now - thinkingStart : 0
                    update((m) => ({
                      ...m,
                      timeline: m.timeline.map((it) =>
                        it.kind === 'thinking' && it.id === segId
                          ? {
                              ...it,
                              text: it.text + ev.payload.text,
                              thinkingMs: elapsed,
                            }
                          : it,
                      ),
                    }))
                  }
                  break
                }
                case 'token_chunk':
                  contentBuf += ev.payload.text
                  scheduleContentFlush()
                  break
                case 'tool_call_start':
                  // 工具调用切断当前 thinking 段，下一批 thinking 归到下一次循环
                  currentThinkingId = null
                  update((m) => ({
                    ...m,
                    timeline: [
                      ...m.timeline,
                      {
                        kind: 'tool',
                        call: {
                          call_id: ev.payload.call_id,
                          name: ev.payload.name,
                          args: ev.payload.args,
                          status: 'running',
                        },
                      },
                    ],
                  }))
                  break
                case 'tool_progress':
                  update((m) => ({
                    ...m,
                    timeline: m.timeline.map((it) =>
                      it.kind === 'tool' && it.call.call_id === ev.payload.call_id
                        ? { ...it, call: { ...it.call, stage: ev.payload.label } }
                        : it,
                    ),
                  }))
                  break
                case 'tool_call_end':
                  update((m) => ({
                    ...m,
                    timeline: m.timeline.map((it) =>
                      it.kind === 'tool' && it.call.call_id === ev.payload.call_id
                        ? {
                            ...it,
                            call: {
                              ...it.call,
                              status:
                                (ev.payload.status as ToolCallState['status']) ?? 'ok',
                              preview: ev.payload.preview,
                              stage: undefined,
                            },
                          }
                        : it,
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
                case 'research_started':
                  updateResearch((r) => ({
                    ...r,
                    phase: 'planning',
                    query: ev.payload.query,
                  }))
                  break
                case 'research_plan':
                  updateResearch((r) => ({
                    ...r,
                    phase: 'researching',
                    subquestions: ev.payload.subquestions,
                  }))
                  break
                case 'research_subagent_start':
                  updateResearch((r) => {
                    const rest = r.subagents.filter((s) => s.sub_id !== ev.payload.sub_id)
                    return {
                      ...r,
                      phase: 'researching',
                      subagents: [
                        ...rest,
                        {
                          sub_id: ev.payload.sub_id,
                          question: ev.payload.question,
                          status: 'running' as const,
                          sources: 0,
                        },
                      ].sort((a, b) => a.sub_id - b.sub_id),
                    }
                  })
                  break
                case 'research_subagent_progress':
                  updateResearch((r) => ({
                    ...r,
                    subagents: r.subagents.map((s) => {
                      if (s.sub_id !== ev.payload.sub_id) return s
                      const base = { ...s, label: ev.payload.label, sources: ev.payload.sources }
                      const actions = [...(s.actions ?? [])]
                      if (ev.payload.action) {
                        // 新工具调用：追加一行进行中的过程
                        actions.push({
                          label: ev.payload.label,
                          detail: ev.payload.detail ?? '',
                          status: 'running',
                        })
                      } else if (ev.payload.status && actions.length > 0) {
                        // 工具结束：把最近一行过程标成结果状态
                        const last = actions[actions.length - 1]
                        actions[actions.length - 1] = {
                          ...last,
                          status: ev.payload.status as ResearchAction['status'],
                        }
                      }
                      return { ...base, actions }
                    }),
                  }))
                  break
                case 'research_subagent_end':
                  updateResearch((r) => ({
                    ...r,
                    subagents: r.subagents.map((s) =>
                      s.sub_id === ev.payload.sub_id
                        ? {
                            ...s,
                            status:
                              ev.payload.status === 'failed' ? 'failed' : 'ok',
                            sources: ev.payload.sources,
                            note: ev.payload.note,
                            label: undefined,
                          }
                        : s,
                    ),
                  }))
                  break
                case 'research_reflect':
                  updateResearch((r) => ({
                    ...r,
                    phase: 'reflecting',
                    reflect: {
                      note: ev.payload.note,
                      gap: ev.payload.gap,
                      followups: ev.payload.followups,
                    },
                  }))
                  break
                case 'research_synthesizing':
                  updateResearch((r) => ({ ...r, phase: 'synthesizing' }))
                  break
                case 'final_answer':
                  flushContent()
                  update((m) => ({
                    ...m,
                    // 深度研究：正文流式时是"被检索顺序"的原始编号，final_answer 才是
                    // 重编号后的最终稿 —— 以它为准覆盖；普通对话保留已流式好的正文。
                    content: m.research ? ev.payload.text : m.content || ev.payload.text,
                    streaming: false,
                    model: ev.payload.model ?? m.model,
                    cached: ev.payload.cached ?? false,
                    downgraded: ev.payload.downgraded ?? false,
                    research: m.research ? { ...m.research, phase: 'done' } : m.research,
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
              flushContent()
              update((m) => ({
                ...m,
                error: m.error ?? `连接错误：${err.message}`,
                streaming: false,
                timeline: endRunningTools(m.timeline),
                plan: endRunningPlan(m.plan, 'failed'),
              }))
            },
            onClose() {
              flushContent()
              update((m) => {
                const hadRunning = hasRunningTool(m.timeline)
                const noOutput = !m.content && !m.error
                // 正常完成（有正文且无错误）→ 残留的进行中步骤收敛为完成；否则记失败
                const cleanFinish = !!m.content && !m.error
                return {
                  ...m,
                  streaming: false,
                  timeline: endRunningTools(m.timeline),
                  plan: endRunningPlan(m.plan, cleanFinish ? 'success' : 'failed'),
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
          { sessionId: sid, signal: ctrl.signal, mode, skipCache },
        )
      } catch {
        // onError 已处理
      } finally {
        if (flushTimer != null) clearTimeout(flushTimer)
        flushContent()
        if (streamCtrlRef.current === ctrl) streamCtrlRef.current = null
        setInFlight(false)
        // 流式结束后把最终态写入 versions（若该消息处于多版本模式）
        setMessages((prev) =>
          prev.map((m) => {
            if (m.role !== 'assistant' || m.id !== assistantId) return m
            if (!m.versions) return m
            const versions = appendVersion(m.versions, snapshot(m))
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
    (text: string, mode?: ChatMode) => {
      if (!sessionId || !text.trim()) return
      // text 是含内联附件正文的完整消息：发给后端用全文，气泡展示只留 query + 附件卡片
      const { text: display, attachments } = parseUserMessage(text)
      const userMsg: UserMessage = {
        id: generateId(),
        role: 'user',
        content: display,
        rawContent: text,
        attachments,
        createdAt: Date.now(),
        userIndex: nextUserIndex(messagesRef.current),
      }
      const assistantMsg = newAssistantMessage(mode)
      setMessages((prev) => [...prev, userMsg, assistantMsg])
      void streamInto(text, assistantMsg.id, sessionId, mode)
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
          ? {
              ...m,
              streaming: false,
              timeline: endRunningTools(m.timeline),
              plan: endRunningPlan(m.plan, 'failed'),
            }
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
          id: generateId(),
          role: 'user',
          content: newText,
          createdAt: Date.now(),
          userIndex: ord,
        }
        return [...kept, editedUser, assistantMsg]
      })
      // 编辑重发 = 要新答案：跳过语义缓存，用当前选定模型重答
      void streamInto(newText, assistantMsg.id, sessionId, undefined, true)
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
          const baseVersions = (m.versions ?? [snapshot(m)]).slice(-MAX_ASSISTANT_VERSIONS)
          return {
            ...m,
            versions: baseVersions,
            versionIndex: baseVersions.length - 1,
            content: '',
            plan: null,
            timeline: [],
            error: null,
            streaming: true,
            createdAt: Date.now(),
            model: undefined,
            cached: false,
            downgraded: false,
          }
        })
      })
      // 重新生成 = 要新答案：跳过语义缓存（否则单轮起步会命中旧缓存原样返回），用当前选定模型
      void streamInto(userMsg.rawContent ?? userMsg.content, assistantId, sessionId, undefined, true)
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
    hasMoreOlder,
    loadingOlder,
    loadOlderMessages,
    send,
    stop,
    editResend,
    resendUser,
    regenerate,
    switchVersion,
  }
}
