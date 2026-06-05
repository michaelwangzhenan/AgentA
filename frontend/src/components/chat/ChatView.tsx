import { useRef } from 'react'
import { Composer, type ComposerHandle } from '@/components/chat/Composer'
import { MessageList } from '@/components/chat/MessageList'
import { EmptyState, EmptyChips } from '@/components/chat/EmptyState'
import type { BubbleCallbacks } from '@/components/chat/MessageBubble'
import type { Message } from '@/types/chat'

export type ChatViewProps = {
  sessionId: string | null
  messages: Message[]
  inFlight: boolean
  onSend: (text: string) => void
  onStop: () => void
  onRegenerate: (assistantId: string) => void
  onEditResend: (userId: string, newText: string) => void
  onResendUser: (userId: string) => void
  onSwitchVersion: (assistantId: string, index: number) => void
}

export function ChatView({
  sessionId,
  messages,
  inFlight,
  onSend,
  onStop,
  onRegenerate,
  onEditResend,
  onResendUser,
  onSwitchVersion,
}: ChatViewProps) {
  const composerRef = useRef<ComposerHandle>(null)
  const cb: BubbleCallbacks = {
    inFlight,
    onRegenerate,
    onEditResend,
    onResendUser,
    onSwitchVersion,
  }

  const composer = (
    <Composer
      ref={composerRef}
      sessionId={sessionId}
      inFlight={inFlight}
      onSend={onSend}
      onStop={onStop}
    />
  )

  return (
    <div className="flex h-full flex-1 flex-col">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">AgentA</h1>
        <p className="text-xs text-muted-foreground">基于 RAG + Agent 的学习助手</p>
      </header>

      {messages.length === 0 ? (
        // 空状态：欢迎屏 + 居中 composer
        <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-4 pb-24">
          <div className="w-full max-w-3xl">
            <EmptyState />
            {composer}
            <EmptyChips onPick={(p) => composerRef.current?.fill(p)} />
          </div>
        </div>
      ) : (
        // 有消息：列表 + 沉底 composer
        <>
          <MessageList messages={messages} cb={cb} />
          {composer}
        </>
      )}
    </div>
  )
}
