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

export type AssistantMessage = {
  id: string
  role: 'assistant'
  content: string
  thinking: string
  plan: PlanStep[] | null
  toolCalls: ToolCallState[]
  error: string | null
  streaming: boolean
}

export type UserMessage = {
  id: string
  role: 'user'
  content: string
}

export type Message = UserMessage | AssistantMessage
