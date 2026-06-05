import { fetchEventSource } from '@microsoft/fetch-event-source'
import type {
  AgentStreamEvent,
  ChatRequest,
  ChatResponse,
} from '@/types/chat'
import type {
  Session,
  SessionListResponse,
  SessionMessagesResponse,
} from '@/types/session'
import type {
  KBClearAllResponse,
  KBDeleteResponse,
  KBDocument,
  KBDocumentListResponse,
  KBUploadResponse,
} from '@/types/kb'
import type {
  MCPReloadResponse,
  MCPServer,
  MCPServerCreateRequest,
  MCPServerListResponse,
  MCPServerRenameRequest,
  MCPServerToggleRequest,
  MCPServerToggleResponse,
  MCPServerUpdateRequest,
  MCPTool,
  MCPToolListResponse,
  MemoryItem,
  MemoryListResponse,
  RulesReadResponse,
  RulesWriteResponse,
  SkillCreateRequest,
  SkillItem,
  SkillRenameRequest,
  SkillReloadResponse,
  SkillToggleResponse,
  SkillUpdateRequest,
  SkillsResponse,
} from '@/types/resources'
import type { AppConfig } from '@/types/config'
import type {
  Plan,
  PlanListResponse,
  PlanSummary,
  QuizListResponse,
  QuizSet,
  QuizSetSummary,
  SRSCard,
  SRSCardListResponse,
} from '@/types/business'

// ─── 通用 helper ────────────────────────────────────────────────────────

async function _ensureOk(res: Response): Promise<void> {
  if (res.ok) return
  let detail = `HTTP ${res.status}`
  try {
    const data = (await res.json()) as { detail?: string }
    if (data?.detail) detail = `${detail}: ${data.detail}`
  } catch {
    // 响应不是 JSON
  }
  throw new Error(detail)
}

// ─── Step 1：非流式（保留作为 fallback / 调试入口）────────────────────

export async function postChat(message: string): Promise<ChatResponse> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message } satisfies ChatRequest),
  })

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = (await res.json()) as { detail?: string }
      if (data?.detail) detail = `${detail}: ${data.detail}`
    } catch {
      // 响应不是 JSON / 解析失败，沿用 detail 默认值
    }
    throw new Error(detail)
  }

  return (await res.json()) as ChatResponse
}

// ─── Step 2：SSE 流式 ─────────────────────────────────────────────────

export type StreamHandlers = {
  onEvent: (event: AgentStreamEvent) => void
  onClose?: () => void
  onError?: (err: Error) => void
}

class FatalStreamError extends Error {}
class StreamAbortedError extends Error {}

export async function streamChat(
  message: string,
  handlers: StreamHandlers,
  options: { sessionId?: string; signal?: AbortSignal } = {},
): Promise<void> {
  const { sessionId, signal } = options
  try {
    await fetchEventSource('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        ...(sessionId ? { session_id: sessionId } : {}),
      } satisfies ChatRequest),
      signal,
      openWhenHidden: true,
      // `@microsoft/fetch-event-source` 对外部 signal 的内部 dispose 在 React/strict 模式
      // 下不总能立刻关闭底层 fetch（参见 Azure/fetch-event-source #24 / #46 / #84）。
      // 业界推荐做法：在 onopen / onmessage 入口主动检查 signal.aborted，主动 throw
      // 一个 abort 错误，借库的 onerror rethrow 机制走"立刻关闭+不重试"路径。
      async onopen(response) {
        if (signal?.aborted) throw new StreamAbortedError('aborted')
        const ct = response.headers.get('content-type') ?? ''
        if (response.ok && ct.includes('text/event-stream')) {
          return
        }
        let detail = `HTTP ${response.status}`
        try {
          const data = (await response.json()) as { detail?: string }
          if (data?.detail) detail = `${detail}: ${data.detail}`
        } catch {
          // 响应不是 JSON
        }
        throw new FatalStreamError(detail)
      },
      onmessage(ev) {
        if (signal?.aborted) throw new StreamAbortedError('aborted')
        if (!ev.data) return
        try {
          const event = JSON.parse(ev.data) as AgentStreamEvent
          handlers.onEvent(event)
        } catch (e) {
          console.error('[streamChat] SSE frame parse error', ev.data, e)
        }
      },
      onerror(err) {
        // 主动 abort 抛出的：静默关闭，不调用 onError（避免在 UI 上显示"连接错误"）
        if (err instanceof StreamAbortedError || signal?.aborted) {
          throw err
        }
        const e = err instanceof Error ? err : new Error(String(err))
        handlers.onError?.(e)
        throw e
      },
      onclose() {
        handlers.onClose?.()
      },
    })
  } catch (err) {
    // 主动 abort 路径：吞掉，让调用方按正常 resolve 处理
    if (err instanceof StreamAbortedError) return
    if (signal?.aborted) return
    throw err
  }
}

// ─── Step 3：Session 管理 ──────────────────────────────────────────────

export async function listSessions(): Promise<Session[]> {
  const res = await fetch('/api/sessions')
  await _ensureOk(res)
  return ((await res.json()) as SessionListResponse).sessions
}

export async function createSession(title?: string): Promise<Session> {
  const res = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(title ? { title } : {}),
  })
  await _ensureOk(res)
  return (await res.json()) as Session
}

export async function renameSession(id: string, title: string): Promise<Session> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  await _ensureOk(res)
  return (await res.json()) as Session
}

export async function deleteSession(id: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
  return (await res.json()) as { deleted: boolean }
}

export async function loadSessionMessages(
  id: string,
): Promise<SessionMessagesResponse> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages`)
  await _ensureOk(res)
  return (await res.json()) as SessionMessagesResponse
}

// ─── Step 4：Knowledge Base ────────────────────────────────────────────

export async function listKBDocuments(): Promise<KBDocument[]> {
  const res = await fetch('/api/kb/documents')
  await _ensureOk(res)
  return ((await res.json()) as KBDocumentListResponse).documents
}

export async function uploadKBFile(file: File): Promise<KBUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/kb/upload', {
    method: 'POST',
    body: form,
  })
  await _ensureOk(res)
  return (await res.json()) as KBUploadResponse
}

export async function deleteKBDocument(docId: string): Promise<KBDeleteResponse> {
  const res = await fetch(`/api/kb/documents/${encodeURIComponent(docId)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
  return (await res.json()) as KBDeleteResponse
}

export async function clearAllKBDocuments(): Promise<KBClearAllResponse> {
  const res = await fetch('/api/kb/documents', { method: 'DELETE' })
  await _ensureOk(res)
  return (await res.json()) as KBClearAllResponse
}

// ─── Step 5：User Memory / Rules / Skills / MCP ───────────────────────

export async function listMemories(): Promise<MemoryItem[]> {
  const res = await fetch('/api/memory')
  await _ensureOk(res)
  return ((await res.json()) as MemoryListResponse).memories
}

export async function upsertMemory(
  category: string,
  key: string,
  value: string,
  source: string = 'manual',
): Promise<MemoryItem> {
  const res = await fetch('/api/memory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category, key, value, source }),
  })
  await _ensureOk(res)
  return (await res.json()) as MemoryItem
}

export async function patchMemory(
  id: number,
  value: string,
): Promise<{ updated: boolean }> {
  const res = await fetch(`/api/memory/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  })
  await _ensureOk(res)
  return (await res.json()) as { updated: boolean }
}

export async function deleteMemory(id: number): Promise<{ deleted: boolean }> {
  const res = await fetch(`/api/memory/${id}`, { method: 'DELETE' })
  await _ensureOk(res)
  return (await res.json()) as { deleted: boolean }
}

export async function clearMemories(): Promise<{ cleared: number }> {
  const res = await fetch('/api/memory', { method: 'DELETE' })
  await _ensureOk(res)
  return (await res.json()) as { cleared: number }
}

export async function readRules(): Promise<RulesReadResponse> {
  const res = await fetch('/api/rules')
  await _ensureOk(res)
  return (await res.json()) as RulesReadResponse
}

export async function writeRules(text: string): Promise<RulesWriteResponse> {
  const res = await fetch('/api/rules', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  await _ensureOk(res)
  return (await res.json()) as RulesWriteResponse
}

export async function listSkills(): Promise<SkillsResponse> {
  const res = await fetch('/api/skills')
  await _ensureOk(res)
  return (await res.json()) as SkillsResponse
}

export async function reloadSkills(): Promise<SkillReloadResponse> {
  const res = await fetch('/api/skills/reload', { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as SkillReloadResponse
}

export async function createSkill(req: SkillCreateRequest): Promise<SkillItem> {
  const res = await fetch('/api/skills', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillItem
}

export async function updateSkill(name: string, req: SkillUpdateRequest): Promise<SkillItem> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillItem
}

export async function deleteSkill(name: string): Promise<void> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
  await _ensureOk(res)
}

export async function renameSkill(
  name: string,
  req: SkillRenameRequest,
): Promise<SkillItem> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillItem
}

export async function toggleSkill(name: string, enabled: boolean): Promise<SkillToggleResponse> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillToggleResponse
}

export async function listMCPServers(): Promise<MCPServer[]> {
  const res = await fetch('/api/mcp/servers')
  await _ensureOk(res)
  return ((await res.json()) as MCPServerListResponse).servers
}

export async function listMCPTools(): Promise<MCPTool[]> {
  const res = await fetch('/api/mcp/tools')
  await _ensureOk(res)
  return ((await res.json()) as MCPToolListResponse).tools
}

export async function createMCPServer(
  req: MCPServerCreateRequest,
): Promise<MCPServer> {
  const res = await fetch('/api/mcp/servers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as MCPServer
}

export async function updateMCPServer(
  name: string,
  req: MCPServerUpdateRequest,
): Promise<MCPServer> {
  const res = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as MCPServer
}

export async function deleteMCPServer(name: string): Promise<void> {
  const res = await fetch(`/api/mcp/servers/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
}

export async function renameMCPServer(
  name: string,
  req: MCPServerRenameRequest,
): Promise<MCPServer> {
  const res = await fetch(
    `/api/mcp/servers/${encodeURIComponent(name)}/rename`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    },
  )
  await _ensureOk(res)
  return (await res.json()) as MCPServer
}

export async function toggleMCPServer(
  name: string,
  req: MCPServerToggleRequest,
): Promise<MCPServerToggleResponse> {
  const res = await fetch(
    `/api/mcp/servers/${encodeURIComponent(name)}/toggle`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    },
  )
  await _ensureOk(res)
  return (await res.json()) as MCPServerToggleResponse
}

export async function reloadMCPServers(): Promise<MCPReloadResponse> {
  const res = await fetch('/api/mcp/reload', { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as MCPReloadResponse
}

// ─── Step 6：System Config ─────────────────────────────────────────────

export async function getConfig(): Promise<AppConfig> {
  const res = await fetch('/api/config')
  await _ensureOk(res)
  return (await res.json()) as AppConfig
}

// ─── Step 7：业务面板（plans / quizzes / srs） ─────────────────────────

export async function listPlans(): Promise<PlanSummary[]> {
  const res = await fetch('/api/plans')
  await _ensureOk(res)
  return ((await res.json()) as PlanListResponse).plans
}

export async function getActivePlan(): Promise<Plan | null> {
  const res = await fetch('/api/plans/active')
  await _ensureOk(res)
  return (await res.json()) as Plan | null
}

export async function getPlan(planId: number): Promise<Plan> {
  const res = await fetch(`/api/plans/${planId}`)
  await _ensureOk(res)
  return (await res.json()) as Plan
}

export async function listQuizzes(): Promise<QuizSetSummary[]> {
  const res = await fetch('/api/quizzes')
  await _ensureOk(res)
  return ((await res.json()) as QuizListResponse).quizzes
}

export async function getQuiz(quizSetId: number): Promise<QuizSet> {
  const res = await fetch(`/api/quizzes/${quizSetId}`)
  await _ensureOk(res)
  return (await res.json()) as QuizSet
}

export async function listSRSDue(limit?: number): Promise<SRSCard[]> {
  const url = limit ? `/api/srs/due?limit=${limit}` : '/api/srs/due'
  const res = await fetch(url)
  await _ensureOk(res)
  return ((await res.json()) as SRSCardListResponse).cards
}

export async function listSRSCards(): Promise<SRSCard[]> {
  const res = await fetch('/api/srs/cards')
  await _ensureOk(res)
  return ((await res.json()) as SRSCardListResponse).cards
}

export async function getSRSCard(cardId: number): Promise<SRSCard> {
  const res = await fetch(`/api/srs/cards/${cardId}`)
  await _ensureOk(res)
  return (await res.json()) as SRSCard
}
