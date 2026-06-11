export type Role = 'user' | 'assistant'

export type ChatMode = 'chat' | 'deep_research'

export type ChatRequest = {
  message: string
  session_id?: string
  mode?: ChatMode
  /** 「重新生成」时为 true：跳过语义缓存，用当前选定模型重答 */
  skip_cache?: boolean
}

export type ChatResponse = {
  reply: string
  session_id: string
  model?: string
  cached?: boolean
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
      type: 'tool_progress'
      payload: { call_id: string; stage: string; label: string }
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
        /** 本次实际应答的模型 id（auto 路由后可能与所选不同） */
        model?: string
        /** auto 档是否被向下降级到更便宜的模型 */
        downgraded?: boolean
        /** 是否直接来自语义缓存 */
        cached?: boolean
      }
    }
  | {
      type: 'error'
      payload: { message: string; recoverable: boolean; phase: string }
    }
  | { type: 'info'; payload: Record<string, unknown> }
  // ─── Deep Research 四阶段进度事件（对齐后端 research_* 事件）───────────
  | { type: 'research_started'; payload: { query: string } }
  | {
      type: 'research_plan'
      payload: { subquestions: { id: number; text: string }[] }
    }
  | {
      type: 'research_subagent_start'
      payload: { sub_id: number; question: string }
    }
  | {
      type: 'research_subagent_progress'
      payload: { sub_id: number; stage: string; label: string; sources: number }
    }
  | {
      type: 'research_subagent_end'
      payload: { sub_id: number; status: string; sources: number; note?: string }
    }
  | {
      type: 'research_reflect'
      payload: { note: string; gap?: string; followups?: string[] }
    }
  | { type: 'research_synthesizing'; payload: Record<string, unknown> }

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
  /** 工具运行中的阶段标签（如 检索中），来自 tool_progress 事件；结束后清空 */
  stage?: string
}

/** 一段 thinking（对应 agent 一次循环的推理）；每次循环单独成段 */
export type ThinkingSegment = {
  kind: 'thinking'
  id: string
  text: string
  /** 本段 reasoning 耗时（ms）；null 表示无计时 */
  thinkingMs: number | null
}

export type ToolSegment = {
  kind: 'tool'
  call: ToolCallState
}

/** 按事件到达顺序排列的 thinking / 工具调用混合时间线（保留 think→act 的循环结构） */
export type TimelineItem = ThinkingSegment | ToolSegment

// ─── Deep Research 研究面板状态 ────────────────────────────────────────

export type ResearchPhase =
  | 'planning'
  | 'researching'
  | 'reflecting'
  | 'synthesizing'
  | 'done'

export type ResearchSubagentStatus = 'running' | 'ok' | 'failed'

export type ResearchSubagent = {
  sub_id: number
  question: string
  status: ResearchSubagentStatus
  /** 当前阶段标签（检索知识库 / 联网搜索 / 读取网页），结束后清空 */
  label?: string
  sources: number
  note?: string
}

export type ResearchReflect = {
  note: string
  gap?: string
  followups?: string[]
}

/** 深度研究进度面板状态：四阶段 + 子代理行 */
export type ResearchState = {
  phase: ResearchPhase
  query: string
  subquestions: { id: number; text: string }[]
  subagents: ResearchSubagent[]
  reflect?: ResearchReflect | null
}

/** 一次生成结果的快照；regenerate 多次后用于 ‹N/M› 切换（仅前端内存，不持久化） */
export type AssistantVersion = {
  content: string
  plan: PlanStep[] | null
  timeline: TimelineItem[]
  error: string | null
  model?: string
  cached?: boolean
  downgraded?: boolean
}

export type AssistantMessage = {
  id: string
  role: 'assistant'
  content: string
  plan: PlanStep[] | null
  timeline: TimelineItem[]
  error: string | null
  streaming: boolean
  createdAt?: number
  /** 深度研究进度面板状态；非深度研究消息为 null / 缺省 */
  research?: ResearchState | null
  /** regenerate 产生的历史版本快照（含当前）；缺省 / 长度<2 时不显示切换器 */
  versions?: AssistantVersion[]
  versionIndex?: number
  /** 本次实际应答模型 id；auto 路由降级时与所选不同 */
  model?: string
  /** 回答是否直接来自语义缓存 */
  cached?: boolean
  /** auto 档是否被向下降级 */
  downgraded?: boolean
}

/** 用户消息里携带的附件（仅用于展示卡片；正文已内联进 rawContent 发给后端） */
export type MessageAttachment = {
  name: string
  kind: 'text' | 'image' | 'other'
  /** 文本附件行数；非文本附件无此值 */
  lines?: number
  /** 是否随消息发给了后端（图片 / 二进制当前不发） */
  sent: boolean
}

export type UserMessage = {
  id: string
  role: 'user'
  /** 展示用：仅用户输入的 query（不含附件正文） */
  content: string
  /** 发 / 重发给后端的完整内容（含内联附件正文）；缺省时退回 content */
  rawContent?: string
  attachments?: MessageAttachment[]
  createdAt?: number
}

export type Message = UserMessage | AssistantMessage
