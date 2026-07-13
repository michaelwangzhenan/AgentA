import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, FileText, Loader2, Sparkles, Trash2, X } from 'lucide-react'

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const
const DEFAULT_PAGE_SIZE = 10

// 'YYYY-MM-DD' → epoch 秒（本地时区）；空串返回 undefined
function dateToEpoch(s: string, endOfDay: boolean): number | undefined {
  if (!s) return undefined
  const d = new Date(`${s}T${endOfDay ? '23:59:59' : '00:00:00'}`)
  return Number.isNaN(d.getTime()) ? undefined : Math.floor(d.getTime() / 1000)
}

import { Button } from '@/components/ui/button'
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
import { cn } from '@/lib/utils'
import type { KBDocument } from '@/types/kb'

export type DocumentListProps = {
  documents: KBDocument[]
  loading: boolean
  onDelete: (docId: string) => Promise<void> | void
  onDeleteMany?: (docIds: string[]) => Promise<void> | void
  // 为某文档生成 golden 评估题候选；generatingDocId = 正在生成的文档（转圈）
  onGenerateGolden?: (doc: KBDocument) => Promise<void> | void
  generatingDocId?: string | null
  // 点候选数 → 跳质量看板 Golden 管理（按该文档筛选）
  onOpenGolden?: (docId: string, label: string) => void
  // 是否显示「评估题」列（golden 仅 admin 可见）
  showGolden?: boolean
  /** L2 顶栏统一设置，确认框只读展示 */
  goldenGenPreview?: { llmLabel: string; maxQ: number }
}

type SortKey =
  | 'filename'
  | 'lang'
  | 'chunks'
  | 'total_chars'
  | 'mtime'
  | 'ingested_at'
type SortDir = 'asc' | 'desc'

type SortColumn = {
  key: SortKey
  label: string
  align: 'left' | 'right'
  defaultDir: SortDir // 第一次切到该列时用的方向（数字列默认 desc 显示"大"在上）
}

const SORT_COLUMNS: readonly SortColumn[] = [
  { key: 'filename', label: '文件名', align: 'left', defaultDir: 'asc' },
  { key: 'lang', label: '语言', align: 'left', defaultDir: 'asc' },
  { key: 'chunks', label: 'chunks', align: 'right', defaultDir: 'desc' },
  { key: 'total_chars', label: '字符', align: 'right', defaultDir: 'desc' },
  { key: 'mtime', label: '修改时间', align: 'left', defaultDir: 'desc' },
  { key: 'ingested_at', label: '入库时间', align: 'left', defaultDir: 'desc' },
] as const

function getSortValue(d: KBDocument, key: SortKey): string | number {
  switch (key) {
    case 'filename':
      return (d.filename || d.source || '').toLowerCase()
    case 'lang':
      return (d.lang || '').toLowerCase()
    case 'chunks':
      return d.chunks
    case 'total_chars':
      return d.total_chars
    case 'mtime':
      return d.mtime
    case 'ingested_at':
      return d.ingested_at
  }
}

function compareDocs(
  a: KBDocument,
  b: KBDocument,
  key: SortKey,
  dir: SortDir,
): number {
  const va = getSortValue(a, key)
  const vb = getSortValue(b, key)
  let cmp: number
  if (typeof va === 'number' && typeof vb === 'number') {
    cmp = va - vb
  } else {
    cmp = String(va).localeCompare(String(vb), 'zh-Hans-CN', {
      sensitivity: 'base',
    })
  }
  return dir === 'asc' ? cmp : -cmp
}

function formatTime(mtime: number): string {
  if (!mtime) return '-'
  const d = new Date(mtime * 1000)
  if (Number.isNaN(d.getTime())) return '-'
  return d.toLocaleString()
}

function formatChars(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export function DocumentList({
  documents,
  loading,
  onDelete,
  onDeleteMany,
  onGenerateGolden,
  generatingDocId,
  onOpenGolden,
  showGolden,
  goldenGenPreview,
}: DocumentListProps) {
  const [deleteTarget, setDeleteTarget] = useState<KBDocument | null>(null)
  const [genTarget, setGenTarget] = useState<KBDocument | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [batchOpen, setBatchOpen] = useState(false)
  // 默认按"入库时间"倒序，跟后端 list_kb_documents 默认排序一致
  const [sortKey, setSortKey] = useState<SortKey>('ingested_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE)

  // 过滤条件：文件名关键词 / 语言 / 扩展名 / 入库时间范围
  const [nameQ, setNameQ] = useState('')
  const [lang, setLang] = useState('')
  const [ext, setExt] = useState('')
  const [tsFrom, setTsFrom] = useState('')
  const [tsTo, setTsTo] = useState('')

  // 下拉选项从当前文档实际出现的值动态生成
  const langOptions = useMemo(
    () => Array.from(new Set(documents.map((d) => d.lang).filter(Boolean))).sort(),
    [documents],
  )
  const extOptions = useMemo(
    () => Array.from(new Set(documents.map((d) => d.ext).filter(Boolean))).sort(),
    [documents],
  )

  const filteredDocs = useMemo(() => {
    const q = nameQ.trim().toLowerCase()
    const from = dateToEpoch(tsFrom, false)
    const to = dateToEpoch(tsTo, true)
    return documents.filter((d) => {
      if (
        q &&
        !(d.filename || '').toLowerCase().includes(q) &&
        !(d.source || '').toLowerCase().includes(q)
      )
        return false
      if (lang && (d.lang || '') !== lang) return false
      if (ext && (d.ext || '') !== ext) return false
      if (from !== undefined && d.ingested_at < from) return false
      if (to !== undefined && d.ingested_at > to) return false
      return true
    })
  }, [documents, nameQ, lang, ext, tsFrom, tsTo])

  // 过滤条件变化时回到第 1 页 + 清空选择（避免选中项已被过滤掉）
  useEffect(() => {
    setOffset(0)
    setSelected(new Set())
  }, [nameQ, lang, ext, tsFrom, tsTo])

  // 文档列表刷新（删除/入库后）时清空选择，避免残留已不存在的 doc_id
  useEffect(() => {
    setSelected(new Set())
  }, [documents])

  // 选择作用于"过滤后"的全部文档（跨分页），便于"过滤→全选→批量删"
  const filteredIds = filteredDocs.map((d) => d.doc_id)
  const allSelected = filteredIds.length > 0 && filteredIds.every((id) => selected.has(id))
  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(filteredIds))
  }
  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const confirmBatchDelete = async () => {
    await onDeleteMany?.([...selected])
    setSelected(new Set())
    setBatchOpen(false)
  }

  const hasFilter = Boolean(nameQ || lang || ext || tsFrom || tsTo)
  const clearFilters = () => {
    setNameQ('')
    setLang('')
    setExt('')
    setTsFrom('')
    setTsTo('')
  }

  const sortedDocs = useMemo(
    // Array.prototype.sort 是稳定排序 (ES2019+)，同键值时保持原顺序
    () => [...filteredDocs].sort((a, b) => compareDocs(a, b, sortKey, sortDir)),
    [filteredDocs, sortKey, sortDir],
  )

  // 列表可能因删除 / 刷新变短：用 clamp 后的 offset 取当前页，避免停在空页
  const total = sortedDocs.length
  const maxOffset = Math.max(0, Math.floor((total - 1) / pageSize) * pageSize)
  const safeOffset = Math.min(offset, maxOffset)
  const pagedDocs = sortedDocs.slice(safeOffset, safeOffset + pageSize)

  const handleSort = (col: SortColumn) => {
    setOffset(0)
    if (col.key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(col.key)
      setSortDir(col.defaultDir)
    }
  }

  const changePageSize = (n: number) => {
    setPageSize(n)
    setOffset(0)
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    await onDelete(deleteTarget.doc_id)
    setDeleteTarget(null)
  }

  const confirmGenerate = async () => {
    if (!genTarget) return
    const target = genTarget
    setGenTarget(null)
    await onGenerateGolden?.(target)
  }

  if (loading) {
    return (
      <div className="p-8 text-center text-sm text-muted-foreground">
        加载中...
      </div>
    )
  }

  if (documents.length === 0) {
    return (
      <div className="p-8 text-center text-sm text-muted-foreground">
        暂无文档；拖一个上来试试
      </div>
    )
  }

  const inputCls =
    'rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground'

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <input
          value={nameQ}
          onChange={(e) => setNameQ(e.target.value)}
          placeholder="文件名关键词"
          className={cn(inputCls, 'w-44')}
        />
        <select
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          className={inputCls}
          aria-label="按语言过滤"
        >
          <option value="">全部语言</option>
          {langOptions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={ext}
          onChange={(e) => setExt(e.target.value)}
          className={inputCls}
          aria-label="按扩展名过滤"
        >
          <option value="">全部类型</option>
          {extOptions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          入库
          <input
            type="date"
            value={tsFrom}
            onChange={(e) => setTsFrom(e.target.value)}
            className={inputCls}
            aria-label="入库时间起"
          />
          –
          <input
            type="date"
            value={tsTo}
            onChange={(e) => setTsTo(e.target.value)}
            className={inputCls}
            aria-label="入库时间止"
          />
        </label>
        {hasFilter && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs text-muted-foreground"
            onClick={clearFilters}
          >
            <X className="h-3.5 w-3.5" />
            清除
          </Button>
        )}
      </div>

      {selected.size > 0 && (
        <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/40 px-3 py-2">
          <span className="text-sm">已选 {selected.size} 项</span>
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => setSelected(new Set())}
            >
              取消选择
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-xs text-destructive hover:text-destructive"
              onClick={() => setBatchOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              批量删除
            </Button>
          </div>
        </div>
      )}

      {sortedDocs.length === 0 ? (
        <div className="p-8 text-center text-sm text-muted-foreground">
          无匹配文档
        </div>
      ) : (
        <>
      <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="w-8 px-3 py-2">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
                aria-label="全选（过滤后全部）"
                className="h-4 w-4 cursor-pointer accent-primary align-middle"
              />
            </th>
            {SORT_COLUMNS.map((col) => {
              const active = sortKey === col.key
              const Icon = sortDir === 'asc' ? ChevronUp : ChevronDown
              return (
                <th
                  key={col.key}
                  className={cn(
                    'px-3 py-2 font-medium',
                    col.align === 'right' && 'text-right',
                  )}
                  aria-sort={
                    active
                      ? sortDir === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : 'none'
                  }
                >
                  <button
                    type="button"
                    onClick={() => handleSort(col)}
                    className={cn(
                      'inline-flex items-center gap-1 whitespace-nowrap transition-colors hover:text-foreground',
                      col.align === 'right' && 'justify-end',
                      active && 'text-foreground',
                    )}
                    aria-label={`按 ${col.label} 排序，当前 ${active ? (sortDir === 'asc' ? '升序' : '降序') : '未排序'}`}
                  >
                    {col.label}
                    <Icon
                      className={cn(
                        'h-3 w-3 transition-opacity',
                        active ? 'opacity-100' : 'opacity-30',
                      )}
                    />
                  </button>
                </th>
              )
            })}
            {showGolden && (
              <th className="w-28 px-3 py-2 text-right font-medium whitespace-nowrap">评估题</th>
            )}
            <th className="w-10 px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {pagedDocs.map((d) => (
            <tr
              key={d.doc_id}
              className={cn(
                'border-b border-border/50 hover:bg-muted/30',
                selected.has(d.doc_id) && 'bg-muted/40',
              )}
            >
              <td className="px-3 py-2">
                <input
                  type="checkbox"
                  checked={selected.has(d.doc_id)}
                  onChange={() => toggleOne(d.doc_id)}
                  aria-label={`选择 ${d.filename || d.source}`}
                  className="h-4 w-4 cursor-pointer accent-primary align-middle"
                />
              </td>
              <td className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="truncate" title={d.source}>
                    {d.filename || d.source}
                  </span>
                </div>
              </td>
              <td className="px-3 py-2 text-muted-foreground">{d.lang || '-'}</td>
              <td className="px-3 py-2 text-right tabular-nums">{d.chunks}</td>
              <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                {formatChars(d.total_chars)}
              </td>
              <td className="px-3 py-2 text-xs text-muted-foreground">
                {formatTime(d.mtime)}
              </td>
              <td className="px-3 py-2 text-xs text-muted-foreground">
                {formatTime(d.ingested_at)}
              </td>
              {showGolden && (
              <td className="px-3 py-2 text-right whitespace-nowrap">
                <div className="flex items-center justify-end gap-1.5">
                  {(d.golden_total ?? 0) > 0 ? (
                    <button
                      type="button"
                      className="rounded px-1.5 py-0.5 text-xs text-primary hover:bg-primary/10"
                      title="查看该文档的评估题候选"
                      onClick={() => onOpenGolden?.(d.doc_id, d.filename || d.source)}
                    >
                      {d.golden_total}
                      {(d.golden_pending ?? 0) > 0 && (
                        <span className="ml-0.5 text-amber-600 dark:text-amber-400">·{d.golden_pending} 待审</span>
                      )}
                    </button>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                  {onGenerateGolden && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-primary"
                      disabled={generatingDocId === d.doc_id}
                      onClick={() => setGenTarget(d)}
                      aria-label={`为 ${d.filename} 生成评估题`}
                      title="生成评估题候选（LLM）"
                    >
                      {generatingDocId === d.doc_id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Sparkles className="h-4 w-4" />
                      )}
                    </Button>
                  )}
                </div>
              </td>
              )}
              <td className="px-3 py-2 text-right">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground hover:text-destructive"
                  onClick={() => setDeleteTarget(d)}
                  aria-label={`删除 ${d.filename}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <Pager
        total={total}
        offset={safeOffset}
        pageSize={pageSize}
        onOffset={setOffset}
        onPageSize={changePageSize}
      />
        </>
      )}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(o: boolean) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent
          onKeyDown={(e) => {
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.ctrlKey &&
              !e.metaKey &&
              !e.altKey
            ) {
              e.preventDefault()
              confirmDelete()
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>删除文档？</AlertDialogTitle>
            <AlertDialogDescription>
              即将删除 "{deleteTarget?.filename}" 及其 {deleteTarget?.chunks} 个 chunks（不可恢复）
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmDelete}
              autoFocus
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={genTarget !== null}
        onOpenChange={(o: boolean) => !o && setGenTarget(null)}
      >
        <AlertDialogContent
          onKeyDown={(e) => {
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.ctrlKey &&
              !e.metaKey &&
              !e.altKey
            ) {
              e.preventDefault()
              confirmGenerate()
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>生成评估题候选？</AlertDialogTitle>
            <AlertDialogDescription>
              将用 <b>{goldenGenPreview?.llmLabel ?? 'LLM'}</b> 为 &quot;
              {genTarget?.filename}&quot; 生成 <b>{goldenGenPreview?.maxQ ?? 3}</b>{' '}
              条评估题候选。会清掉该文档旧 pending（approved 保留），消耗 token 并耗时若干秒。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={confirmGenerate} autoFocus>
              生成
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={batchOpen} onOpenChange={(o: boolean) => !o && setBatchOpen(false)}>
        <AlertDialogContent
          onKeyDown={(e) => {
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.ctrlKey &&
              !e.metaKey &&
              !e.altKey
            ) {
              e.preventDefault()
              confirmBatchDelete()
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>批量删除 {selected.size} 个文档？</AlertDialogTitle>
            <AlertDialogDescription>
              即将删除选中的 <b>{selected.size}</b> 个文档及其所有 chunks（不可恢复）。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={confirmBatchDelete}
              autoFocus
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// 分页导航栏：上一页 / 范围 / 下一页 / 页码 / 跳至 / 每页（样式对齐「数据库」L2）
function Pager({
  total,
  offset,
  pageSize,
  onOffset,
  onPageSize,
}: {
  total: number
  offset: number
  pageSize: number
  onOffset: (offset: number) => void
  onPageSize: (n: number) => void
}) {
  const [jump, setJump] = useState('')
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const page = Math.floor(offset / pageSize) + 1
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + pageSize, total)

  const go = () => {
    const n = parseInt(jump, 10)
    if (!Number.isNaN(n)) {
      const clamped = Math.min(Math.max(1, n), totalPages)
      onOffset((clamped - 1) * pageSize)
    }
    setJump('')
  }

  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-border px-3 py-2 text-sm text-muted-foreground">
      <button
        type="button"
        onClick={() => onOffset(Math.max(0, offset - pageSize))}
        disabled={offset <= 0}
        className="rounded-md border border-border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
      >
        上一页
      </button>
      <span>
        {from}–{to} / {total}
      </span>
      <button
        type="button"
        onClick={() => onOffset(offset + pageSize)}
        disabled={to >= total}
        className="rounded-md border border-border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
      >
        下一页
      </button>
      <span>
        第 {page}/{totalPages} 页
      </span>
      <span className="flex items-center gap-1">
        跳至
        <input
          value={jump}
          inputMode="numeric"
          onChange={(e) => setJump(e.target.value.replace(/[^0-9]/g, ''))}
          onKeyDown={(e) => {
            if (e.key === 'Enter') go()
          }}
          placeholder={String(page)}
          className="w-14 rounded-md border border-border bg-background px-1.5 py-1 text-center text-foreground"
        />
        <button
          type="button"
          onClick={go}
          className="rounded-md border border-border px-2 py-1 hover:bg-muted/50"
        >
          跳转
        </button>
      </span>
      <label className="ml-auto flex items-center gap-1.5">
        每页
        <select
          value={pageSize}
          onChange={(e) => onPageSize(Number(e.target.value))}
          className="rounded-md border border-border bg-background px-1.5 py-1 text-foreground"
        >
          {PAGE_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        条
      </label>
    </div>
  )
}
