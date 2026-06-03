import { useState } from 'react'
import { Composer } from '@/components/chat/Composer'
import { MessageList } from '@/components/chat/MessageList'
import { postChat } from '@/api/client'
import type { Message } from '@/types/chat'

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)

  const handleSend = async (text: string) => {
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
    }
    setMessages((prev) => [...prev, userMsg])
    setLoading(true)
    try {
      const res = await postChat(text)
      const assistantMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: res.reply,
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch (e: unknown) {
      const detail = e instanceof Error ? e.message : String(e)
      const errMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: `ERROR — ${detail}`,
      }
      setMessages((prev) => [...prev, errMsg])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">AgentA</h1>
        <p className="text-xs text-muted-foreground">
          Step 1 - 最小聊天回路（非流式）
        </p>
      </header>
      <MessageList messages={messages} loading={loading} />
      <Composer onSend={handleSend} disabled={loading} />
    </div>
  )
}

export default App
