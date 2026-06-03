import type { ChatRequest, ChatResponse } from '@/types/chat'

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
