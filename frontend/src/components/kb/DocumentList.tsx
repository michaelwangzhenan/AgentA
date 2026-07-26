import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, FileText, Loader2, Sparkles, Trash2, X } from 'lucide-react'

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const
const DEFAULT_PAGE_SIZE = 10

// 'YYYY-MM-DD' → epoch 秒（本地时区）；空串返回 undefined
export function dateToEpoch(s: string, endOfDay: boolean): number | undefined {
  if (!s) return undefined
  const d = new Date(`${s}T${endOfDay ? '23:59:59' : '00:00:00'}`)
  return Number.isNaN(d.getTime()) ? undefined : Math.floor(d.getTime() / 1000)
}

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { cn } from '@/lib/utils'
import type { KBDocument, KBDocumentsQuery } from '@/types/kb'

export type DocumentListQuery = {
  page: number
  pageSize: number
  sortKey: SortKey
  sortDir: SortDir
  nameQ: string
  lang: string
  ext: string
  tsFrom: string
  tsTo: string
}

export const DEFAULT_DOCUMENT_LIST_QUERY: DocumentListQuery = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  sortKey: 'ingested_at',
  sortDir: 'desc',
  nameQ: '',
  lang: '',
  ext: '',
  tsFrom: '',
  tsTo: '',
}

export type DocumentListProps = {
  documents: KBDocument[]
  total: number
  loading: boolean
  query: DocumentListQuery
  onQueryChange: (patch: Partial<DocumentListQuery>) => void
  onDelete?: (docId: string) => Promise<void> | void
  onDeleteMany?: (docIds: string[]) => Promise<void> | void
  onGenerateGolden?: (doc: KBDocument) => Promise<void> | void
  generatingDocId?: string | null
  onOpenGolden?: (docId: string, label: string) => void
  showGolden?: boolean
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

export function documentListQueryToApi(q: DocumentListQuery): KBDocumentsQuery {
  return {
    page: q.page,
    pageSize: q.pageSize,
    sortBy: q.sortKey,
    desc: q.sortDir === 'desc',
    filenameQ: q.nameQ.trim() || undefined,
    lang: q.lang || undefined,
    ext: q.ext || undefined,
    tsFrom: dateToEpoch(q.tsFrom, false),
    tsTo: dateToEpoch(q.tsTo, true),
  }
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
  total,
  loading,
  query,
  onQueryChange,
  onDelete,
  onDeleteMany,
  onGenerateGolden,
  generatingDocId,
  onOpenGolden,
  showGolden,
  goldenGenPreview,
}: DocumentListProps) {
  const [deleteTarget, setDeleteTarget] = useState<KBDocument | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [genTarget, setGenTarget] = useState<KBDocument | null>(null)
  const [genBusy, setGenBusy] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const { sortKey, sortDir, pageSize, page, nameQ, lang, ext, tsFrom, tsTo } = query
  const offset = (page - 1) * pageSize

  const patch = (p: Partial<DocumentListQuery>) => {
    const resetsPage = 'nameQ' in p || 'lang' in p || 'ext' in p || 'tsFrom' in p || 'tsTo' in p
      || 'sortKey' in p || 'sortDir' in p || 'pageSize' in p
    onQueryChange({ ...p, ...(resetsPage ? { page: 1 } : {}) })
  }

  useEffect(() => {
    setSelected(new Set())
  }, [documents, page])

  const pageIds = documents.map((d) => d.doc_id)
  const allSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id))
  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(pageIds))
  }
  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchBusy, setBatchBusy] = useState(false)

  const confirmBatchDelete = async () => {
    setBatchBusy(true)
    try {
      await onDeleteMany?.([...selected])
      setSelected(new Set())
      setBatchOpen(false)
    } finally {
      setBatchBusy(false)
    }
  }

  const hasFilter = Boolean(nameQ || lang || ext || tsFrom || tsTo)
  const clearFilters = () => {
    patch({
      nameQ: '',
      lang: '',
      ext: '',
      tsFrom: '',
      tsTo: '',
      page: 1,
    })
  }

  const handleSort = (col: SortColumn) => {
    if (col.key === sortKey) {
      patch({ sortDir: sortDir === 'asc' ? 'desc' : 'asc' })
    } else {
      patch({ sortKey: col.key, sortDir: col.defaultDir })
    }
  }

  const changePageSize = (n: number) => {
    patch({ pageSize: n, page: 1 })
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleteBusy(true)
    try {
      await onDelete?.(deleteTarget.doc_id)
      setDeleteTarget(null)
    } finally {
      setDeleteBusy(false)
    }
  }

  const confirmGenerate = async () => {
    if (!genTarget) return
    setGenBusy(true)
    try {
      await onGenerateGolden?.(genTarget)
      setGenTarget(null)
    } finally {
      setGenBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="p-8 text-center text-sm text-muted-foreground">
        加载中...
      </div>
    )
  }

  if (!loading && total === 0 && !hasFilter) {
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
          onChange={(e) => patch({ nameQ: e.target.value })}
          placeholder="文件名关键词"
          className={cn(inputCls, 'w-44')}
        />
        <input
          value={lang}
          onChange={(e) => patch({ lang: e.target.value })}
          placeholder="语言"
          className={cn(inputCls, 'w-24')}
        />
        <input
          value={ext}
          onChange={(e) => patch({ ext: e.target.value })}
          placeholder="扩展名 .md"
          className={cn(inputCls, 'w-28')}
        />
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          入库
          <input
            type="date"
            value={tsFrom}
            onChange={(e) => patch({ tsFrom: e.target.value })}
            className={inputCls}
            aria-label="入库时间起"
          />
          –
          <input
            type="date"
            value={tsTo}
            onChange={(e) => patch({ tsTo: e.target.value })}
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
            {onDelete ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-xs text-destructive hover:text-destructive"
              onClick={() => setBatchOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              批量删除
            </Button>
            ) : null}
          </div>
        </div>
      )}

      {documents.length === 0 && !loading ? (
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
                aria-label="全选本页"
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
          {documents.map((d) => (
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
              {onDelete ? (
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
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <Pager
        total={total}
        offset={offset}
        pageSize={pageSize}
        onOffset={(o) => onQueryChange({ page: Math.floor(o / pageSize) + 1 })}
        onPageSize={changePageSize}
      />
        </>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(o) => !o && !deleteBusy && setDeleteTarget(null)}
        title="删除文档？"
        description={`即将删除 "${deleteTarget?.filename}" 及其 ${deleteTarget?.chunks} 个 chunks（不可恢复）`}
        loading={deleteBusy}
        confirmLabel="删除"
        onConfirm={confirmDelete}
        contentProps={{
          onKeyDown: (e) => {
            if (deleteBusy) return
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.ctrlKey &&
              !e.metaKey &&
              !e.altKey
            ) {
              e.preventDefault()
              void confirmDelete()
            }
          },
        }}
      />

      <ConfirmDialog
        open={genTarget !== null}
        onOpenChange={(o) => !o && !genBusy && setGenTarget(null)}
        title="生成评估题候选？"
        description={
          <>
            将用 <b>{goldenGenPreview?.llmLabel ?? 'LLM'}</b> 为 &quot;
            {genTarget?.filename}&quot; 生成评估题候选（按文档字数，上限{' '}
            <b>{goldenGenPreview?.maxQ ?? 3}</b> 条）。会清掉该文档旧 pending（approved
            保留），消耗 token 并耗时若干秒。
          </>
        }
        loading={genBusy}
        destructive={false}
        confirmLabel="生成"
        onConfirm={confirmGenerate}
        contentProps={{
          onKeyDown: (e) => {
            if (genBusy) return
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.ctrlKey &&
              !e.metaKey &&
              !e.altKey
            ) {
              e.preventDefault()
              void confirmGenerate()
            }
          },
        }}
      />

      <ConfirmDialog
        open={batchOpen}
        onOpenChange={(o) => !o && !batchBusy && setBatchOpen(o)}
        title={`批量删除 ${selected.size} 个文档？`}
        description={
          <>
            即将删除选中的 <b>{selected.size}</b> 个文档及其所有 chunks（不可恢复）。
          </>
        }
        loading={batchBusy}
        confirmLabel="删除"
        onConfirm={confirmBatchDelete}
        contentProps={{
          onKeyDown: (e) => {
            if (batchBusy) return
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.ctrlKey &&
              !e.metaKey &&
              !e.altKey
            ) {
              e.preventDefault()
              void confirmBatchDelete()
            }
          },
        }}
      />
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
