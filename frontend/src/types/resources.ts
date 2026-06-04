// Memory / Rules / Skills / MCP 共享前端类型

export type MemoryItem = {
  id: number
  category: string
  key: string
  value: string
  source: string
  created_at: string
  accessed_at: string
}

export type MemoryListResponse = {
  memories: MemoryItem[]
}

export type RulesReadResponse = {
  text: string
  path: string
  exists: boolean
}

export type RulesWriteResponse = {
  path: string
  length: number
  restart_required: boolean
}

export type SkillItem = {
  name: string
  description: string
  location: string
}

export type SkillFailure = {
  path: string
  reason: string
}

export type SkillsResponse = {
  loaded: SkillItem[]
  failed: SkillFailure[]
}

export type MCPServer = {
  name: string
  status: string
  tool_count: number
  error: string | null
  command: string
}

export type MCPServerListResponse = {
  servers: MCPServer[]
}

export type MCPTool = {
  name: string
  description: string
  inputSchema: Record<string, unknown>
  server: string
}

export type MCPToolListResponse = {
  tools: MCPTool[]
}

// memory 类别中文 label（跟后端 src/memory/user_memory.py CATEGORY_LABELS 对齐）
export const CATEGORY_LABELS: Record<string, string> = {
  preference: '偏好',
  background: '背景',
  instruction: '指令',
  task: '任务',
  correction: '纠错',
}

export const SOURCE_LABELS: Record<string, string> = {
  auto: '自动',
  explicit: '请记住',
  manual: '手工',
}
