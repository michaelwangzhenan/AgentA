import { fetchEventSource } from '@microsoft/fetch-event-source'
import type {
  AgentStreamEvent,
  ChatRequest,
  ChatResponse,
} from '@/types/chat'

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
  signal?: AbortSignal,
): Promise<void> {
  await fetchEventSource('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message } satisfies ChatRequest),
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
