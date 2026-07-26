import { useState } from 'react'
import {
  Copy,
  Download,
  Pencil,
  RotateCcw,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Loader2,
  FileText,
  Image as ImageIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { ThinkingBlock } from './ThinkingBlock'
import { PlanBlock } from './PlanBlock'
import { ResearchPanel } from './ResearchPanel'
import { ToolBlock } from './ToolBlock'
import { Markdown } from './Markdown'
import { parseSources } from './sources'
import { SourcesPanel } from './SourcesPanel'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { fileBadge } from '@/lib/attachments'
import { cn } from '@/lib/utils'
import { useWriteScope } from '@/lib/permissions'
import type { AssistantMessage, Message, MessageAttachment } from '@/types/chat'

export type BubbleCallbacks = {
  inFlight: boolean
  // 嵌入窄面板（学而时习侧栏）时让气泡占满宽度，避免右侧留白
  compact?: boolean
  onRegenerate: (assistantId: string) => void
  onEditResend: (userId: string, newText: string) => void
  onResendUser: (userId: string) => void
  onSwitchVersion: (assistantId: string, index: number) => void
}

function formatTime(ms?: number): string {
  if (!ms) return ''
  const d = new Date(ms)
  const today = new Date()
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  if (sameDay) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success('已复制')
  } catch {
    toast.error('复制失败')
  }
}

/** 把文本另存为本地 .md 文件（深度研究报告导出用）。 */
function downloadMarkdown(filename: string, content: string) {
  try {
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
    toast.success('已导出')
  } catch {
    toast.error('导出失败')
  }
}

/** 由研究问题 + 日期拼一个安全的文件名（去掉非法字符，限长）。 */
function researchFilename(query: string | undefined): string {
  const date = new Date().toISOString().slice(0, 10)
  const base = (query || '深度研究报告')
    .replace(/[\\/:*?"<>|\n\r]/g, ' ')
    .trim()
    .slice(0, 40)
  return `${base || '深度研究报告'}-${date}.md`
}

export function MessageBubble({
  message,
  cb,
}: {
  message: Message
  cb: BubbleCallbacks
}) {
  if (message.role === 'user') {
    return <UserBubble message={message} cb={cb} />
  }
  return <AssistantBubble message={message} cb={cb} />
}

// ─── 用户气泡 ──────────────────────────────────────────────────────────────

function UserBubble({
  message,
  cb,
}: {
  message: Extract<Message, { role: 'user' }>
  cb: BubbleCallbacks
}) {
  const { allowed: canWriteChat, tip: chatTip } = useWriteScope('chat')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(message.content)
  const [confirm, setConfirm] = useState<null | { text: string }>(null)

  const startEdit = () => {
    setDraft(message.content)
    setEditing(true)
  }

  if (editing) {
    return (
      <div className="flex justify-end">
        <div className="w-full max-w-[80%]">
          <Textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-20 resize-none"
          />
          <div className="mt-1.5 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
              取消
            </Button>
            <Button
              size="sm"
              disabled={!draft.trim()}
              onClick={() => {
                setEditing(false)
                setConfirm({ text: draft.trim() })
              }}
            >
              保存并重发
            </Button>
          </div>
        </div>
        <ResendConfirm
          open={!!confirm}
          onOpenChange={(o) => !o && setConfirm(null)}
          onConfirm={() => {
            if (confirm) cb.onEditResend(message.id, confirm.text)
            setConfirm(null)
          }}
        />
      </div>
    )
  }

  const attachments = message.attachments ?? []

  return (
    <div className="group flex flex-col items-end gap-1">
      {attachments.length > 0 ? <AttachmentCards items={attachments} /> : null}
      {message.content ? (
        <div className={cn(
          'rounded-2xl bg-user-bubble px-4 py-2 text-sm break-words whitespace-pre-wrap text-user-bubble-foreground',
          cb.compact ? 'max-w-[92%]' : 'max-w-[80%]',
        )}>
          {message.content}
        </div>
      ) : null}
      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        <span className="mr-1 text-[11px] text-muted-foreground">
          {formatTime(message.createdAt)}
        </span>
        <IconBtn
          label={canWriteChat ? '重发' : chatTip}
          disabled={cb.inFlight || !canWriteChat}
          onClick={() => cb.onResendUser(message.id)}
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </IconBtn>
        <IconBtn label={canWriteChat ? '编辑' : chatTip} disabled={!canWriteChat} onClick={startEdit}>
          <Pencil className="h-3.5 w-3.5" />
        </IconBtn>
        <IconBtn label="复制" onClick={() => copyText(message.content)}>
          <Copy className="h-3.5 w-3.5" />
        </IconBtn>
      </div>
    </div>
  )
}

function AttachmentCards({ items }: { items: MessageAttachment[] }) {
  return (
    <div className="flex max-w-[80%] flex-wrap justify-end gap-2">
      {items.map((a, i) => {
        const subtitle =
          a.kind === 'text'
            ? `${a.lines ?? 0} 行`
            : a.kind === 'image'
              ? '图片（未发送）'
              : '二进制（未发送）'
        return (
          <div
            key={`${a.name}-${i}`}
            className="flex w-44 items-start gap-2 rounded-xl border border-border bg-card px-3 py-2"
          >
            {a.kind === 'image' ? (
              <ImageIcon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            ) : (
              <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium" title={a.name}>
                {a.name}
              </div>
              <div className="mt-0.5 text-[11px] text-muted-foreground">{subtitle}</div>
              <span className="mt-1 inline-block rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                {fileBadge(a.name)}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ResendConfirm({
  open,
  onOpenChange,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onConfirm: () => void
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>重发这条消息？</AlertDialogTitle>
          <AlertDialogDescription>
            这会丢弃此条消息之后的所有回答和后续对话，且不可撤销。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>确认重发</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ─── 助手气泡 ──────────────────────────────────────────────────────────────

function AssistantBubble({
  message,
  cb,
}: {
  message: AssistantMessage
  cb: BubbleCallbacks
}) {
  const { allowed: canWriteChat, tip: chatTip } = useWriteScope('chat')
  const { body, sources } = parseSources(message.content)
  const versions = message.versions
  const showVersions = !!versions && versions.length > 1
  const vIndex = message.versionIndex ?? (versions ? versions.length - 1 : 0)

  const hasAny =
    message.timeline.length > 0 ||
    message.plan ||
    message.research ||
    message.content ||
    message.error

  return (
    <div className="group flex flex-col items-start gap-1">
      <div className={cn('w-full space-y-1', cb.compact ? 'max-w-full' : 'max-w-[85%]')}>
        {message.research ? <ResearchPanel research={message.research} /> : null}

        {message.plan && message.plan.length > 0 ? (
          <PlanBlock steps={message.plan} />
        ) : null}

        {/* thinking 与工具调用按发生顺序交替显示，保留每次循环 think→act 的结构 */}
        {message.timeline.map((it, i) =>
          it.kind === 'thinking' ? (
            <ThinkingBlock
              key={it.id}
              text={it.text}
              thinkingMs={it.thinkingMs}
              streaming={
                message.streaming && i === message.timeline.length - 1 && !message.content
              }
            />
          ) : (
            <ToolBlock
              key={it.call.call_id}
              call={it.call}
              planTotal={message.plan?.length}
            />
          ),
        )}

        {body ? (
          <div className="rounded-2xl bg-muted px-4 py-2 text-[15px] text-foreground break-words">
            {message.streaming ? (
              <div className="whitespace-pre-wrap">{body}</div>
            ) : (
              <Markdown>{body}</Markdown>
            )}
            {message.streaming ? <StreamingCursor /> : null}
          </div>
        ) : null}

        {message.streaming && !body ? (
          <div className="flex items-center gap-2 rounded-2xl bg-muted px-4 py-2 text-sm text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span>生成中…</span>
          </div>
        ) : null}

        <AnswerMeta message={message} />

        <SourcesPanel sources={sources} />

        {message.error ? (
          <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <div>{message.error}</div>
            <Button
              variant="outline"
              size="xs"
              disabled={cb.inFlight || !canWriteChat}
              title={canWriteChat ? undefined : chatTip}
              onClick={() => cb.onRegenerate(message.id)}
            >
              <RotateCcw className="h-3 w-3" /> 重试
            </Button>
          </div>
        ) : null}

        {!hasAny && !message.streaming ? (
          <div className="rounded-2xl bg-muted px-4 py-2 text-sm text-muted-foreground">
            （无文本输出）
          </div>
        ) : null}
      </div>

      {/* 元数据底栏占位 [§3]：模型 / 耗时 / token 待 token 统计接口接入 */}

      {/* hover 操作行（多版本时即便当前版本无正文也要保留，否则切到空版本后切不回来）*/}
      {!message.streaming && (message.content || message.error || showVersions) ? (
        <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <IconBtn label="复制" onClick={() => copyText(body)}>
            <Copy className="h-3.5 w-3.5" />
          </IconBtn>
          {message.research ? (
            <IconBtn
              label="另存为 Markdown"
              onClick={() =>
                downloadMarkdown(
                  researchFilename(message.research?.query),
                  message.content,
                )
              }
            >
              <Download className="h-3.5 w-3.5" />
            </IconBtn>
          ) : null}
          <IconBtn
            label={canWriteChat ? '重新生成' : chatTip}
            disabled={cb.inFlight || !canWriteChat}
            onClick={() => cb.onRegenerate(message.id)}
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </IconBtn>
          {showVersions ? (
            <div className="ml-1 flex items-center gap-0.5 text-[11px] text-muted-foreground">
              <button
                type="button"
                className="rounded p-0.5 hover:bg-foreground/10 disabled:opacity-40"
                disabled={vIndex <= 0}
                onClick={() => cb.onSwitchVersion(message.id, vIndex - 1)}
                aria-label="上一个版本"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="tabular-nums">
                {vIndex + 1}/{versions!.length}
              </span>
              <button
                type="button"
                className="rounded p-0.5 hover:bg-foreground/10 disabled:opacity-40"
                disabled={vIndex >= versions!.length - 1}
                onClick={() => cb.onSwitchVersion(message.id, vIndex + 1)}
                aria-label="下一个版本"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function StreamingCursor() {
  return (
    <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-foreground/70 align-middle" />
  )
}

/** 回答下方的降本标注：命中语义缓存标「缓存」，auto 路由降级标实际应答模型。 */
function AnswerMeta({ message }: { message: AssistantMessage }) {
  if (message.streaming) return null
  if (message.cached) {
    return (
      <div className="px-1">
        <span
          className="inline-flex items-center rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400"
          title="本回答直接来自语义缓存（相近问题的历史答案）；点「重新生成」可绕过缓存重新作答。"
        >
          缓存
        </span>
      </div>
    )
  }
  if (message.downgraded && message.model) {
    return (
      <div className="px-1">
        <span
          className="inline-flex items-center rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-600 dark:text-sky-400"
          title="auto 模式按问题难度自动降级到更便宜的模型作答；这是本次实际应答的模型。"
        >
          {message.model}
        </span>
      </div>
    )
  }
  return null
}

function IconBtn({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-40"
    >
      {children}
    </button>
  )
}
