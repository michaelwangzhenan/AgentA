import { Fragment, useEffect, useRef, useState } from 'react'
import { ArrowDown } from 'lucide-react'
import { MessageBubble, type BubbleCallbacks } from './MessageBubble'
import logoUrl from '@/assets/agentA_logo.svg'
import type { Message } from '@/types/chat'

const STICK_THRESHOLD_PX = 120
const TIME_GAP_MS = 30 * 60 * 1000

type Props = {
  messages: Message[]
  cb: BubbleCallbacks
}

function timeLabel(ms: number): string {
  const d = new Date(ms)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000
  const hhmm = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (ms >= startOfToday) return `今天 ${hhmm}`
  if (ms >= startOfYesterday) return `昨天 ${hhmm}`
  return `${d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })} ${hhmm}`
}

export function MessageList({ messages, cb }: Props) {
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

  const scrollToBottom = () => {
    const el = containerRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    setStick(true)
  }

  const lastStreaming =
    messages.length > 0 &&
    messages[messages.length - 1].role === 'assistant' &&
    (messages[messages.length - 1] as { streaming?: boolean }).streaming

  return (
    <div className="relative flex-1 overflow-hidden">
      <div
        ref={containerRef}
        onScroll={onScroll}
        className="h-full overflow-y-auto px-4 py-6"
      >
        <div className="mx-auto max-w-4xl space-y-4">
          {messages.map((m, i) => {
            const prev = messages[i - 1]
            const showSep =
              !!m.createdAt &&
              !!prev?.createdAt &&
              m.createdAt - prev.createdAt > TIME_GAP_MS
            return (
              <Fragment key={m.id}>
                {showSep ? (
                  <div className="flex justify-center py-1">
                    <span className="rounded-full bg-muted px-2.5 py-0.5 text-[11px] text-muted-foreground">
                      {timeLabel(m.createdAt!)}
                    </span>
                  </div>
                ) : null}
                <MessageBubble message={m} cb={cb} />
              </Fragment>
            )
          })}

          {/* 对话结尾 logo + 问候：靠左贴在应答下方，文字悬停才显示（参考 Claude 网页版） */}
          {messages.length > 0 && !lastStreaming ? (
            <div className="group -mt-6 flex items-center gap-2 pb-2">
              <img src={logoUrl} alt="AgentA" className="h-22 w-22 opacity-99" />
              <span className="rounded-full bg-muted px-3 py-0 text-base text-muted-foreground opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                你好，我是 AgentA，有什么可以帮你？
              </span>
            </div>
          ) : null}
        </div>
      </div>

      {/* 回到最新 */}
      {!stick ? (
        <button
          type="button"
          onClick={scrollToBottom}
          className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-border bg-popover px-3 py-1.5 text-xs text-foreground shadow-md transition-colors hover:bg-muted"
        >
          <ArrowDown className="h-3.5 w-3.5" /> 回到最新
        </button>
      ) : null}
    </div>
  )
}
