// Session 元数据类型 —— 对齐后端 src/api/schemas/session.py
import type { Message } from '@/types/chat'

export type Session = {
  id: string
  title: string
  created_at: string
  msg_count: number
}

export type SessionListResponse = {
  sessions: Session[]
}

export type SessionMessagesResponse = {
  // OpenAI messages 格式（含可选 tool_calls / tool_call_id）
  messages: Array<{
    role: string
    content: string
    tool_calls?: unknown
    tool_call_id?: string
  }>
}

// 把后端 messages 列表（OpenAI 格式）转成前端 Message[]（用户/助手气泡）。
// 简化策略：
//   - role=user  → UserMessage
//   - role=assistant 且有 content → AssistantMessage（不含 tool_calls / thinking，因为这些是流式期间的状态）
//   - role=assistant 且仅有 tool_calls 无 content → 跳过（中间步骤；下一条 assistant 的 content 会承载最终答案）
//   - role=tool / role=system → 跳过（不展示）
export function backendMessagesToFrontend(
  raw: SessionMessagesResponse['messages'],
): Message[] {
  const out: Message[] = []
  for (const m of raw) {
    if (m.role === 'user') {
      out.push({
        id: crypto.randomUUID(),
        role: 'user',
        content: m.content,
      })
    } else if (m.role === 'assistant' && m.content) {
      out.push({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: m.content,
        thinking: '',
        plan: null,
        toolCalls: [],
        error: null,
        streaming: false,
      })
    }
  }
  return out
}
