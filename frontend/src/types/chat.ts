export type Role = 'user' | 'assistant'

export type ChatRequest = {
  message: string
  session_id?: string
}

export type ChatResponse = {
  reply: string
  session_id: string
}

// ─── Step 2：流式事件帧 ─────────────────────────────────────────────────
// 跟后端 src/agent/core/event_bus.py 的 ALL_EVENT_TYPES 对齐

export type TokenUsage = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export type AgentStreamEvent =
  | { type: 'thinking_chunk'; payload: { text: string } }
  | { type: 'token_chunk'; payload: { text: string } }
  | {
      type: 'tool_call_start'
      payload: { name: string; args: Record<string, unknown>; call_id: string }
    }
  | {
      type: 'tool_call_end'
      payload: { call_id: string; status: string; preview: string }
    }
  | {
      type: 'plan_created'
      payload: { steps: { id: number; text: string }[]; [k: string]: unknown }
    }
  | {
      type: 'plan_step_start'
      payload: { step_id: number; text: string }
    }
  | {
      type: 'plan_step_end'
      payload: { step_id: number; status: string; note?: string }
    }
  | {
      type: 'final_answer'
      payload: {
        text: string
        usage?: TokenUsage | null
        aborted_by_user?: boolean
      }
    }
  | {
      type: 'error'
      payload: { message: string; recoverable: boolean; phase: string }
    }
  | { type: 'info'; payload: Record<string, unknown> }

// ─── Assistant 消息子块状态 ────────────────────────────────────────────

// 跟后端 update_step(status=...) 的取值对齐（详 src/agent/tools.py _tool_update_step）：
//   success / failed / skipped 由 LLM 传入；pending / running 是前端 placeholder
export type PlanStepStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'failed'
  | 'skipped'

export type PlanStep = {
  id: number
  text: string
  status: PlanStepStatus
  note?: string
}

export type ToolCallState = {
  call_id: string
  name: string
  args: Record<string, unknown>
  status: 'running' | 'ok' | 'error' | 'empty'
  preview?: string
}

/** 一次生成结果的快照；regenerate 多次后用于 ‹N/M› 切换（仅前端内存，不持久化） */
export type AssistantVersion = {
  content: string
  thinking: string
  thinkingMs: number | null
  plan: PlanStep[] | null
  toolCalls: ToolCallState[]
  error: string | null
}

export type AssistantMessage = {
  id: string
  role: 'assistant'
  content: string
  thinking: string
  /** reasoning 累计耗时（ms）；null 表示本轮没有 thinking */
  thinkingMs: number | null
  plan: PlanStep[] | null
  toolCalls: ToolCallState[]
  error: string | null
  streaming: boolean
  createdAt?: number
  /** regenerate 产生的历史版本快照（含当前）；缺省 / 长度<2 时不显示切换器 */
  versions?: AssistantVersion[]
  versionIndex?: number
}

export type UserMessage = {
  id: string
  role: 'user'
  content: string
  createdAt?: number
}

export type Message = UserMessage | AssistantMessage
