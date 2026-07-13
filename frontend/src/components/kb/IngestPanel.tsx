import { useEffect, useRef, useState } from 'react'
import { Check, Folder, FolderUp, Loader2, Play, Trash2, X } from 'lucide-react'

import { ingestKBFileStream } from '@/api/client'
import type { IngestProgress, KBCollection } from '@/types/kb'
import { ACCEPT_EXTENSIONS, DropZone } from '@/components/kb/DropZone'
import { GoldenGenControls } from '@/components/kb/GoldenGenControls'
import { Button } from '@/components/ui/button'
import { toast } from '@/lib/toast'

export type IngestPanelProps = {
  collections: KBCollection[]
  defaultAlias: string
  isAdmin?: boolean
  onIngested: () => void
  onGotoGolden?: () => void // 入库完成 toast 里"去 Golden 管理"链接
}

// 待入库条目：单文件 或 整个文件夹（折叠，不展开里面的文件）
type StageItem =
  | { kind: 'file'; id: string; file: File }
  | { kind: 'folder'; id: string; name: string; files: File[] }

// 单个文件的入库状态（进度细节用）
type FileState = 'pending' | 'running' | 'ok' | 'skip' | 'fail'

const SUPPORTED = new Set(ACCEPT_EXTENSIONS)

function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}

function relPathOf(f: File): string {
  return (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
}

export function IngestPanel({
  collections,
  defaultAlias,
  isAdmin = false,
  onIngested,
  onGotoGolden,
}: IngestPanelProps) {
  const [target, setTarget] = useState('')
  const userPickedRef = useRef(false)
  const [goldenLlm, setGoldenLlm] = useState('none')
  const [goldenMaxQ, setGoldenMaxQ] = useState(3)
  const [items, setItems] = useState<StageItem[]>([])
  const [running, setRunning] = useState(false)
  const [current, setCurrent] = useState('')
  const [chunk, setChunk] = useState<IngestProgress | null>(null)
  const [stats, setStats] = useState({ done: 0, total: 0, ok: 0, skip: 0, fail: 0 })
  const [status, setStatus] = useState<Record<string, FileState>>({})
  const folderRef = useRef<HTMLInputElement | null>(null)
  // 取消入库：cancelRef 让批次循环在文件间停下；abortRef 中断当前在传的请求
  const cancelRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)

  // 默认库加载后回填下拉（用户手动改过则不再覆盖）
  useEffect(() => {
    if (defaultAlias && !userPickedRef.current) setTarget(defaultAlias)
  }, [defaultAlias])

  // webkitdirectory 非标准属性，React 类型里没有，用 ref 直接设
  useEffect(() => {
    folderRef.current?.setAttribute('webkitdirectory', '')
  }, [])

  // 拖拽 / 点击选文件：每个文件单独入列，按文件名去重
  const addFiles = (files: File[]) => {
    const accepted = files.filter((f) => SUPPORTED.has(extOf(f.name)))
    const skipped = files.length - accepted.length
    if (skipped > 0) toast.info(`已忽略 ${skipped} 个不支持格式的文件`)
    setItems((prev) => {
      const seen = new Set(prev.filter((i) => i.kind === 'file').map((i) => i.id))
      const next = [...prev]
      for (const f of accepted) {
        const id = `file:${f.name}`
        if (!seen.has(id)) {
          seen.add(id)
          next.push({ kind: 'file', id, file: f })
        }
      }
      return next
    })
  }

  // 选文件夹：整个文件夹折叠成一个节点，按顶层文件夹名去重（重复选则覆盖）
  const addFolder = (files: File[]) => {
    const accepted = files.filter((f) => SUPPORTED.has(extOf(f.name)))
    if (accepted.length === 0) {
      toast.info('该文件夹未发现可入库的文件')
      return
    }
    const top = relPathOf(accepted[0]).split('/')[0] || '文件夹'
    const id = `folder:${top}`
    setItems((prev) => {
      const without = prev.filter((i) => i.id !== id)
      return [...without, { kind: 'folder', id, name: top, files: accepted }]
    })
  }

  const removeItem = (id: string) => setItems((prev) => prev.filter((i) => i.id !== id))

  // 拍平所有条目为待上传文件 + 展示 label + 稳定 key（进度跟踪用）
  const flatten = (): { file: File; label: string; key: string }[] => {
    const out: { file: File; label: string; key: string }[] = []
    for (const it of items) {
      if (it.kind === 'file') out.push({ file: it.file, label: it.file.name, key: it.id })
      else
        for (const f of it.files)
          out.push({ file: f, label: relPathOf(f), key: `${it.id}::${relPathOf(f)}` })
    }
    return out
  }

  const totalFiles = items.reduce(
    (n, it) => n + (it.kind === 'file' ? 1 : it.files.length),
    0,
  )

  const start = async () => {
    const flat = flatten()
    if (flat.length === 0 || !target) return
    setRunning(true)
    cancelRef.current = false
    const sm: Record<string, FileState> = {}
    flat.forEach((f) => (sm[f.key] = 'pending'))
    setStatus({ ...sm })
    let ok = 0
    let skip = 0
    let fail = 0
    let gold = 0
    let cancelled = false
    setStats({ done: 0, total: flat.length, ok: 0, skip: 0, fail: 0 })
    try {
      for (let i = 0; i < flat.length; i++) {
        if (cancelRef.current) {
          cancelled = true
          break
        }
        const { file, label, key } = flat[i]
        sm[key] = 'running'
        setStatus({ ...sm })
        setCurrent(label)
        setChunk(null)
        const ac = new AbortController()
        abortRef.current = ac
        try {
          const resp = await ingestKBFileStream(
            file,
            target,
            label,
            (p) => setChunk(p),
            ac.signal,
            isAdmin ? { goldenLlm, goldenMaxQ } : undefined,
          )
          if (resp.chunks > 0 && resp.status === 'ingested') {
            ok++
            gold += resp.golden_generated || 0
            sm[key] = 'ok'
          } else {
            skip++
            sm[key] = 'skip'
          }
        } catch (e) {
          // 取消导致的中断：不计失败，退出批次
          if (cancelRef.current || (e as Error).name === 'AbortError') {
            cancelled = true
            sm[key] = 'pending'
            setStatus({ ...sm })
            break
          }
          fail++
          sm[key] = 'fail'
          toast.error(`${label}：${(e as Error).message}`)
        }
        setStatus({ ...sm })
        setStats({ done: i + 1, total: flat.length, ok, skip, fail })
      }
      if (cancelled) {
        toast.info(`已取消入库（新增 ${ok}${skip ? ` · 未变 ${skip}` : ''}）`)
      } else {
        const goldPart = gold ? ` · 评估题 ${gold} 条待审` : ''
        const summary = `入库完成：新增 ${ok}${skip ? ` · 未变 ${skip}` : ''}${fail ? ` · 失败 ${fail}` : ''}${goldPart}`
        // 有新 golden 候选时：提示固定不消失、可手动关、带"去 Golden 管理"跳转；否则正常自动消失
        const opts =
          gold > 0
            ? {
                duration: Infinity,
                closeButton: true,
                ...(onGotoGolden
                  ? { action: { label: '去 Golden 管理', onClick: onGotoGolden } }
                  : {}),
              }
            : undefined
        if (fail > 0) toast.error(summary, opts)
        else toast.success(summary, opts)
        setItems([])
        setStatus({})
      }
    } finally {
      abortRef.current = null
      setRunning(false)
      setCurrent('')
      setChunk(null)
      onIngested()
    }
  }

  const cancel = () => {
    cancelRef.current = true
    abortRef.current?.abort()
  }

  // 当前文件的块级阶段文案
  const chunkText = (c: IngestProgress): string => {
    if (c.phase === 'parse') return '解析中…'
    if (c.phase === 'split') return `切分得 ${c.total} 块`
    if (c.phase === 'golden') return '生成评估题候选中…'
    return `嵌入 第 ${c.done}/${c.total} 块`
  }
  const chunkPct = chunk && chunk.total > 0 ? Math.round((chunk.done / chunk.total) * 100) : 0

  const pct = stats.total ? Math.round((stats.done / stats.total) * 100) : 0

  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-4">
      <h2 className="text-sm font-medium">入库</h2>

      <DropZone onFiles={addFiles} disabled={running} />

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          disabled={running}
          onClick={() => folderRef.current?.click()}
        >
          <FolderUp className="h-4 w-4" />
          选择文件夹
        </Button>
        <input
          ref={folderRef}
          type="file"
          multiple
          className="hidden"
          disabled={running}
          onChange={(e) => {
            addFolder(Array.from(e.target.files ?? []))
            e.target.value = ''
          }}
        />
        <span className="text-xs text-muted-foreground">
          文件夹会递归收集里面的 {ACCEPT_EXTENSIONS.join(' / ')}
        </span>
      </div>

      {/* 入库进度：总进度条 + 计数 + 当前文件 */}
      {running && (
        <div className="space-y-1.5 rounded-md border border-border bg-muted/30 px-3 py-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-foreground">
              入库中 {stats.done}/{stats.total}（{pct}%）
            </span>
            <span className="text-muted-foreground">
              新增 {stats.ok}
              {stats.skip > 0 && ` · 未变 ${stats.skip}`}
              {stats.fail > 0 && (
                <span className="text-destructive"> · 失败 {stats.fail}</span>
              )}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          {current && (
            <div className="truncate text-xs text-muted-foreground" title={current}>
              当前：{current}
            </div>
          )}
          {chunk && (
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span>{chunkText(chunk)}</span>
                <Loader2 className="h-3 w-3 animate-spin text-primary" />
              </div>
              {chunk.total > 0 && (
                <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full bg-primary/70 transition-all"
                    style={{ width: `${chunkPct}%` }}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {items.length > 0 && (
        <div className="rounded-md border border-border">
          <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
            <span className="text-sm font-medium">待入库（{totalFiles} 个文件）</span>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 gap-1 text-xs text-muted-foreground"
                disabled={running}
                onClick={() => setItems([])}
              >
                <Trash2 className="h-3.5 w-3.5" />
                清空
              </Button>
              {isAdmin && (
                <GoldenGenControls
                  goldenLlm={goldenLlm}
                  goldenMaxQ={goldenMaxQ}
                  onGoldenLlmChange={setGoldenLlm}
                  onGoldenMaxQChange={setGoldenMaxQ}
                  disabled={running}
                />
              )}
              <select
                value={target}
                onChange={(e) => {
                  userPickedRef.current = true
                  setTarget(e.target.value)
                }}
                disabled={running}
                className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                aria-label="目标库"
              >
                {collections.flatMap((c) => {
                  // 有云端版的库（m3）拆成「本地 / 云端(api)」两项，让入库路径可见且可选；
                  // 云端项 value=api-<alias>（如 api-m3），后端 resolve 到同一 kb_<alias>
                  const localValue = c.alias
                  const apiValue = `api-${c.alias}`
                  const local = (
                    <option key={c.alias} value={localValue}>
                      {c.alias}
                      {defaultAlias === localValue ? '（默认）' : ''} · {c.model}
                      {c.supports_api ? ' · 本地' : ''}
                    </option>
                  )
                  if (!c.supports_api) return [local]
                  return [
                    local,
                    <option key={apiValue} value={apiValue}>
                      {c.alias} · {c.model} · 云端(api)
                      {defaultAlias === apiValue ? '（默认）' : ''}
                    </option>,
                  ]
                })}
              </select>
              {running ? (
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-7 gap-1 text-xs"
                  onClick={cancel}
                >
                  <X className="h-3.5 w-3.5" />
                  取消
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="h-7 gap-1 text-xs"
                  disabled={!target}
                  onClick={start}
                >
                  <Play className="h-3.5 w-3.5" />
                  开始入库
                </Button>
              )}
            </div>
          </div>
          <ul className="max-h-56 overflow-y-auto">
            {items.map((it) => (
              <li
                key={it.id}
                className="flex items-center justify-between gap-2 border-b border-border/50 px-3 py-1.5 text-sm last:border-0"
              >
                {it.kind === 'file' ? (
                  <span className="truncate" title={it.file.name}>
                    {it.file.name}
                  </span>
                ) : (
                  <span className="flex min-w-0 items-center gap-1.5" title={it.name}>
                    <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-medium">{it.name}/</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      · {it.files.length} 个文件
                    </span>
                  </span>
                )}
                <span className="flex shrink-0 items-center gap-2">
                  {running ? (
                    <RowStatus item={it} status={status} relPathOf={relPathOf} />
                  ) : (
                    <>
                      <span className="text-xs text-muted-foreground">
                        {fmtSize(
                          it.kind === 'file'
                            ? it.file.size
                            : it.files.reduce((s, f) => s + f.size, 0),
                        )}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeItem(it.id)}
                        className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                        aria-label={`移除 ${it.kind === 'file' ? it.file.name : it.name}`}
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// 列表行右侧的入库状态：单文件显示一个状态徽标；文件夹显示 已完成/总数 聚合
function RowStatus({
  item,
  status,
  relPathOf,
}: {
  item: StageItem
  status: Record<string, FileState>
  relPathOf: (f: File) => string
}) {
  if (item.kind === 'file') {
    return <StateBadge state={status[item.id] ?? 'pending'} />
  }
  let done = 0
  let fail = 0
  for (const f of item.files) {
    const s = status[`${item.id}::${relPathOf(f)}`] ?? 'pending'
    if (s === 'ok' || s === 'skip') done++
    else if (s === 'fail') {
      done++
      fail++
    }
  }
  return (
    <span className="text-xs text-muted-foreground">
      {done}/{item.files.length}
      {fail > 0 && <span className="text-destructive"> · 失败 {fail}</span>}
    </span>
  )
}

function StateBadge({ state }: { state: FileState }) {
  if (state === 'running')
    return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
  if (state === 'ok')
    return (
      <span className="flex items-center gap-0.5 text-xs text-emerald-600 dark:text-emerald-500">
        <Check className="h-3.5 w-3.5" />
      </span>
    )
  if (state === 'skip')
    return <span className="text-xs text-muted-foreground">未变</span>
  if (state === 'fail')
    return (
      <span className="flex items-center gap-0.5 text-xs text-destructive">
        <X className="h-3.5 w-3.5" />
      </span>
    )
  return <span className="text-xs text-muted-foreground/60">待处理</span>
}
