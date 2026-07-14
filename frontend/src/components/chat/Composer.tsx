import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from 'react'
import {
  ArrowUp,
  Brain,
  Loader2,
  Mic,
  Microscope,
  Paperclip,
  Plus,
  Square,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useDraft } from '@/hooks/useDraft'
import { useSpeechInput } from '@/hooks/useSpeechInput'
import {
  useComposerSettings,
  type ThinkingLevel,
} from '@/hooks/useComposerSettings'
import { listSkills } from '@/api/client'
import type { SkillItem } from '@/types/resources'
import type { ChatMode } from '@/types/chat'
import { cn } from '@/lib/utils'
import { generateId } from '@/lib/id'
import { toast } from 'sonner'

export type ComposerHandle = {
  fill: (text: string) => void
  focus: () => void
}

type Props = {
  sessionId: string | null
  inFlight: boolean
  onSend: (text: string, mode?: ChatMode) => void
  onStop: () => void
}

// token 估算占位：字符数 / 4（真实统计待 §3 接口），超此软上限标红
const TOKEN_SOFT_LIMIT = 8000

const THINKING_LABELS: Record<ThinkingLevel, string> = {
  off: '关',
  low: '低',
  medium: '中',
  high: '高',
}

// 能力/价位档位徽章样式（对齐后端 ModelConfig.tier）
const TIER_META: Record<string, { label: string; className: string }> = {
  min: { label: 'min', className: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400' },
  low: { label: 'low', className: 'bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300' },
  medium: { label: 'medium', className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300' },
  high: { label: 'high', className: 'bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300' },
  max: { label: 'max', className: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300' },
}

function TierBadge({ tier }: { tier?: string }) {
  const meta = tier ? TIER_META[tier] : undefined
  if (!meta) return null
  return (
    <span
      className={cn(
        'ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium leading-none',
        meta.className,
      )}
    >
      {meta.label}
    </span>
  )
}

type Attachment = {
  id: string
  file: File
  kind: 'text' | 'image' | 'other'
  previewUrl?: string
  textContent?: string
}

const TEXT_EXT = /\.(txt|md|markdown|json|ya?ml|csv|log|py|ts|tsx|js|jsx|java|go|rs|c|cpp|h|sh|sql|html|css|xml|toml|ini)$/i

function classifyFile(file: File): Attachment['kind'] {
  if (file.type.startsWith('image/')) return 'image'
  if (file.type.startsWith('text/') || TEXT_EXT.test(file.name)) return 'text'
  return 'other'
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function messageUtf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length
}

export const Composer = forwardRef<ComposerHandle, Props>(function Composer(
  { sessionId, inFlight, onSend, onStop },
  ref,
) {
  const [text, setText, clearDraft] = useDraft(sessionId)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [skills, setSkills] = useState<SkillItem[]>([])
  const [slashIndex, setSlashIndex] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const settings = useComposerSettings()
  const { supported: micSupported, listening, toggle: toggleMic } =
    useSpeechInput((t) => setText(text ? `${text} ${t}` : t))

  // 暴露 fill / focus 给父组件（空状态 chip 点击填充）
  useImperativeHandle(ref, () => ({
    fill: (t: string) => {
      setText(t)
      requestAnimationFrame(() => textareaRef.current?.focus())
    },
    focus: () => textareaRef.current?.focus(),
  }))

  // 切会话 / 卸载时 revoke 全部图片预览 URL，避免 Object URL 泄漏
  useEffect(() => {
    setAttachments((prev) => {
      for (const a of prev) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl)
      }
      return []
    })
  }, [sessionId])

  useEffect(() => {
    return () => {
      setAttachments((prev) => {
        for (const a of prev) {
          if (a.previewUrl) URL.revokeObjectURL(a.previewUrl)
        }
        return []
      })
    }
  }, [])

  // 首次加载 skills（slash 菜单用）
  useEffect(() => {
    listSkills()
      .then((r) => setSkills(r.loaded))
      .catch(() => setSkills([]))
  }, [])

  // 全局 Cmd/Ctrl+/ 聚焦发送框
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === '/') {
        e.preventDefault()
        textareaRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // textarea 自动增高
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [text])

  // ─── slash skill 菜单 ───────────────────────────────────────────────────
  const slashQuery = useMemo(() => {
    const m = /^\/(\S*)$/.exec(text)
    return m ? m[1].toLowerCase() : null
  }, [text])
  const slashMatches = useMemo(() => {
    if (slashQuery === null) return []
    return skills
      .filter((s) => s.name.toLowerCase().includes(slashQuery))
      .slice(0, 6)
  }, [slashQuery, skills])
  const slashOpen = slashMatches.length > 0
  useEffect(() => {
    setSlashIndex(0)
  }, [slashQuery])

  const applySkill = (s: SkillItem) => {
    setText(`/${s.name} `)
    requestAnimationFrame(() => textareaRef.current?.focus())
  }

  // ─── 附件 ───────────────────────────────────────────────────────────────
  const addFiles = async (files: FileList | File[]) => {
    const list = Array.from(files)
    const maxCount = settings.attachmentMaxCount
    const maxBytes = settings.messageMaxBytes
    if (attachments.length + list.length > maxCount) {
      toast.error(`附件最多 ${maxCount} 个`)
      return
    }
    const next: Attachment[] = []
    for (const file of list) {
      if (attachments.length + next.length >= maxCount) {
        toast.error(`附件最多 ${maxCount} 个`)
        break
      }
      const kind = classifyFile(file)
      if (kind === 'text' && file.size > maxBytes) {
        toast.error(`文本附件 ${file.name} 超过上限（${formatSize(maxBytes)}）`)
        continue
      }
      const att: Attachment = { id: generateId(), file, kind }
      if (kind === 'image') att.previewUrl = URL.createObjectURL(file)
      if (kind === 'text') {
        try {
          att.textContent = await file.text()
          const bodyBytes = messageUtf8Bytes(att.textContent)
          if (bodyBytes > maxBytes) {
            toast.error(`文本附件 ${file.name} 超过上限（${formatSize(maxBytes)}）`)
            continue
          }
        } catch {
          att.textContent = ''
        }
      }
      next.push(att)
    }
    if (!next.length) return
    setAttachments((prev) => [...prev, ...next])
  }

  const removeAttachment = (id: string) => {
    setAttachments((prev) => {
      const target = prev.find((a) => a.id === id)
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
      return prev.filter((a) => a.id !== id)
    })
  }

  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const imgs = Array.from(e.clipboardData.files).filter((f) =>
      f.type.startsWith('image/'),
    )
    if (imgs.length) {
      e.preventDefault()
      void addFiles(imgs)
    }
  }

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length) void addFiles(e.dataTransfer.files)
  }

  // ─── 发送 ───────────────────────────────────────────────────────────────
  const buildMessage = (): string => {
    let body = text.trim()
    const textAtts = attachments.filter((a) => a.kind === 'text')
    const otherAtts = attachments.filter((a) => a.kind !== 'text')
    for (const a of textAtts) {
      body += `\n\n附件 \`${a.file.name}\`：\n\`\`\`\n${a.textContent ?? ''}\n\`\`\``
    }
    // 图片 / 二进制附件：当前无多模态后端，仅提示未发送（见 §2.2 决策记录）
    for (const a of otherAtts) {
      body += `\n\n[附件 ${a.file.name}（${a.kind === 'image' ? '图片' : '二进制'}）未随消息发送：暂不支持多模态]`
    }
    return body.trim()
  }

  const tokenEstimate = useMemo(() => {
    const textChars = attachments
      .filter((a) => a.kind === 'text')
      .reduce((n, a) => n + (a.textContent?.length ?? 0), 0)
    return Math.ceil((text.length + textChars) / 4)
  }, [text, attachments])

  const submit = () => {
    if (inFlight) return
    const msg = buildMessage()
    if (!msg) return
    const nbytes = messageUtf8Bytes(msg)
    if (nbytes > settings.messageMaxBytes) {
      toast.error(
        `消息过长（${formatSize(nbytes)}，上限 ${formatSize(settings.messageMaxBytes)}）`,
      )
      return
    }
    onSend(msg, settings.deepResearch ? 'deep_research' : undefined)
    clearDraft()
    setAttachments((prev) => {
      prev.forEach((a) => {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl)
        a.textContent = undefined
      })
      return []
    })
    const el = textareaRef.current
    if (el) el.style.height = 'auto'
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // slash 菜单开着时，方向键 / 回车归菜单
    if (slashOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashIndex((i) => (i + 1) % slashMatches.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashIndex((i) => (i - 1 + slashMatches.length) % slashMatches.length)
        return
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        applySkill(slashMatches[slashIndex])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setText('')
        return
      }
    }
    if (e.key === 'Escape' && inFlight) {
      e.preventDefault()
      onStop()
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const canSend = !!buildMessage()

  return (
    <div className="border-t border-border bg-background px-4 py-3">
      <div className="relative mx-auto max-w-3xl">
        {/* slash skill 菜单 */}
        {slashOpen ? (
          <div className="absolute bottom-full left-0 z-20 mb-2 w-72 overflow-hidden rounded-lg border border-border bg-popover shadow-md">
            <div className="px-3 py-1.5 text-xs text-muted-foreground">Skills</div>
            {slashMatches.map((s, i) => (
              <button
                key={s.name}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault()
                  applySkill(s)
                }}
                className={cn(
                  'flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left',
                  i === slashIndex ? 'bg-accent' : 'hover:bg-accent/60',
                )}
              >
                <span className="font-mono text-sm">/{s.name}</span>
                {s.description ? (
                  <span className="line-clamp-1 text-xs text-muted-foreground">
                    {s.description}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        ) : null}

        {/* 附件卡片行 */}
        {attachments.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachments.map((a) => (
              <div
                key={a.id}
                className="group relative flex items-center gap-2 rounded-lg border border-border bg-muted/40 py-1.5 pr-7 pl-2"
              >
                {a.kind === 'image' && a.previewUrl ? (
                  <img
                    src={a.previewUrl}
                    alt={a.file.name}
                    className="h-8 w-8 rounded object-cover"
                  />
                ) : (
                  <Paperclip className="h-4 w-4 text-muted-foreground" />
                )}
                <div className="flex flex-col">
                  <span className="max-w-[140px] truncate text-xs font-medium">
                    {a.file.name}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {formatSize(a.file.size)}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => removeAttachment(a.id)}
                  className="absolute top-1 right-1 rounded p-0.5 text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                  aria-label="移除附件"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        ) : null}

        {/* 输入区 */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={cn(
            'rounded-2xl border border-border bg-card transition-colors',
            dragOver && 'border-blue-500 ring-2 ring-blue-500/30',
          )}
        >
          <Textarea
            ref={textareaRef}
            className="max-h-[200px] min-h-11 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
            placeholder="输入消息…（Enter 发送，Shift+Enter 换行，/ 调用 skill）"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
          />

          {/* 工具条 */}
          <div className="flex items-center gap-1 px-2 pb-2">
            {/* + 上传菜单 */}
            <DropdownMenu>
              <DropdownMenuTrigger
                className="flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                aria-label="添加附件"
              >
                <Plus className="h-4 w-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" side="top" className="w-auto min-w-40">
                <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                  <Paperclip className="h-4 w-4" /> 上传文件
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* 模型选择：厂商 → 具体模型 两级菜单 */}
            <DropdownMenu>
              <DropdownMenuTrigger
                className="flex h-8 items-center gap-1 rounded-md px-2 text-sm text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                disabled={settings.loading}
              >
                {settings.activeModelLabel || '模型'}
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" side="top" className="w-auto min-w-44">
                <DropdownMenuRadioGroup
                  value={settings.activeModel}
                  onValueChange={(v) => void settings.setModel(v)}
                >
                  <DropdownMenuRadioItem value="auto">
                    <span className="flex flex-1 items-center justify-between">
                      <span className="font-medium"> Auto </span>
                    </span>
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
                <DropdownMenuSeparator />
                {settings.providers.map((p) => (
                  <DropdownMenuSub key={p.name}>
                    <DropdownMenuSubTrigger>{p.label}</DropdownMenuSubTrigger>
                    <DropdownMenuSubContent className="min-w-44">
                      <DropdownMenuRadioGroup
                        value={settings.activeModel}
                        onValueChange={(v) => void settings.setModel(v)}
                      >
                        {p.models.map((m) => {
                          const isFree = m.label.includes('(free)')
                          return (
                            <DropdownMenuRadioItem key={m.id} value={m.id}>
                              <span className="flex flex-1 items-center justify-between">
                                <span
                                  className={cn(
                                    isFree && 'font-bold text-green-600 dark:text-green-400',
                                  )}
                                >
                                  {m.label}
                                </span>
                                <TierBadge tier={m.tier} />
                              </span>
                            </DropdownMenuRadioItem>
                          )
                        })}
                      </DropdownMenuRadioGroup>
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>

            {/* 推理强度档位 */}
            <DropdownMenu>
              <DropdownMenuTrigger
                className={cn(
                  'flex h-8 items-center gap-1 rounded-md px-2 text-sm',
                  settings.thinkingSupported
                    ? 'text-muted-foreground hover:bg-foreground/10 hover:text-foreground'
                    : 'cursor-not-allowed text-muted-foreground/40',
                )}
                disabled={!settings.thinkingSupported}
                title={
                  settings.thinkingSupported
                    ? '推理强度'
                    : '当前模型不支持 thinking'
                }
              >
                <Brain className="h-4 w-4" />
                {'深度思考：'}
                {THINKING_LABELS[settings.level]}
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" side="top" className="w-auto min-w-32">
                <DropdownMenuRadioGroup
                  value={settings.level}
                  onValueChange={(v) =>
                    void settings.setLevel(v as ThinkingLevel)
                  }
                >
                  {(['off', 'low', 'medium', 'high'] as ThinkingLevel[]).map(
                    (lv) => (
                      <DropdownMenuRadioItem key={lv} value={lv}>
                        {THINKING_LABELS[lv]}
                      </DropdownMenuRadioItem>
                    ),
                  )}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* 深度研究开关：仅在全局启用时显示；耗时数分钟、费更多 token */}
            {settings.deepResearchEnabled ? (
              <button
                type="button"
                onClick={() => settings.setDeepResearch(!settings.deepResearch)}
                aria-pressed={settings.deepResearch}
                title="深度研究：拆子问题 → 并行查 KB+联网 → 反思补查 → 综述带引用的报告（耗时数分钟、费更多 token）"
                className={cn(
                  'flex h-8 items-center gap-1 rounded-md px-2 text-sm transition-colors',
                  settings.deepResearch
                    ? 'bg-violet-500/15 text-violet-600 dark:text-violet-300'
                    : 'text-muted-foreground hover:bg-foreground/10 hover:text-foreground',
                )}
              >
                <Microscope className="h-4 w-4" />
                深度研究
              </button>
            ) : null}

            <div className="ml-auto flex items-center gap-2">
              {/* token 估算占位 [§3] */}
              {text.length > 0 ? (
                <span
                  className={cn(
                    'text-[11px] tabular-nums',
                    tokenEstimate > TOKEN_SOFT_LIMIT
                      ? 'text-destructive'
                      : 'text-muted-foreground',
                  )}
                  title="token 估算（占位，待 token 统计接口接入）"
                >
                  ~{tokenEstimate} tokens
                </span>
              ) : null}

              {/* 麦克风听写 */}
              {micSupported ? (
                <button
                  type="button"
                  onClick={toggleMic}
                  className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-full',
                    listening
                      ? 'bg-red-500/15 text-red-500'
                      : 'text-muted-foreground hover:bg-foreground/10 hover:text-foreground',
                  )}
                  aria-label={listening ? '停止录音' : '语音听写'}
                >
                  <Mic className="h-4 w-4" />
                </button>
              ) : null}

              {/* 发送 / 停止 */}
              {inFlight ? (
                <Button
                  size="icon"
                  variant="secondary"
                  onClick={onStop}
                  className="h-8 w-8 rounded-full"
                  aria-label="停止生成"
                >
                  <Square className="h-3.5 w-3.5 fill-current" />
                </Button>
              ) : (
                <Button
                  size="icon"
                  onClick={submit}
                  disabled={!canSend}
                  className="h-8 w-8 rounded-full"
                  aria-label="发送"
                >
                  {settings.loading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ArrowUp className="h-4 w-4" />
                  )}
                </Button>
              )}
            </div>
          </div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) void addFiles(e.target.files)
            e.target.value = ''
          }}
        />
      </div>
    </div>
  )
})
