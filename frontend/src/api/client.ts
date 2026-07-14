import { fetchEventSource } from '@microsoft/fetch-event-source'
import type {
  AgentStreamEvent,
  ChatMode,
  ChatRequest,
  ChatResponse,
} from '@/types/chat'
import type {
  Session,
  SessionListResponse,
  SessionMessagesResponse,
} from '@/types/session'
import type {
  IngestProgress,
  IngestResult,
  KBClearAllResponse,
  KBCollectionListResponse,
  KBDeleteResponse,
  KBDocument,
  KBDocumentListResponse,
  KBDocumentsQuery,
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
import type {
  ConfigItemResponse,
  ConfigItemView,
  ConfigReloadResponse,
  ConfigResponse,
  ModelsResponse,
} from '@/types/config'
import type {
  CreatePlanInput,
  Plan,
  PlanListResponse,
  PlanSummary,
  QuizAnswerInput,
  QuizListResponse,
  QuizSet,
  QuizSetSummary,
  SRSCard,
  SRSCardListResponse,
  SRSRating,
} from '@/types/business'
import type { ApiKeyView, ApiKeysResponse } from '@/types/apiKeys'
import type { AuthResponse, LlmPrefs, LlmPrefsUpdate, UserInfo } from '@/types/auth'
import type {
  PricingResponse,
  PricingUpdateItem,
  SavingsSeries,
  SavingsSummary,
  UsageEvents,
  UsageSeries,
  UsageSummary,
  UserUsageList,
} from '@/types/usage'
import type { RoutingPoolResponse } from '@/types/routing'
import type {
  Bm25DocDetail,
  Bm25DocsPage,
  Bm25Indexes,
  ChromaCollections,
  ChromaItemDetail,
  ChromaItemsPage,
  ItemsQuery,
  OrphanCleanupResult,
  OrphanSegmentsPreview,
  PruneResult,
  PurgePreview,
  PurgeResult,
  PurgeSelection,
  SqliteDatabases,
  SqliteTableRows,
  VacuumResult,
} from '@/types/dbAdmin'
import type {
  EvalRunRequest,
  EvalRunStatus,
  EvalSummary,
  GoldenCreateInput,
  GoldenGenOptions,
  GoldenItem,
  GoldenList,
  GoldenUpdateInput,
  ReportContent,
  ReportList,
  SecurityEventPage,
  SecurityEventsQuery,
  SecurityRuntimeSummary,
  SecuritySummary,
  SecurityTrend,
  TraceDetail,
  TraceList,
  TraceOverview,
  TraceSeries,
} from '@/types/eval'
import type {
  BackupListResponse,
  BackupSnapshot,
  RestoreResponse,
} from '@/types/backup'

// ─── 401 全局处理 ──────────────────────────────────────────────────────
// 登录态失效时，由 AuthProvider 注册回调把界面切回登录页。

let _onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  _onUnauthorized = fn
}

// ─── 通用 helper ────────────────────────────────────────────────────────

// 所有 API 请求统一显式带上 cookie 凭证。同源部署靠浏览器默认即可，但显式声明能在
// 前后端跨域部署时仍带上登录态（与 iter_6 §3.6 设计一致）。
function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { credentials: 'include', ...init })
}

async function _ensureOk(res: Response): Promise<void> {
  if (res.ok) return
  if (res.status === 401) _onUnauthorized?.()
  // 后端给了友好 detail（如"用户名或密码错误"）就直接用，不加 "HTTP 401:" 前缀；
  // 没 detail 时才回落到 HTTP 状态码。
  let detail = `HTTP ${res.status}`
  try {
    const data = (await res.json()) as { detail?: string }
    if (data?.detail) detail = data.detail
  } catch {
    // 响应不是 JSON
  }
  throw new Error(detail)
}

// ─── Step 1：非流式（保留作为 fallback / 调试入口）────────────────────

export async function postChat(message: string): Promise<ChatResponse> {
  const res = await apiFetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message } satisfies ChatRequest),
  })
  await _ensureOk(res)
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
  options: {
    sessionId?: string
    signal?: AbortSignal
    mode?: ChatMode
    skipCache?: boolean
  } = {},
): Promise<void> {
  const { sessionId, signal, mode, skipCache } = options
  try {
    await fetchEventSource('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        message,
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(mode ? { mode } : {}),
        ...(skipCache ? { skip_cache: true } : {}),
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
        if (response.status === 401) _onUnauthorized?.()
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
  const res = await apiFetch('/api/sessions')
  await _ensureOk(res)
  return ((await res.json()) as SessionListResponse).sessions
}

export async function createSession(title?: string): Promise<Session> {
  const res = await apiFetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(title ? { title } : {}),
  })
  await _ensureOk(res)
  return (await res.json()) as Session
}

export async function renameSession(id: string, title: string): Promise<Session> {
  const res = await apiFetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  await _ensureOk(res)
  return (await res.json()) as Session
}

export async function deleteSession(id: string): Promise<{ deleted: boolean }> {
  const res = await apiFetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
  return (await res.json()) as { deleted: boolean }
}

export const SESSION_MESSAGES_PAGE_SIZE = 60

export async function loadSessionMessages(
  id: string,
  opts?: { limit?: number; beforeId?: number },
): Promise<SessionMessagesResponse> {
  const params = new URLSearchParams()
  if (opts?.limit != null) params.set('limit', String(opts.limit))
  if (opts?.beforeId != null) params.set('before_id', String(opts.beforeId))
  const qs = params.toString()
  const res = await apiFetch(
    `/api/sessions/${encodeURIComponent(id)}/messages${qs ? `?${qs}` : ''}`,
  )
  await _ensureOk(res)
  return (await res.json()) as SessionMessagesResponse
}

/** 从第 userMessageIndex（0 基）条 user 消息起截断（编辑重发 / 重新生成前置步骤）。 */
export async function truncateSession(
  id: string,
  userMessageIndex: number,
): Promise<{ deleted: number }> {
  const res = await apiFetch(
    `/api/sessions/${encodeURIComponent(id)}/truncate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_message_index: userMessageIndex }),
    },
  )
  await _ensureOk(res)
  return (await res.json()) as { deleted: number }
}

// ─── Step 4：Knowledge Base ────────────────────────────────────────────

export async function getKBCollections(refresh = false): Promise<KBCollectionListResponse> {
  const res = await apiFetch(`/api/kb/collections${refresh ? '?refresh=true' : ''}`)
  await _ensureOk(res)
  return (await res.json()) as KBCollectionListResponse
}

export async function listKBDocuments(
  model: string,
  query: KBDocumentsQuery = {},
): Promise<KBDocumentListResponse> {
  const params = new URLSearchParams({ model })
  if (query.page != null) params.set('page', String(query.page))
  if (query.pageSize != null) params.set('page_size', String(query.pageSize))
  if (query.sortBy) params.set('sort_by', query.sortBy)
  if (query.desc != null) params.set('desc', query.desc ? 'true' : 'false')
  if (query.filenameQ) params.set('filename_q', query.filenameQ)
  if (query.lang) params.set('lang', query.lang)
  if (query.ext) params.set('ext', query.ext)
  if (query.tsFrom != null) params.set('ts_from', String(query.tsFrom))
  if (query.tsTo != null) params.set('ts_to', String(query.tsTo))
  const res = await apiFetch(`/api/kb/documents?${params.toString()}`)
  await _ensureOk(res)
  return (await res.json()) as KBDocumentListResponse
}

// 上传 + 入库：SSE 流式，progress 事件回调 onProgress，最终返回 done 结果。
// 校验失败（400/415/413/422）按普通 HTTP 错误抛出；流中 error 事件转为异常抛出。
export type IngestGoldenOpts = {
  goldenLlm?: string
  goldenMaxQ?: number
}

export async function ingestKBFileStream(
  file: File,
  model: string,
  relpath: string,
  ingestId: string,
  onProgress?: (p: IngestProgress) => void,
  signal?: AbortSignal,
  golden?: IngestGoldenOpts,
): Promise<IngestResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('model', model)
  form.append('relpath', relpath)
  form.append('ingest_id', ingestId)
  if (golden?.goldenLlm != null) form.append('golden_llm', golden.goldenLlm)
  if (golden?.goldenMaxQ != null) form.append('golden_max_q', String(golden.goldenMaxQ))
  const res = await apiFetch('/api/kb/upload', { method: 'POST', body: form, signal })
  if (!res.ok) {
    await _ensureOk(res) // 抛出带 detail 的错误
  }
  const reader = res.body?.getReader()
  if (!reader) throw new Error('无法读取上传响应流')

  const decoder = new TextDecoder()
  let buf = ''
  let result: IngestResult | null = null
  let errMsg: string | null = null

  for (;;) {
    if (signal?.aborted) {
      await reader.cancel().catch(() => {})
      throw new DOMException('Aborted', 'AbortError')
    }
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      for (const line of block.split('\n')) {
        if (!line.startsWith('data:')) continue
        const ev = JSON.parse(line.slice(5).trim())
        if (ev.type === 'progress') {
          onProgress?.({ phase: ev.phase, done: ev.done, total: ev.total })
        } else if (ev.type === 'done') {
          result = ev as IngestResult
        } else if (ev.type === 'error') {
          errMsg = ev.message
        }
      }
    }
  }

  if (errMsg) throw new Error(errMsg)
  if (!result) throw new Error('上传未返回结果')
  return result
}

/** 显式取消进行中的单文件入库（abort 经代理时后端未必能感知断开）。 */
export async function cancelKBUpload(ingestId: string): Promise<void> {
  const form = new FormData()
  form.append('ingest_id', ingestId)
  const res = await apiFetch('/api/kb/upload/cancel', { method: 'POST', body: form })
  await _ensureOk(res)
}

export async function deleteKBDocument(
  docId: string,
  model: string,
): Promise<KBDeleteResponse> {
  const res = await apiFetch(
    `/api/kb/documents/${encodeURIComponent(docId)}?model=${encodeURIComponent(model)}`,
    { method: 'DELETE' },
  )
  await _ensureOk(res)
  return (await res.json()) as KBDeleteResponse
}

export async function clearAllKBDocuments(model: string): Promise<KBClearAllResponse> {
  const res = await apiFetch(
    `/api/kb/documents?model=${encodeURIComponent(model)}`,
    { method: 'DELETE' },
  )
  await _ensureOk(res)
  return (await res.json()) as KBClearAllResponse
}

// ─── Step 5：User Memory / Rules / Skills / MCP ───────────────────────

export async function listMemories(): Promise<MemoryItem[]> {
  const res = await apiFetch('/api/memory')
  await _ensureOk(res)
  return ((await res.json()) as MemoryListResponse).memories
}

export async function createMemory(
  text: string,
  source: string = 'manual',
): Promise<MemoryItem> {
  const res = await apiFetch('/api/memory', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, source }),
  })
  await _ensureOk(res)
  return (await res.json()) as MemoryItem
}

export async function patchMemory(
  id: number,
  text: string,
): Promise<{ updated: boolean }> {
  const res = await apiFetch(`/api/memory/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  await _ensureOk(res)
  return (await res.json()) as { updated: boolean }
}

export async function deleteMemory(id: number): Promise<{ deleted: boolean }> {
  const res = await apiFetch(`/api/memory/${id}`, { method: 'DELETE' })
  await _ensureOk(res)
  return (await res.json()) as { deleted: boolean }
}

export async function clearMemories(): Promise<{ cleared: number }> {
  const res = await apiFetch('/api/memory', { method: 'DELETE' })
  await _ensureOk(res)
  return (await res.json()) as { cleared: number }
}

export async function readRules(): Promise<RulesReadResponse> {
  const res = await apiFetch('/api/rules')
  await _ensureOk(res)
  return (await res.json()) as RulesReadResponse
}

export async function writeRules(text: string): Promise<RulesWriteResponse> {
  const res = await apiFetch('/api/rules', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  await _ensureOk(res)
  return (await res.json()) as RulesWriteResponse
}

export async function listSkills(): Promise<SkillsResponse> {
  const res = await apiFetch('/api/skills')
  await _ensureOk(res)
  return (await res.json()) as SkillsResponse
}

export async function reloadSkills(): Promise<SkillReloadResponse> {
  const res = await apiFetch('/api/skills/reload', { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as SkillReloadResponse
}

export async function createSkill(req: SkillCreateRequest): Promise<SkillItem> {
  const res = await apiFetch('/api/skills', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillItem
}

export async function updateSkill(name: string, req: SkillUpdateRequest): Promise<SkillItem> {
  const res = await apiFetch(`/api/skills/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillItem
}

export async function deleteSkill(name: string): Promise<void> {
  const res = await apiFetch(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
  await _ensureOk(res)
}

export async function renameSkill(
  name: string,
  req: SkillRenameRequest,
): Promise<SkillItem> {
  const res = await apiFetch(`/api/skills/${encodeURIComponent(name)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillItem
}

export async function toggleSkill(name: string, enabled: boolean): Promise<SkillToggleResponse> {
  const res = await apiFetch(`/api/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  await _ensureOk(res)
  return (await res.json()) as SkillToggleResponse
}

export async function listMCPServers(): Promise<MCPServer[]> {
  const res = await apiFetch('/api/mcp/servers')
  await _ensureOk(res)
  return ((await res.json()) as MCPServerListResponse).servers
}

export async function listMCPTools(): Promise<MCPTool[]> {
  const res = await apiFetch('/api/mcp/tools')
  await _ensureOk(res)
  return ((await res.json()) as MCPToolListResponse).tools
}

export async function createMCPServer(
  req: MCPServerCreateRequest,
): Promise<MCPServer> {
  const res = await apiFetch('/api/mcp/servers', {
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
  const res = await apiFetch(`/api/mcp/servers/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as MCPServer
}

export async function deleteMCPServer(name: string): Promise<void> {
  const res = await apiFetch(`/api/mcp/servers/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
}

export async function renameMCPServer(
  name: string,
  req: MCPServerRenameRequest,
): Promise<MCPServer> {
  const res = await apiFetch(
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
  const res = await apiFetch(
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
  const res = await apiFetch('/api/mcp/reload', { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as MCPReloadResponse
}

// ─── Step 6：System Config ─────────────────────────────────────────────

export async function getConfig(): Promise<ConfigResponse> {
  const res = await apiFetch('/api/config')
  await _ensureOk(res)
  return (await res.json()) as ConfigResponse
}

export async function getModels(): Promise<ModelsResponse> {
  const res = await apiFetch('/api/config/models')
  await _ensureOk(res)
  return (await res.json()) as ModelsResponse
}

export async function patchConfig(
  key: string,
  value: unknown,
): Promise<ConfigItemView> {
  const res = await apiFetch(`/api/config/${encodeURIComponent(key)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  })
  await _ensureOk(res)
  return ((await res.json()) as ConfigItemResponse).item
}

export async function resetConfig(key: string): Promise<ConfigItemView> {
  const res = await apiFetch(`/api/config/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
  return ((await res.json()) as ConfigItemResponse).item
}

export async function reloadConfig(): Promise<ConfigReloadResponse> {
  const res = await apiFetch('/api/config/reload', { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as ConfigReloadResponse
}

// ─── API Keys（仅 admin；后端永不返回明文）─────────────────────────────

export async function getApiKeys(): Promise<ApiKeyView[]> {
  const res = await apiFetch('/api/api-keys')
  await _ensureOk(res)
  return ((await res.json()) as ApiKeysResponse).items
}

export async function updateApiKey(id: string, value: string): Promise<ApiKeyView> {
  const res = await apiFetch(`/api/api-keys/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  })
  await _ensureOk(res)
  return (await res.json()) as ApiKeyView
}

export async function resetApiKey(id: string): Promise<ApiKeyView> {
  const res = await apiFetch(`/api/api-keys/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
  return (await res.json()) as ApiKeyView
}

// ─── Step 7：业务面板（plans / quizzes / srs） ─────────────────────────

export async function listPlans(): Promise<PlanSummary[]> {
  const res = await apiFetch('/api/plans')
  await _ensureOk(res)
  return ((await res.json()) as PlanListResponse).plans
}

export async function getActivePlan(): Promise<Plan | null> {
  const res = await apiFetch('/api/plans/active')
  await _ensureOk(res)
  return (await res.json()) as Plan | null
}

export async function getPlan(planId: number): Promise<Plan> {
  const res = await apiFetch(`/api/plans/${planId}`)
  await _ensureOk(res)
  return (await res.json()) as Plan
}

export async function createPlan(input: CreatePlanInput): Promise<Plan> {
  const res = await apiFetch('/api/plans', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  await _ensureOk(res)
  return (await res.json()) as Plan
}

export async function updatePlanTask(
  planId: number,
  taskId: number,
  status: string,
  note = '',
): Promise<Plan> {
  const res = await apiFetch(`/api/plans/${planId}/tasks/${taskId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, note }),
  })
  await _ensureOk(res)
  return (await res.json()) as Plan
}

export async function activatePlan(planId: number): Promise<Plan> {
  const res = await apiFetch(`/api/plans/${planId}/activate`, { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as Plan
}

export async function abandonPlan(planId: number): Promise<Plan> {
  const res = await apiFetch(`/api/plans/${planId}/abandon`, { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as Plan
}

export async function listQuizzes(): Promise<QuizSetSummary[]> {
  const res = await apiFetch('/api/quizzes')
  await _ensureOk(res)
  return ((await res.json()) as QuizListResponse).quizzes
}

export async function getQuiz(quizSetId: number): Promise<QuizSet> {
  const res = await apiFetch(`/api/quizzes/${quizSetId}`)
  await _ensureOk(res)
  return (await res.json()) as QuizSet
}

export async function submitQuiz(
  quizSetId: number,
  answers: QuizAnswerInput[],
): Promise<QuizSet> {
  const res = await apiFetch(`/api/quizzes/${quizSetId}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  })
  await _ensureOk(res)
  return (await res.json()) as QuizSet
}

export async function archiveQuiz(quizSetId: number): Promise<QuizSet> {
  const res = await apiFetch(`/api/quizzes/${quizSetId}/archive`, { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as QuizSet
}

export async function listSRSDue(limit?: number): Promise<SRSCard[]> {
  const url = limit ? `/api/srs/due?limit=${limit}` : '/api/srs/due'
  const res = await apiFetch(url)
  await _ensureOk(res)
  return ((await res.json()) as SRSCardListResponse).cards
}

export async function listSRSCards(): Promise<SRSCard[]> {
  const res = await apiFetch('/api/srs/cards')
  await _ensureOk(res)
  return ((await res.json()) as SRSCardListResponse).cards
}

export async function getSRSCard(cardId: number): Promise<SRSCard> {
  const res = await apiFetch(`/api/srs/cards/${cardId}`)
  await _ensureOk(res)
  return (await res.json()) as SRSCard
}

export async function createSRSCard(input: {
  front: string
  back: string
  note?: string
}): Promise<SRSCard> {
  const res = await apiFetch('/api/srs/cards', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  await _ensureOk(res)
  return (await res.json()) as SRSCard
}

export async function reviewSRSCard(
  cardId: number,
  rating: SRSRating,
): Promise<SRSCard> {
  const res = await apiFetch(`/api/srs/cards/${cardId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating }),
  })
  await _ensureOk(res)
  return (await res.json()) as SRSCard
}

export async function setSRSCardStatus(
  cardId: number,
  action: 'suspend' | 'resume' | 'archive',
): Promise<SRSCard> {
  const res = await apiFetch(`/api/srs/cards/${cardId}/${action}`, { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as SRSCard
}

// ─── Step 8：认证（注册 / 登录 / 退出 / 当前用户） ─────────────────────

/** 拉当前登录用户；未登录返回 null（不触发全局 401 跳转）。 */
export async function getMe(): Promise<UserInfo | null> {
  const res = await apiFetch('/api/auth/me')
  if (res.status === 401) return null
  await _ensureOk(res)
  return (await res.json()) as UserInfo
}

export async function login(username: string, password: string): Promise<UserInfo> {
  const res = await apiFetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  await _ensureOk(res)
  return ((await res.json()) as AuthResponse).user
}

export async function register(username: string, password: string): Promise<UserInfo> {
  const res = await apiFetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  await _ensureOk(res)
  return ((await res.json()) as AuthResponse).user
}

export async function logout(): Promise<void> {
  const res = await apiFetch('/api/auth/logout', { method: 'POST' })
  await _ensureOk(res)
}

export async function updateUsername(username: string): Promise<UserInfo> {
  const res = await apiFetch('/api/auth/username', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  })
  await _ensureOk(res)
  return (await res.json()) as UserInfo
}

export async function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<void> {
  const res = await apiFetch('/api/auth/password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  })
  await _ensureOk(res)
}

export async function listUsers(): Promise<UserInfo[]> {
  const res = await apiFetch('/api/admin/users')
  await _ensureOk(res)
  return ((await res.json()) as { users: UserInfo[] }).users
}

export async function deleteUser(userId: number): Promise<void> {
  const res = await apiFetch(`/api/admin/users/${userId}`, { method: 'DELETE' })
  await _ensureOk(res)
}

export async function deleteOwnAccount(): Promise<void> {
  const res = await apiFetch('/api/auth/me', { method: 'DELETE' })
  await _ensureOk(res)
}

export async function getLlmPrefs(): Promise<LlmPrefs> {
  const res = await apiFetch('/api/auth/llm-prefs')
  await _ensureOk(res)
  return (await res.json()) as LlmPrefs
}

export async function patchLlmPrefs(update: LlmPrefsUpdate): Promise<LlmPrefs> {
  const res = await apiFetch('/api/auth/llm-prefs', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  })
  await _ensureOk(res)
  return (await res.json()) as LlmPrefs
}

// ─── Step 9：Token 用量统计（iter_11） ─────────────────────────────────────
// scope='mine' 走本人端点；scope='all' 走 admin 全员端点（仅 admin 可见）。

type UsageScope = 'mine' | 'all'

function _usagePrefix(scope: UsageScope): string {
  return scope === 'all' ? '/api/usage/admin' : '/api/usage'
}

export async function getUsageSummary(
  range: string,
  scope: UsageScope = 'mine',
): Promise<UsageSummary> {
  const res = await apiFetch(`${_usagePrefix(scope)}/summary?range=${range}`)
  await _ensureOk(res)
  return (await res.json()) as UsageSummary
}

export async function getUsageSeries(
  range: string,
  groupBy: string,
  scope: UsageScope = 'mine',
): Promise<UsageSeries> {
  const res = await apiFetch(
    `${_usagePrefix(scope)}/series?range=${range}&group_by=${groupBy}`,
  )
  await _ensureOk(res)
  return (await res.json()) as UsageSeries
}

export async function getUsageEvents(
  range: string,
  opts: { scope?: UsageScope; modelId?: string; userId?: number; limit?: number; offset?: number } = {},
): Promise<UsageEvents> {
  const { scope = 'mine', modelId, userId, limit = 50, offset = 0 } = opts
  const params = new URLSearchParams({ range, limit: String(limit), offset: String(offset) })
  if (modelId) params.set('model_id', modelId)
  if (userId != null) params.set('user_id', String(userId))
  const res = await apiFetch(`${_usagePrefix(scope)}/events?${params.toString()}`)
  await _ensureOk(res)
  return (await res.json()) as UsageEvents
}

export function usageEventsCsvUrl(
  range: string,
  opts: { scope?: UsageScope; modelId?: string; userId?: number } = {},
): string {
  const { scope = 'mine', modelId, userId } = opts
  const params = new URLSearchParams({ range })
  if (modelId) params.set('model_id', modelId)
  if (userId != null) params.set('user_id', String(userId))
  return `${_usagePrefix(scope)}/events.csv?${params.toString()}`
}

export async function getUsageUsers(range: string): Promise<UserUsageList> {
  const res = await apiFetch(`/api/usage/admin/users?range=${range}`)
  await _ensureOk(res)
  return (await res.json()) as UserUsageList
}

export async function getPricing(): Promise<PricingResponse> {
  const res = await apiFetch('/api/usage/pricing')
  await _ensureOk(res)
  return (await res.json()) as PricingResponse
}

export async function putPricing(items: PricingUpdateItem[]): Promise<PricingResponse> {
  const res = await apiFetch('/api/usage/pricing', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
  await _ensureOk(res)
  return (await res.json()) as PricingResponse
}

// ─── 降本看板 + 模型路由候选池（iter_14）─────────────────────────────────

export async function getSavingsSummary(
  range: string,
  scope: UsageScope = 'mine',
): Promise<SavingsSummary> {
  const res = await apiFetch(`${_usagePrefix(scope)}/savings?range=${range}`)
  await _ensureOk(res)
  return (await res.json()) as SavingsSummary
}

export async function getSavingsSeries(
  range: string,
  scope: UsageScope = 'mine',
): Promise<SavingsSeries> {
  const res = await apiFetch(`${_usagePrefix(scope)}/savings/series?range=${range}`)
  await _ensureOk(res)
  return (await res.json()) as SavingsSeries
}

export async function getRoutingPool(): Promise<RoutingPoolResponse> {
  const res = await apiFetch('/api/routing/pool')
  await _ensureOk(res)
  return (await res.json()) as RoutingPoolResponse
}

export async function putRoutingPool(modelIds: string[]): Promise<RoutingPoolResponse> {
  const res = await apiFetch('/api/routing/pool', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_ids: modelIds }),
  })
  await _ensureOk(res)
  return (await res.json()) as RoutingPoolResponse
}

// ─── 评估 + 可观测（质量看板，iter_14）─────────────────────────────────

export async function getTraceOverview(
  range: string,
  scope: 'mine' | 'all' = 'mine',
): Promise<TraceOverview> {
  const res = await apiFetch(`/api/eval/trace/overview?range=${range}&scope=${scope}`)
  await _ensureOk(res)
  return (await res.json()) as TraceOverview
}

export async function getTraceSeries(
  range: string,
  scope: 'mine' | 'all' = 'mine',
): Promise<TraceSeries> {
  const res = await apiFetch(`/api/eval/trace/series?range=${range}&scope=${scope}`)
  await _ensureOk(res)
  return (await res.json()) as TraceSeries
}

export async function getTraceList(
  range: string,
  opts: { scope?: 'mine' | 'all'; limit?: number; offset?: number } = {},
): Promise<TraceList> {
  const { scope = 'mine', limit = 30, offset = 0 } = opts
  const res = await apiFetch(
    `/api/eval/trace/list?range=${range}&scope=${scope}&limit=${limit}&offset=${offset}`,
  )
  await _ensureOk(res)
  return (await res.json()) as TraceList
}

export async function getTraceDetail(traceId: string): Promise<TraceDetail> {
  const res = await apiFetch(`/api/eval/trace/${encodeURIComponent(traceId)}`)
  await _ensureOk(res)
  return (await res.json()) as TraceDetail
}

export async function getGolden(
  opts: {
    status?: string
    source?: string
    doc_id?: string
    source_contains?: string
    limit?: number
    offset?: number
  } = {},
): Promise<GoldenList> {
  const params = new URLSearchParams()
  if (opts.status) params.set('status', opts.status)
  if (opts.source) params.set('source', opts.source)
  if (opts.doc_id) params.set('doc_id', opts.doc_id)
  if (opts.source_contains) params.set('source_contains', opts.source_contains)
  params.set('limit', String(opts.limit ?? 50))
  params.set('offset', String(opts.offset ?? 0))
  const res = await apiFetch(`/api/eval/golden?${params.toString()}`)
  await _ensureOk(res)
  return (await res.json()) as GoldenList
}

export async function createGolden(input: GoldenCreateInput): Promise<GoldenItem> {
  const res = await apiFetch('/api/eval/golden', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  await _ensureOk(res)
  return (await res.json()) as GoldenItem
}

export async function updateGolden(
  id: number,
  input: GoldenUpdateInput,
): Promise<GoldenItem> {
  const res = await apiFetch(`/api/eval/golden/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  await _ensureOk(res)
  return (await res.json()) as GoldenItem
}

export async function deleteGolden(id: number): Promise<{ deleted: boolean }> {
  const res = await apiFetch(`/api/eval/golden/${id}`, { method: 'DELETE' })
  await _ensureOk(res)
  return (await res.json()) as { deleted: boolean }
}

export async function importGolden(): Promise<{ added: number; source: string }> {
  const res = await apiFetch('/api/eval/golden/import', { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as { added: number; source: string }
}

// File System Access API 的最小类型（避免 any；仅 Chromium 系支持）
type SaveFilePicker = (opts?: {
  suggestedName?: string
  types?: { description?: string; accept: Record<string, string[]> }[]
}) => Promise<{
  createWritable: () => Promise<{
    write: (data: Blob) => Promise<void>
    close: () => Promise<void>
  }>
}>

// 导出全部 golden 为 json：优先弹"另存为"让用户选目录；不支持则回退默认下载目录。
// 返回 true=已保存，false=用户取消。
export async function exportGolden(): Promise<boolean> {
  const suggestedName = `golden-export-${Date.now()}.json`
  const picker = (window as unknown as { showSaveFilePicker?: SaveFilePicker }).showSaveFilePicker
  // 先在用户手势内弹保存对话框（保住 transient activation），再去取数据
  let handle: Awaited<ReturnType<SaveFilePicker>> | null = null
  if (typeof picker === 'function') {
    try {
      handle = await picker({
        suggestedName,
        types: [{ description: 'JSON', accept: { 'application/json': ['.json'] } }],
      })
    } catch (e) {
      if ((e as DOMException)?.name === 'AbortError') return false // 用户取消
      handle = null // 其它失败 → 回退普通下载
    }
  }

  const res = await apiFetch('/api/eval/golden/export')
  await _ensureOk(res)
  const blob = await res.blob()

  if (handle) {
    const w = await handle.createWritable()
    await w.write(blob)
    await w.close()
    return true
  }
  // 回退：浏览器默认下载目录
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = suggestedName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return true
}

// 为某已入库文档手动生成 golden 候选（pending）
export async function getGoldenGenOptions(): Promise<GoldenGenOptions> {
  const res = await apiFetch('/api/eval/golden/gen-options')
  await _ensureOk(res)
  return (await res.json()) as GoldenGenOptions
}

export type GenerateGoldenOpts = {
  goldenLlm?: string
  goldenMaxQ?: number
}

export async function generateGolden(
  model: string,
  source: string,
  docId: string,
  golden?: GenerateGoldenOpts,
): Promise<{ generated: number; removed_pending: number }> {
  const res = await apiFetch('/api/eval/golden/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      source,
      doc_id: docId,
      golden_llm: golden?.goldenLlm,
      golden_max_q: golden?.goldenMaxQ,
    }),
  })
  await _ensureOk(res)
  return (await res.json()) as { generated: number; removed_pending: number }
}

export async function getReports(): Promise<ReportList> {
  const res = await apiFetch('/api/eval/reports')
  await _ensureOk(res)
  return (await res.json()) as ReportList
}

export async function getReportContent(name: string): Promise<ReportContent> {
  const res = await apiFetch(`/api/eval/reports/content?name=${encodeURIComponent(name)}`)
  await _ensureOk(res)
  return (await res.json()) as ReportContent
}

export async function getSecuritySummary(): Promise<SecuritySummary> {
  const res = await apiFetch('/api/eval/security/summary')
  await _ensureOk(res)
  return (await res.json()) as SecuritySummary
}

export async function getSecurityTrend(limit = 30): Promise<SecurityTrend> {
  const res = await apiFetch(`/api/eval/security/trend?limit=${limit}`)
  await _ensureOk(res)
  return (await res.json()) as SecurityTrend
}

export async function getSecurityRuntimeSummary(
  range = '30d',
  limit = 50,
): Promise<SecurityRuntimeSummary> {
  const res = await apiFetch(`/api/eval/security/runtime/summary?range=${range}&limit=${limit}`)
  await _ensureOk(res)
  return (await res.json()) as SecurityRuntimeSummary
}

export async function getSecurityRuntimeEvents(
  opts: SecurityEventsQuery = {},
): Promise<SecurityEventPage> {
  const {
    range = '30d', tsFrom, tsTo, eventType, userId,
    sortBy = 'created_at', desc = true, limit = 10, offset = 0,
  } = opts
  const p = new URLSearchParams({
    range, sort_by: sortBy, desc: String(desc),
    limit: String(limit), offset: String(offset),
  })
  if (tsFrom != null) p.set('ts_from', String(tsFrom))
  if (tsTo != null) p.set('ts_to', String(tsTo))
  if (eventType) p.set('event_type', eventType)
  if (userId != null) p.set('user_id', String(userId))
  const res = await apiFetch(`/api/eval/security/runtime/events?${p.toString()}`)
  await _ensureOk(res)
  return (await res.json()) as SecurityEventPage
}

// ─── 离线评估：触发 / 状态 / 取消 / 通用摘要 ──────────────────────────────

export async function runEval(req: EvalRunRequest): Promise<EvalRunStatus> {
  const res = await apiFetch('/api/eval/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  await _ensureOk(res)
  return (await res.json()) as EvalRunStatus
}

export async function getEvalRunStatus(): Promise<EvalRunStatus> {
  const res = await apiFetch('/api/eval/run/status')
  await _ensureOk(res)
  return (await res.json()) as EvalRunStatus
}

export async function cancelEval(): Promise<EvalRunStatus> {
  const res = await apiFetch('/api/eval/run/cancel', { method: 'POST' })
  await _ensureOk(res)
  return (await res.json()) as EvalRunStatus
}

export async function getEvalSummary(task: string, report?: string): Promise<EvalSummary> {
  const q = new URLSearchParams({ task })
  if (report) q.set('report', report)
  const res = await apiFetch(`/api/eval/summary?${q.toString()}`)
  await _ensureOk(res)
  return (await res.json()) as EvalSummary
}

// ─── 数据库（/admin/db/*，仅 admin，只读）────────────────────────────────

export async function getChromaCollections(): Promise<ChromaCollections> {
  const res = await apiFetch('/api/admin/db/chroma/collections')
  await _ensureOk(res)
  return (await res.json()) as ChromaCollections
}

export async function getChromaItems(
  name: string,
  opts: ItemsQuery = {},
): Promise<ChromaItemsPage> {
  const { limit = 50, offset = 0, filenameQ, bodyQ, tsFrom, tsTo, sortBy, desc } = opts
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (filenameQ) params.set('filename_q', filenameQ)
  if (bodyQ) params.set('body_q', bodyQ)
  if (tsFrom != null) params.set('ts_from', String(tsFrom))
  if (tsTo != null) params.set('ts_to', String(tsTo))
  if (sortBy) params.set('sort_by', sortBy)
  if (desc) params.set('desc', 'true')
  const res = await apiFetch(
    `/api/admin/db/chroma/${encodeURIComponent(name)}/items?${params.toString()}`,
  )
  await _ensureOk(res)
  return (await res.json()) as ChromaItemsPage
}

export async function getChromaItem(
  name: string,
  itemId: string,
): Promise<ChromaItemDetail> {
  const res = await apiFetch(
    `/api/admin/db/chroma/${encodeURIComponent(name)}/items/${encodeURIComponent(itemId)}`,
  )
  await _ensureOk(res)
  return (await res.json()) as ChromaItemDetail
}

export async function getBm25Indexes(): Promise<Bm25Indexes> {
  const res = await apiFetch('/api/admin/db/bm25/indexes')
  await _ensureOk(res)
  return (await res.json()) as Bm25Indexes
}

export async function getBm25Docs(
  collection: string,
  opts: ItemsQuery = {},
): Promise<Bm25DocsPage> {
  const { limit = 50, offset = 0, filenameQ, bodyQ, tsFrom, tsTo, sortBy, desc } = opts
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (filenameQ) params.set('filename_q', filenameQ)
  if (bodyQ) params.set('body_q', bodyQ)
  if (tsFrom != null) params.set('ts_from', String(tsFrom))
  if (tsTo != null) params.set('ts_to', String(tsTo))
  if (sortBy) params.set('sort_by', sortBy)
  if (desc) params.set('desc', 'true')
  const res = await apiFetch(
    `/api/admin/db/bm25/${encodeURIComponent(collection)}/docs?${params.toString()}`,
  )
  await _ensureOk(res)
  return (await res.json()) as Bm25DocsPage
}

export async function getBm25Doc(
  collection: string,
  docId: string,
): Promise<Bm25DocDetail> {
  const res = await apiFetch(
    `/api/admin/db/bm25/${encodeURIComponent(collection)}/docs/${encodeURIComponent(docId)}`,
  )
  await _ensureOk(res)
  return (await res.json()) as Bm25DocDetail
}

export async function getSqliteDatabases(): Promise<SqliteDatabases> {
  const res = await apiFetch('/api/admin/db/sqlite/databases')
  await _ensureOk(res)
  return (await res.json()) as SqliteDatabases
}

export type SqliteRowsQuery = {
  limit?: number
  offset?: number
  userId?: number
  timeCol?: string
  tsFrom?: number
  tsTo?: number
  sortBy?: string
  desc?: boolean
}

// ── DB 维护（破坏性，admin）──────────────────────────────────────────────

export async function getPrunePreview(days: number): Promise<PruneResult> {
  const res = await apiFetch(`/api/admin/db/maintenance/prune/preview?days=${days}`)
  await _ensureOk(res)
  return (await res.json()) as PruneResult
}

export async function runPrune(days: number): Promise<PruneResult> {
  const res = await apiFetch('/api/admin/db/maintenance/prune', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ days }),
  })
  await _ensureOk(res)
  return (await res.json()) as PruneResult
}

export async function getPurgeUserPreview(userId: number): Promise<PurgePreview> {
  const res = await apiFetch(`/api/admin/db/maintenance/purge-user/preview?user_id=${userId}`)
  await _ensureOk(res)
  return (await res.json()) as PurgePreview
}

export async function runPurgeUser(
  userId: number,
  selections: PurgeSelection[],
): Promise<PurgeResult> {
  const res = await apiFetch('/api/admin/db/maintenance/purge-user', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, selections }),
  })
  await _ensureOk(res)
  return (await res.json()) as PurgeResult
}

export async function runVacuum(dbKey?: string): Promise<VacuumResult> {
  const res = await apiFetch('/api/admin/db/maintenance/vacuum', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(dbKey ? { db_key: dbKey } : {}),
  })
  await _ensureOk(res)
  return (await res.json()) as VacuumResult
}

export async function getOrphanSegmentsPreview(): Promise<OrphanSegmentsPreview> {
  const res = await apiFetch('/api/admin/db/maintenance/orphan-segments/preview')
  await _ensureOk(res)
  return (await res.json()) as OrphanSegmentsPreview
}

export async function cleanupOrphanSegments(): Promise<OrphanCleanupResult> {
  const res = await apiFetch('/api/admin/db/maintenance/orphan-segments', {
    method: 'POST',
  })
  await _ensureOk(res)
  return (await res.json()) as OrphanCleanupResult
}

export async function getSqliteTableRows(
  dbKey: string,
  table: string,
  opts: SqliteRowsQuery = {},
): Promise<SqliteTableRows> {
  const { limit = 50, offset = 0, userId, timeCol, tsFrom, tsTo, sortBy, desc } = opts
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (userId != null) params.set('user_id', String(userId))
  if (timeCol) params.set('time_col', timeCol)
  if (tsFrom != null) params.set('ts_from', String(tsFrom))
  if (tsTo != null) params.set('ts_to', String(tsTo))
  if (sortBy) params.set('sort_by', sortBy)
  if (desc) params.set('desc', 'true')
  const res = await apiFetch(
    `/api/admin/db/sqlite/${encodeURIComponent(dbKey)}/${encodeURIComponent(table)}?${params.toString()}`,
  )
  await _ensureOk(res)
  return (await res.json()) as SqliteTableRows
}

// ─── 运行时数据备份（/admin/backup/*，仅 admin）────────────────────────

export async function listBackups(): Promise<BackupListResponse> {
  const res = await apiFetch('/api/admin/backup/list')
  await _ensureOk(res)
  return (await res.json()) as BackupListResponse
}

export async function createBackup(categories: string[]): Promise<BackupSnapshot> {
  const res = await apiFetch('/api/admin/backup/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ categories }),
  })
  await _ensureOk(res)
  return (await res.json()) as BackupSnapshot
}

export async function deleteBackup(name: string): Promise<void> {
  const res = await apiFetch(`/api/admin/backup/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  await _ensureOk(res)
}

export async function restoreBackup(file: File): Promise<RestoreResponse> {
  const form = new FormData()
  form.append('file', file)
  const res = await apiFetch('/api/admin/backup/restore', {
    method: 'POST',
    body: form,
  })
  await _ensureOk(res)
  return (await res.json()) as RestoreResponse
}

// 下载走浏览器原生导航（GET + cookie 凭证），返回拼好的 URL 供 <a> 使用。
export function backupDownloadUrl(name: string): string {
  return `/api/admin/backup/download/${encodeURIComponent(name)}`
}
