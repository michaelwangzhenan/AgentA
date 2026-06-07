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
}

export type RulesWriteResponse = {
  length: number
}

export type SkillItem = {
  name: string
  description: string
  location: string
  body: string
  // name / description 之外的 frontmatter 字段（agentskills.io allowed-tools 等），passthrough 保留
  frontmatter_extra: Record<string, unknown>
}

export type SkillFailure = {
  path: string
  reason: string
}

export type SkillsResponse = {
  loaded: SkillItem[]
  disabled: SkillItem[]
  failed: SkillFailure[]
}

export type SkillReloadResponse = {
  loaded_count: number
  disabled_count: number
  failed_count: number
}

export type SkillCreateRequest = {
  name: string
  description: string
  body: string
  frontmatter_extra?: Record<string, unknown>
}

export type SkillUpdateRequest = {
  description: string
  body: string
  // null/undefined = 保留磁盘原有 extra；{} = 清空；非空 dict = 整体替换
  frontmatter_extra?: Record<string, unknown> | null
}

export type SkillRenameRequest = {
  new_name: string
}

export type SkillToggleResponse = {
  name: string
  enabled: boolean
}

export type MCPServer = {
  name: string
  status: string
  enabled: boolean
  tool_count: number
  error: string | null
  command: string
  args: string[]
  env: Record<string, string>
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

export type MCPServerCreateRequest = {
  name: string
  command: string
  args: string[]
  env: Record<string, string>
}

export type MCPServerUpdateRequest = {
  command: string
  args: string[]
  env: Record<string, string>
}

export type MCPServerRenameRequest = {
  new_name: string
}

export type MCPServerToggleRequest = {
  enabled: boolean
}

export type MCPServerToggleResponse = {
  name: string
  enabled: boolean
}

export type MCPReloadResponse = {
  total: number
  enabled: number
  connected: number
  failed: number
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
