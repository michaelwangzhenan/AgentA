import { useEffect, useRef, useState } from 'react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '@/types/chat'

const STICK_THRESHOLD_PX = 120

type Props = {
  messages: Message[]
}

export function MessageList({ messages }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [stick, setStick] = useState(true)

  const onScroll = () => {
    const el = containerRef.current
    if (!el) return
    const fromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setStick(fromBottom < STICK_THRESHOLD_PX)
  }

  useEffect(() => {
    if (!stick) return
    const el = containerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, stick])

  return (
    <div
      ref={containerRef}
      onScroll={onScroll}
      className="flex-1 overflow-y-auto px-4 py-6"
    >
      <div className="mx-auto max-w-3xl space-y-4">
        {messages.length === 0 ? (
          <div className="py-12 text-center text-sm text-muted-foreground">
            发条消息开始对话
          </div>
        ) : null}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
      </div>
    </div>
  )
}
