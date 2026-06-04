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
  KBDeleteResponse,
  KBDocument,
  KBDocumentListResponse,
  KBUploadResponse,
} from '@/types/kb'

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

export async function streamChat(
  message: string,
  handlers: StreamHandlers,
  options: { sessionId?: string; signal?: AbortSignal } = {},
): Promise<void> {
  const { sessionId, signal } = options
  await fetchEventSource('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      ...(sessionId ? { session_id: sessionId } : {}),
    } satisfies ChatRequest),
    signal,
    openWhenHidden: true,
    async onopen(response) {
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
      if (!ev.data) return
      try {
        const event = JSON.parse(ev.data) as AgentStreamEvent
        handlers.onEvent(event)
      } catch (e) {
        console.error('[streamChat] SSE frame parse error', ev.data, e)
      }
    },
    onerror(err) {
      const e = err instanceof Error ? err : new Error(String(err))
      handlers.onError?.(e)
      throw e
    },
    onclose() {
      handlers.onClose?.()
    },
  })
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
