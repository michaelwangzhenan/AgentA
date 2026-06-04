// Session 元数据类型 —— 对齐后端 src/api/schemas/session.py
import type { AssistantMessage, Message, ToolCallState } from '@/types/chat'

export type Session = {
  id: string
  title: string
  created_at: string
  msg_count: number
}

export type SessionListResponse = {
  sessions: Session[]
}

type BackendToolCall = {
  id?: string
  type?: string
  function?: {
    name?: string
    arguments?: string
  }
}

export type SessionMessagesResponse = {
  // OpenAI messages 格式（含可选 tool_calls / tool_call_id）
  messages: Array<{
    role: string
    content: string
    tool_calls?: BackendToolCall[]
    tool_call_id?: string
  }>
}

/**
 * 把后端 messages 列表（OpenAI 格式）转成前端 Message[]（用户/助手气泡）。
 *
 * 重建策略：
 *   - 一个 user message 后到下一个 user message 之间的所有 assistant + tool messages
 *     合并成单个前端 AssistantMessage（恢复 WorkBlock 视图）
 *   - assistant.tool_calls → ToolCallState[]（args 从 JSON 字符串反解析）
 *   - 紧跟的 role=tool message 的 content → 对应 tool_call 的 preview
 *   - thinking / plan 不重建（流式期间的瞬态信号；plan 当前结构需要 make_plan 工具 args
 *     反推，复杂度高，暂留空。用户要看完整 plan 去"学习计划"view）
 */
export function backendMessagesToFrontend(
  raw: SessionMessagesResponse['messages'],
): Message[] {
  const out: Message[] = []
  let pendingAssistant: AssistantMessage | null = null
  let toolCallIndex = new Map<string, ToolCallState>()

  const finalize = () => {
    if (pendingAssistant) {
      out.push(pendingAssistant)
      pendingAssistant = null
      toolCallIndex = new Map()
    }
  }

  for (const m of raw) {
    if (m.role === 'user') {
      finalize()
      out.push({
        id: crypto.randomUUID(),
        role: 'user',
        content: m.content,
      })
      continue
    }

    if (m.role === 'assistant') {
      if (!pendingAssistant) {
        pendingAssistant = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: '',
          thinking: '',
          plan: null,
          toolCalls: [],
          error: null,
          streaming: false,
        }
      }
      // 累积 tool_calls 到当前轮
      const tcs = m.tool_calls
      if (Array.isArray(tcs)) {
        for (const tc of tcs) {
          const callId = tc.id ?? ''
          const fn = tc.function ?? {}
          const name = fn.name ?? ''
          let args: Record<string, unknown> = {}
          if (fn.arguments) {
            try {
              args = JSON.parse(fn.arguments) as Record<string, unknown>
            } catch {
              args = { _raw: fn.arguments }
            }
          }
          const state: ToolCallState = {
            call_id: callId,
            name,
            args,
            status: 'ok',
          }
          pendingAssistant.toolCalls.push(state)
          if (callId) toolCallIndex.set(callId, state)
        }
      }
      // 累积 content（同轮内多条 assistant 含 content 时拼接，极少见）
      if (m.content) {
        pendingAssistant.content = pendingAssistant.content
          ? `${pendingAssistant.content}\n\n${m.content}`
          : m.content
      }
      continue
    }

    if (m.role === 'tool') {
      const tid = m.tool_call_id ?? ''
      const state = toolCallIndex.get(tid)
      if (state) {
        state.preview = m.content
      }
      continue
    }
    // role=system 等其它角色不展示
  }
  finalize()
  return out
}
