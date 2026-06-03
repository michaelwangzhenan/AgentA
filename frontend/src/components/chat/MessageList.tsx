import { useEffect, useRef } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import type { Message } from '@/types/chat'

type Props = {
  messages: Message[]
  loading: boolean
}

export function MessageList({ messages, loading }: Props) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  return (
    <ScrollArea className="flex-1 px-4 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        {messages.length === 0 && !loading && (
          <div className="py-12 text-center text-sm text-muted-foreground">
            发条消息开始对话
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-2xl bg-muted px-4 py-2 text-sm text-muted-foreground">
              thinking…
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </ScrollArea>
  )
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[80%] rounded-2xl px-4 py-2 text-sm break-words whitespace-pre-wrap',
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted text-foreground',
        )}
      >
        {message.content}
      </div>
    </div>
  )
}
