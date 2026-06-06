import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CalendarRange,
  Check,
  ChevronDown,
  Layers,
  ListChecks,
  MessageSquare,
  PanelRightClose,
  Plus,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ChatView } from '@/components/chat/ChatView'
import { PlansView } from '@/components/business/PlansView'
import { QuizzesView } from '@/components/business/QuizzesView'
import { SRSView } from '@/components/business/SRSView'
import type { Message } from '@/types/chat'
import type { Session } from '@/types/session'
import { cn } from '@/lib/utils'

type MasteryTab = 'plans' | 'quizzes' | 'srs'

const TABS: { key: MasteryTab; label: string; icon: typeof CalendarRange }[] = [
  { key: 'plans', label: '学习计划', icon: CalendarRange },
  { key: 'quizzes', label: '测验', icon: ListChecks },
  { key: 'srs', label: '复习', icon: Layers },
]

const INTRO_DISMISSED_KEY = 'agenta:mastery:introDismissed'
const CHAT_OPEN_KEY = 'agenta:mastery:chatOpen'
const CHAT_WIDTH_KEY = 'agenta:mastery:chatWidth'
const MIN_CHAT_WIDTH = 320
const MAX_CHAT_WIDTH = 896
const DEFAULT_CHAT_WIDTH = 400

// 与 ChatView 一致的聊天 props（从 App 的 useChat 透传下来）+ session 切换
export type MasteryViewProps = {
  sessionId: string | null
  sessions: Session[]
  messages: Message[]
  inFlight: boolean
  onSelectSession: (id: string) => void
  onCreateSession: () => void
  onSend: (text: string) => void
  onStop: () => void
  onRegenerate: (assistantId: string) => void
  onEditResend: (userId: string, newText: string) => void
  onResendUser: (userId: string) => void
  onSwitchVersion: (assistantId: string, index: number) => void
}

export function MasteryView(props: MasteryViewProps) {
  const { inFlight, sessions, sessionId } = props
  const [tab, setTab] = useState<MasteryTab>('plans')
  const [introDismissed, setIntroDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(INTRO_DISMISSED_KEY) === '1'
    } catch {
      return false
    }
  })
  const [chatOpen, setChatOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(CHAT_OPEN_KEY) !== '0'
    } catch {
      return true
    }
  })
  const [chatWidth, setChatWidth] = useState<number>(() => {
    try {
      const raw = Number(localStorage.getItem(CHAT_WIDTH_KEY))
      if (raw >= MIN_CHAT_WIDTH && raw <= MAX_CHAT_WIDTH) return raw
    } catch {
      // 忽略
    }
    return DEFAULT_CHAT_WIDTH
  })

  // 对话回合结束（inFlight true→false）后刷新当前 Tab：
  // 用递增 nonce 作 key 强制子面板重挂载，重新拉数据，让"出题 / 拟计划"结果立即出现。
  const [refreshNonce, setRefreshNonce] = useState(0)
  const prevInFlight = useRef(inFlight)
  useEffect(() => {
    if (prevInFlight.current && !inFlight) {
      setRefreshNonce((n) => n + 1)
    }
    prevInFlight.current = inFlight
  }, [inFlight])

  // ─── 拖拽调整聊天面板宽度 ───────────────────────────────────────────────
  const draggingRef = useRef(false)
  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'

    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return
      // 面板贴右边，宽度 = 视口宽 - 鼠标 X
      const next = Math.min(
        MAX_CHAT_WIDTH,
        Math.max(MIN_CHAT_WIDTH, window.innerWidth - ev.clientX),
      )
      setChatWidth(next)
    }
    const onUp = () => {
      draggingRef.current = false
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      setChatWidth((w) => {
        try {
          localStorage.setItem(CHAT_WIDTH_KEY, String(w))
        } catch {
          // 忽略
        }
        return w
      })
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [])

  const dismissIntro = () => {
    setIntroDismissed(true)
    try {
      localStorage.setItem(INTRO_DISMISSED_KEY, '1')
    } catch {
      // 隐私模式下忽略
    }
  }

  const toggleChat = () => {
    setChatOpen((prev) => {
      const next = !prev
      try {
        localStorage.setItem(CHAT_OPEN_KEY, next ? '1' : '0')
      } catch {
        // 隐私模式下忽略
      }
      return next
    })
  }

  const activeSession = sessions.find((s) => s.id === sessionId)

  return (
    <div className="flex h-full flex-1 overflow-hidden">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-start justify-between border-b border-border px-6 py-3">
          <div>
            <h1 className="text-base font-semibold tracking-tight">学而时习</h1>
            <p className="text-xs text-muted-foreground">
              定计划 · 做测验 · 间隔复习，三步把知识学透
            </p>
          </div>
          {!chatOpen && (
            <Button size="sm" variant="outline" onClick={toggleChat}>
              <MessageSquare className="mr-1 h-4 w-4" />
              AI 助手
            </Button>
          )}
        </header>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto flex max-w-4xl flex-col space-y-4">
            {!introDismissed && (
              <div className="relative rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
                <button
                  onClick={dismissIntro}
                  className="absolute right-2 top-2 rounded p-1 text-muted-foreground hover:bg-accent"
                  aria-label="关闭引导"
                >
                  <X className="h-4 w-4" />
                </button>
                <p className="font-medium">怎么用「学而时习」</p>
                <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-muted-foreground">
                  <li><b>定计划</b>：设一个学习目标，拆成阶段任务，逐条勾掉。</li>
                  <li><b>做测验</b>：用右侧 AI 助手基于知识库出题（"考我 5 道 attention 的题"），回到这里作答、自动批改。</li>
                  <li><b>间隔复习</b>：把要记的内容做成卡片，按"重来/困难/良好/容易"打分，系统自动安排下次复习时间。</li>
                </ol>
              </div>
            )}

            <div className="flex gap-1 border-b border-border">
              {TABS.map(({ key, label, icon: Icon }) => (
                <button
                  key={key}
                  onClick={() => setTab(key)}
                  className={cn(
                    'flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                    tab === key
                      ? 'border-primary text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground',
                  )}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </button>
              ))}
            </div>

            <div key={`${tab}-${refreshNonce}`}>
              {tab === 'plans' && <PlansView />}
              {tab === 'quizzes' && <QuizzesView />}
              {tab === 'srs' && <SRSView />}
            </div>
          </div>
        </div>
      </div>

      {chatOpen && (
        <>
          {/* 拖拽手柄 */}
          <div
            onMouseDown={onResizeStart}
            className="w-1 shrink-0 cursor-col-resize bg-border transition-colors hover:bg-primary/50"
            role="separator"
            aria-orientation="vertical"
          />
          <aside
            className="flex shrink-0 flex-col"
            style={{ width: chatWidth }}
          >
            <div className="flex items-center gap-1 border-b border-border px-2 py-2">
              <DropdownMenu>
                <DropdownMenuTrigger className="flex min-w-0 max-w-[220px] items-center gap-1 rounded-md px-2 py-1 text-sm hover:bg-accent">
                  <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 truncate text-left">
                    {activeSession?.title || 'New Chat'}
                  </span>
                  <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="max-h-80 w-64 overflow-y-auto">
                  {sessions.length === 0 ? (
                    <div className="px-2 py-3 text-center text-xs text-muted-foreground">
                      暂无会话
                    </div>
                  ) : (
                    sessions.map((s) => (
                      <DropdownMenuItem
                        key={s.id}
                        onClick={() => props.onSelectSession(s.id)}
                        className="gap-2"
                      >
                        <Check
                          className={cn(
                            'h-4 w-4 shrink-0',
                            s.id === sessionId ? 'opacity-100' : 'opacity-0',
                          )}
                        />
                        <span className="min-w-0 flex-1 truncate">
                          {s.title || 'New Chat'}
                        </span>
                      </DropdownMenuItem>
                    ))
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              <div className="flex-1" />
              <button
                onClick={props.onCreateSession}
                className="rounded p-1.5 text-muted-foreground hover:bg-accent"
                aria-label="新建会话"
                title="新建会话"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={toggleChat}
                className="rounded p-1.5 text-muted-foreground hover:bg-accent"
                aria-label="收起 AI 助手"
                title="收起"
              >
                <PanelRightClose className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1">
              <ChatView
                sessionId={props.sessionId}
                messages={props.messages}
                inFlight={props.inFlight}
                onSend={props.onSend}
                onStop={props.onStop}
                onRegenerate={props.onRegenerate}
                onEditResend={props.onEditResend}
                onResendUser={props.onResendUser}
                onSwitchVersion={props.onSwitchVersion}
                hideHeader
                compact
              />
            </div>
          </aside>
        </>
      )}
    </div>
  )
}
