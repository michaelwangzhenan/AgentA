import { Composer } from '@/components/chat/Composer'
import { MessageList } from '@/components/chat/MessageList'
import type { Message } from '@/types/chat'

export type ChatViewProps = {
  messages: Message[]
  inFlight: boolean
  onSend: (text: string) => void | Promise<void>
}

export function ChatView({ messages, inFlight, onSend }: ChatViewProps) {
  return (
    <div className="flex h-full flex-1 flex-col">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">AgentA</h1>
        <p className="text-xs text-muted-foreground">基于 RAG + Agent 的学习助手</p>
      </header>
      <MessageList messages={messages} />
      <Composer onSend={onSend} disabled={inFlight} />
    </div>
  )
}
