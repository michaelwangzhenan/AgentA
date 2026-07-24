import { useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Boxes, ChevronRight, Database, Loader2, Search, Wrench } from 'lucide-react'
import { toast } from 'sonner'

import { cn } from '@/lib/utils'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  cleanupOrphanSegments,
  getBm25Doc,
  getBm25Docs,
  getBm25Indexes,
  getChromaCollections,
  getChromaItem,
  getChromaItems,
  getOrphanSegmentsPreview,
  getPrunePreview,
  getPurgeUserPreview,
  getRepairPreview,
  getSqliteDatabases,
  getSqliteTableRows,
  listUsers,
  runPrune,
  runPurgeUser,
  runRepair,
  runVacuum,
} from '@/api/client'
import type {
  Bm25DocDetail,
  Bm25DocsPage,
  Bm25Indexes,
  ChromaCollections,
  ChromaItemDetail,
  ChromaItemsPage,
  Metadata,
  OrphanSegmentsPreview,
  PruneResult,
  PurgePreview,
  PurgeSelection,
  RepairPreview,
  SqliteDatabases,
  SqliteTableRows,
  VacuumResult,
} from '@/types/dbAdmin'
import type { UserInfo } from '@/types/auth'

import type { DatabaseTab } from '@/routes/paths'
import { databasePath, epochToDateInput } from '@/routes/paths'
import { useUrlState } from '@/routes/useUrlState'

// 后端 limit 上限 200，候选项不超过它
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200] as const
const DEFAULT_PAGE_SIZE = 10
// 与后端 db_inspect.CHROMA_SCAN_CAP 对齐，仅用于 truncated 提示文案
const CHROMA_SCAN_CAP_HINT = 20000

export function DatabaseView({
  tab,
  seg1,
  seg2,
  onTabChange,
  onPathChange,
}: {
  tab: DatabaseTab
  seg1?: string
  seg2?: string
  onTabChange: (tab: DatabaseTab) => void
  onPathChange: (path: string) => void
}) {
  const tabs: { value: DatabaseTab; label: string; icon: LucideIcon }[] = [
    { value: 'chroma', label: 'Chroma', icon: Boxes },
    { value: 'bm25', label: 'BM25', icon: Search },
    { value: 'sqlite', label: 'SQLite', icon: Database },
    { value: 'maintenance', label: '维护', icon: Wrench },
  ]

  return (
    <ResourcePage
      title="数据库"
      subtitle="Chroma / BM25 / SQLite 的结构与内容"
    >
      <div className="flex min-h-0 flex-1 gap-4">
        <nav className="sticky top-0 w-32 shrink-0 self-start">
          <ul className="space-y-0.5">
            {tabs.map((t) => (
              <li key={t.value}>
                <button
                  type="button"
                  onClick={() => onTabChange(t.value)}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
                    tab === t.value
                      ? 'bg-muted font-medium text-foreground'
                      : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                  )}
                >
                  <t.icon className="h-4 w-4 shrink-0" />
                  {t.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="min-w-0 flex-1">
          {tab === 'chroma' && (
            <ChromaPanel seg1={seg1} seg2={seg2} onPathChange={onPathChange} />
          )}
          {tab === 'bm25' && (
            <Bm25Panel seg1={seg1} seg2={seg2} onPathChange={onPathChange} />
          )}
          {tab === 'sqlite' && (
            <SqlitePanel seg1={seg1} seg2={seg2} onPathChange={onPathChange} />
          )}
          {tab === 'maintenance' && <MaintenancePanel />}
        </div>
      </div>
    </ResourcePage>
  )
}

// ── 共用小组件 ─────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
    </div>
  )
}

function ErrorNote({ msg }: { msg: string }) {
  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {msg}
    </div>
  )
}

function Breadcrumb({ parts }: { parts: { label: string; onClick?: () => void }[] }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-1 text-sm">
      {parts.map((p, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
          {p.onClick ? (
            <button
              type="button"
              onClick={p.onClick}
              className="rounded px-1 text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            >
              {p.label}
            </button>
          ) : (
            <span className="px-1 font-medium text-foreground">{p.label}</span>
          )}
        </span>
      ))}
    </div>
  )
}

function Pager({
  total,
  offset,
  pageSize,
  onOffset,
  onPageSize,
  className,
}: {
  total: number
  offset: number
  pageSize: number
  onOffset: (offset: number) => void
  onPageSize: (n: number) => void
  className?: string
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
    <div className={cn('flex flex-wrap items-center gap-3 text-sm text-muted-foreground', className)}>
      <button
        type="button"
        onClick={() => onOffset(Math.max(0, offset - pageSize))}
        disabled={offset <= 0}
        className="rounded-md border border-border px-2 py-1 disabled:opacity-40 hover:bg-muted/50"
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
        className="rounded-md border border-border px-2 py-1 disabled:opacity-40 hover:bg-muted/50"
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

function MetadataBlock({ metadata }: { metadata: Metadata }) {
  if (!metadata || Object.keys(metadata).length === 0) {
    return <p className="text-sm text-muted-foreground">（无 metadata）</p>
  }
  return (
    <pre className="overflow-x-auto rounded-md bg-muted/50 p-3 text-xs leading-relaxed">
      {JSON.stringify(metadata, null, 2)}
    </pre>
  )
}

function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-2 py-0.5 text-xs text-muted-foreground">
      {label}: <span className="font-medium text-foreground">{value}</span>
    </span>
  )
}

function Pill({ children }: { children: React.ReactNode }) {
  return (
    <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
      {children}
    </span>
  )
}

// .env 配置的默认入库库标记，样式对齐知识库 L1
function DefaultBadge() {
  return (
    <span className="shrink-0 rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-medium text-primary">
      默认入库
    </span>
  )
}

// chunk 的正文是带换行的大段文本：压成单行、合并空白，作为可扫读的摘要。
function normalizePreview(s: string): string {
  return (s || '').replace(/\s+/g, ' ').trim()
}

function metaStr(m: Metadata, key: string): string | undefined {
  if (!m) return undefined
  const v = (m as Record<string, unknown>)[key]
  return v === undefined || v === null ? undefined : String(v)
}

// epoch 秒 → 可读时间；缺失/非法显示 '-'
function fmtTime(epoch?: number | string): string {
  if (epoch == null || epoch === '') return '-'
  const n = typeof epoch === 'string' ? Number(epoch) : epoch
  if (!Number.isFinite(n) || n <= 0) return '-'
  const d = new Date(n * 1000)
  if (Number.isNaN(d.getTime())) return '-'
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// Chroma / BM25 第二层共用：结构信息（来源/块/语言/行/章节）当主角，正文摘要当配角。
function ChunkRow({
  preview,
  metadata,
  extra,
  showTime,
  onClick,
}: {
  preview: string
  metadata: Metadata
  extra?: React.ReactNode
  showTime?: boolean
  onClick: () => void
}) {
  const title =
    metaStr(metadata, 'title') ||
    metaStr(metadata, 'filename') ||
    metaStr(metadata, 'source') ||
    '（无来源）'
  const ci = metaStr(metadata, 'chunk_index')
  const ct = metaStr(metadata, 'chunk_total')
  const chunk =
    ci != null && ct != null ? `块 ${Number(ci) + 1}/${ct}` : ci != null ? `块 ${Number(ci) + 1}` : undefined
  const lang = metaStr(metadata, 'lang')
  const ls = metaStr(metadata, 'line_start')
  const le = metaStr(metadata, 'line_end')
  const lines = ls != null && le != null ? `行 ${ls}–${le}` : undefined
  const heading = metaStr(metadata, 'heading_path')
  const snippet = normalizePreview(preview)
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      className="group cursor-pointer rounded-md border border-border px-3 py-2 hover:bg-muted/50"
    >
      <div className="flex items-center gap-2">
        <span className="truncate text-sm font-medium" title={title}>
          {title}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {extra}
          {showTime && <Pill>入库时间 {fmtTime(metaStr(metadata, 'ingested_at'))}</Pill>}
          {chunk && <Pill>{chunk}</Pill>}
          {lang && <Pill>{lang}</Pill>}
          {lines && <Pill>{lines}</Pill>}
          <ChevronRight className="h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
        </span>
      </div>
      {heading && (
        <div className="mt-0.5 truncate text-xs text-muted-foreground" title={heading}>
          {heading}
        </div>
      )}
      <div className="mt-0.5 line-clamp-1 text-sm text-muted-foreground">
        {snippet || '（空正文）'}
      </div>
    </div>
  )
}

// depKey 变化即重新拉取；fn 故意不进依赖（每次 render 都是新闭包，靠 depKey 控制刷新）。
function useAsync<T>(fn: () => Promise<T>, depKey: string) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fn()
      .then((d) => !cancelled && setData(d))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey])
  return { data, loading, error }
}

// ── Chroma 面板 ────────────────────────────────────────────────────────────

type ChromaFilters = { filenameQ: string; bodyQ: string; tsFrom?: number; tsTo?: number }
type ChromaSort = { by?: 'filename' | 'ingested_at'; desc: boolean }
const EMPTY_FILTERS: ChromaFilters = { filenameQ: '', bodyQ: '' }

function useChromaListUrlState() {
  const url = useUrlState()
  const page = Math.max(1, url.getInt('page', 1))
  const rawSize = url.getInt('size', DEFAULT_PAGE_SIZE)
  const pageSize = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawSize)
    ? rawSize
    : DEFAULT_PAGE_SIZE
  const offset = (page - 1) * pageSize

  const fromStr = url.get('from')
  const toStr = url.get('to')
  const filters: ChromaFilters = {
    filenameQ: url.get('filename'),
    bodyQ: url.get('body'),
    tsFrom: dateToEpoch(fromStr, false),
    tsTo: dateToEpoch(toStr, true),
  }

  const sortBy = url.get('sort')
  const sort: ChromaSort = {
    by: sortBy === 'filename' || sortBy === 'ingested_at' ? sortBy : undefined,
    desc: url.get('dir') !== 'asc',
  }

  const setOffset = (n: number) => {
    const p = Math.floor(n / pageSize) + 1
    url.patch({ page: p <= 1 ? null : p })
  }
  const setPageSize = (n: number) => {
    url.patch({ size: n === DEFAULT_PAGE_SIZE ? null : n, page: null })
  }
  const setFilters = (f: ChromaFilters) => {
    url.patch({
      filename: f.filenameQ || null,
      body: f.bodyQ || null,
      from: f.tsFrom ? epochToDateInput(f.tsFrom) : null,
      to: f.tsTo ? epochToDateInput(f.tsTo) : null,
      page: null,
    })
  }
  const setSort = (s: ChromaSort) => {
    url.patch({
      sort: s.by || null,
      dir: s.by && !s.desc ? 'asc' : null,
      page: null,
    })
  }

  return { offset, pageSize, filters, sort, setOffset, setPageSize, setFilters, setSort }
}

function ChromaPanel({
  seg1,
  seg2,
  onPathChange,
}: {
  seg1?: string
  seg2?: string
  onPathChange: (path: string) => void
}) {
  const { offset, pageSize, filters, sort, setOffset, setPageSize, setFilters, setSort } =
    useChromaListUrlState()

  if (seg1 && seg2) {
    return (
      <ChromaDetail
        name={seg1}
        itemId={seg2}
        onBack={() => onPathChange(databasePath('chroma', seg1))}
        onRoot={() => onPathChange(databasePath('chroma'))}
      />
    )
  }
  if (seg1) {
    return (
      <ChromaItems
        name={seg1}
        offset={offset}
        setOffset={setOffset}
        pageSize={pageSize}
        onPageSize={setPageSize}
        filters={filters}
        onFilters={setFilters}
        sort={sort}
        onSort={setSort}
        onOpen={(id) => onPathChange(databasePath('chroma', seg1, id))}
        onRoot={() => onPathChange(databasePath('chroma'))}
      />
    )
  }
  return (
    <ChromaList onOpen={(name) => onPathChange(databasePath('chroma', name))} />
  )
}

// 'YYYY-MM-DD' → epoch 秒（本地时区）；空串返回 undefined
function dateToEpoch(s: string, endOfDay: boolean): number | undefined {
  if (!s) return undefined
  const d = new Date(`${s}T${endOfDay ? '23:59:59' : '00:00:00'}`)
  return Number.isNaN(d.getTime()) ? undefined : Math.floor(d.getTime() / 1000)
}

function ChromaFilterBar({
  filters,
  sort,
  onFilters,
  onSort,
}: {
  filters: ChromaFilters
  sort: ChromaSort
  onFilters: (f: ChromaFilters) => void
  onSort: (s: ChromaSort) => void
}) {
  const [filename, setFilename] = useState(filters.filenameQ)
  const [body, setBody] = useState(filters.bodyQ)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const apply = () => {
    onFilters({
      filenameQ: filename.trim(),
      bodyQ: body.trim(),
      tsFrom: dateToEpoch(from, false),
      tsTo: dateToEpoch(to, true),
    })
  }
  const clear = () => {
    setFilename('')
    setBody('')
    setFrom('')
    setTo('')
    onFilters({ ...EMPTY_FILTERS })
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') apply()
  }
  const inputCls =
    'rounded-md border border-border bg-background px-2 py-1 text-foreground'

  return (
    <div className="mb-3 flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1 text-muted-foreground">
        文件名
        <input
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
          onKeyDown={onKey}
          placeholder="包含…"
          className={cn(inputCls, 'w-36')}
        />
      </label>
      <label className="flex items-center gap-1 text-muted-foreground">
        正文
        <input
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={onKey}
          placeholder="包含…"
          className={cn(inputCls, 'w-40')}
        />
      </label>
      <label className="flex items-center gap-1 text-muted-foreground">
        入库时间
        <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} />
        –
        <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={inputCls} />
      </label>
      <button
        type="button"
        onClick={apply}
        className="rounded-md border border-border px-2 py-1 hover:bg-muted/50"
      >
        查询
      </button>
      <button
        type="button"
        onClick={clear}
        className="rounded-md border border-border px-2 py-1 text-muted-foreground hover:bg-muted/50"
      >
        清除
      </button>
      </div>
      <label className="flex items-center gap-1 text-muted-foreground">
        排序
        <select
          value={sort.by ?? ''}
          onChange={(e) =>
            onSort({ ...sort, by: (e.target.value || undefined) as ChromaSort['by'] })
          }
          className={inputCls}
        >
          <option value="">默认</option>
          <option value="ingested_at">入库时间</option>
          <option value="filename">文件名</option>
        </select>
        <button
          type="button"
          onClick={() => onSort({ ...sort, desc: !sort.desc })}
          disabled={!sort.by}
          className="rounded-md border border-border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
          title={sort.desc ? '降序' : '升序'}
        >
          {sort.desc ? '↓' : '↑'}
        </button>
      </label>
    </div>
  )
}

function ChromaList({ onOpen }: { onOpen: (name: string) => void }) {
  const { data, loading, error } = useAsync<ChromaCollections>(getChromaCollections, 'chroma-cols')
  if (loading) return <Spinner />
  if (error) return <ErrorNote msg={error} />
  if (!data) return null
  return (
    <div>
      <Breadcrumb parts={[{ label: 'Chroma' }]} />
      <p className="mb-2 text-xs text-muted-foreground">数据库目录：{data.root}</p>
      {data.error && <ErrorNote msg={data.error} />}
      <ul className="space-y-1">
        {[...data.collections]
          .sort((a, b) => Number(b.is_default) - Number(a.is_default))
          .map((c) => (
            <li key={c.name}>
              <button
                type="button"
                onClick={() => onOpen(c.name)}
                className={cn(
                  'flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors',
                  c.is_default
                    ? 'border-primary/50 bg-primary/5 hover:bg-primary/10'
                    : 'border-border hover:bg-muted/50',
                )}
              >
                <span className="flex items-center gap-2">
                  <span className="font-medium">{c.name}</span>
                  {c.is_default && <DefaultBadge />}
                </span>
                <span className="flex items-center gap-1.5">
                  <StatCard label="条数" value={c.count ?? '—'} />
                  <StatCard label="维度" value={c.dim ?? '—'} />
                  {c.space && <StatCard label="空间" value={c.space} />}
                  {c.error && <span className="text-xs text-destructive">异常</span>}
                </span>
              </button>
            </li>
          ))}
      </ul>
    </div>
  )
}

function ChromaItems({
  name,
  offset,
  setOffset,
  pageSize,
  onPageSize,
  filters,
  onFilters,
  sort,
  onSort,
  onOpen,
  onRoot,
}: {
  name: string
  offset: number
  setOffset: (n: number) => void
  pageSize: number
  onPageSize: (n: number) => void
  filters: ChromaFilters
  onFilters: (f: ChromaFilters) => void
  sort: ChromaSort
  onSort: (s: ChromaSort) => void
  onOpen: (id: string) => void
  onRoot: () => void
}) {
  const fKey = `${filters.filenameQ}|${filters.bodyQ}|${filters.tsFrom ?? ''}|${filters.tsTo ?? ''}`
  const { data, loading, error } = useAsync<ChromaItemsPage>(
    () =>
      getChromaItems(name, {
        limit: pageSize,
        offset,
        filenameQ: filters.filenameQ || undefined,
        bodyQ: filters.bodyQ || undefined,
        tsFrom: filters.tsFrom,
        tsTo: filters.tsTo,
        sortBy: sort.by,
        desc: sort.desc,
      }),
    `${name}:${pageSize}:${offset}:${fKey}:${sort.by ?? ''}:${sort.desc}`,
  )
  return (
    <div>
      <Breadcrumb parts={[{ label: 'Chroma', onClick: onRoot }, { label: name }]} />
      <ChromaFilterBar filters={filters} sort={sort} onFilters={onFilters} onSort={onSort} />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
      {data?.error && <ErrorNote msg={data.error} />}
      {data?.truncated && (
        <p className="mb-2 text-xs text-amber-600 dark:text-amber-500">
          数据量大，过滤 / 排序仅基于前 {CHROMA_SCAN_CAP_HINT} 条结果，可能不完整。
        </p>
      )}
      {data && !loading && (
        <>
          <Pager
            total={data.total}
            offset={offset}
            pageSize={pageSize}
            onOffset={setOffset}
            onPageSize={onPageSize}
            className="mb-3"
          />
          <ul className="space-y-1">
            {data.items.map((it) => (
              <li key={it.id}>
                <ChunkRow
                  preview={it.preview}
                  metadata={it.metadata}
                  showTime
                  onClick={() => onOpen(it.id)}
                />
              </li>
            ))}
          </ul>
          <Pager
            total={data.total}
            offset={offset}
            pageSize={pageSize}
            onOffset={setOffset}
            onPageSize={onPageSize}
            className="mt-3"
          />
        </>
      )}
    </div>
  )
}

function ChromaDetail({
  name,
  itemId,
  onBack,
  onRoot,
}: {
  name: string
  itemId: string
  onBack: () => void
  onRoot: () => void
}) {
  const { data, loading, error } = useAsync<ChromaItemDetail>(
    () => getChromaItem(name, itemId),
    `${name}:${itemId}`,
  )
  return (
    <div>
      <Breadcrumb
        parts={[
          { label: 'Chroma', onClick: onRoot },
          { label: name, onClick: onBack },
          { label: itemId },
        ]}
      />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
      {data && !loading && (
        <div className="space-y-4">
          <section>
            <h3 className="mb-1 text-sm font-medium">metadata</h3>
            <MetadataBlock metadata={data.metadata} />
          </section>
          <section>
            <h3 className="mb-1 text-sm font-medium">正文</h3>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-sm leading-relaxed">
              {data.document || '（空正文）'}
            </pre>
          </section>
        </div>
      )}
    </div>
  )
}

// ── BM25 面板 ──────────────────────────────────────────────────────────────

function Bm25Panel({
  seg1,
  seg2,
  onPathChange,
}: {
  seg1?: string
  seg2?: string
  onPathChange: (path: string) => void
}) {
  const { offset, pageSize, filters, sort, setOffset, setPageSize, setFilters, setSort } =
    useChromaListUrlState()

  if (seg1 && seg2) {
    return (
      <Bm25DocView
        coll={seg1}
        docId={seg2}
        onBack={() => onPathChange(databasePath('bm25', seg1))}
        onRoot={() => onPathChange(databasePath('bm25'))}
      />
    )
  }
  if (seg1) {
    return (
      <Bm25Docs
        coll={seg1}
        offset={offset}
        setOffset={setOffset}
        pageSize={pageSize}
        onPageSize={setPageSize}
        filters={filters}
        onFilters={setFilters}
        sort={sort}
        onSort={setSort}
        onOpen={(id) => onPathChange(databasePath('bm25', seg1, id))}
        onRoot={() => onPathChange(databasePath('bm25'))}
      />
    )
  }
  return <Bm25List onOpen={(coll) => onPathChange(databasePath('bm25', coll))} />
}

function Bm25List({ onOpen }: { onOpen: (coll: string) => void }) {
  const { data, loading, error } = useAsync<Bm25Indexes>(getBm25Indexes, 'bm25-idx')
  if (loading) return <Spinner />
  if (error) return <ErrorNote msg={error} />
  if (!data) return null
  return (
    <div>
      <Breadcrumb parts={[{ label: 'BM25' }]} />
      <p className="mb-2 text-xs text-muted-foreground">索引目录：{data.dir}</p>
      <ul className="space-y-1">
        {[...data.indexes]
          .sort((a, b) => Number(b.is_default) - Number(a.is_default))
          .map((ix) => (
          <li key={ix.file}>
            <button
              type="button"
              onClick={() => !ix.error && onOpen(ix.collection)}
              disabled={!!ix.error}
              className={cn(
                'flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors disabled:opacity-60',
                ix.is_default
                  ? 'border-primary/50 bg-primary/5 hover:bg-primary/10'
                  : 'border-border hover:bg-muted/50',
              )}
            >
              <span className="flex items-center gap-2">
                <span className="font-medium">{ix.file}</span>
                {ix.is_default && <DefaultBadge />}
              </span>
              <span className="flex items-center gap-1.5">
                {ix.error ? (
                  <span className="text-xs text-destructive">加载失败</span>
                ) : (
                  <>
                    <StatCard label="块数" value={ix.docs ?? '—'} />
                    <StatCard label="k1" value={ix.k1 ?? '—'} />
                    <StatCard label="b" value={ix.b ?? '—'} />
                  </>
                )}
                <StatCard label="字节" value={ix.bytes} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Bm25Docs({
  coll,
  offset,
  setOffset,
  pageSize,
  onPageSize,
  filters,
  onFilters,
  sort,
  onSort,
  onOpen,
  onRoot,
}: {
  coll: string
  offset: number
  setOffset: (n: number) => void
  pageSize: number
  onPageSize: (n: number) => void
  filters: ChromaFilters
  onFilters: (f: ChromaFilters) => void
  sort: ChromaSort
  onSort: (s: ChromaSort) => void
  onOpen: (id: string) => void
  onRoot: () => void
}) {
  const fKey = `${filters.filenameQ}|${filters.bodyQ}|${filters.tsFrom ?? ''}|${filters.tsTo ?? ''}`
  const { data, loading, error } = useAsync<Bm25DocsPage>(
    () =>
      getBm25Docs(coll, {
        limit: pageSize,
        offset,
        filenameQ: filters.filenameQ || undefined,
        bodyQ: filters.bodyQ || undefined,
        tsFrom: filters.tsFrom,
        tsTo: filters.tsTo,
        sortBy: sort.by,
        desc: sort.desc,
      }),
    `${coll}:${pageSize}:${offset}:${fKey}:${sort.by ?? ''}:${sort.desc}`,
  )
  return (
    <div>
      <Breadcrumb parts={[{ label: 'BM25', onClick: onRoot }, { label: coll }]} />
      <ChromaFilterBar filters={filters} sort={sort} onFilters={onFilters} onSort={onSort} />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
      {data?.error && <ErrorNote msg={data.error} />}
      {data?.skipped_lines ? (
        <p className="mb-2 text-xs text-amber-600 dark:text-amber-500">
          chunks.jsonl 有 {data.skipped_lines} 行损坏，已跳过
        </p>
      ) : null}
      {data && !loading && (
        <>
          <Pager
            total={data.total}
            offset={offset}
            pageSize={pageSize}
            onOffset={setOffset}
            onPageSize={onPageSize}
            className="mb-3"
          />
          <ul className="space-y-1">
            {data.items.map((it) => (
              <li key={it.id}>
                <ChunkRow
                  preview={it.preview}
                  metadata={it.metadata}
                  extra={<Pill>{it.tokens} tokens</Pill>}
                  showTime
                  onClick={() => onOpen(it.id)}
                />
              </li>
            ))}
          </ul>
          <Pager
            total={data.total}
            offset={offset}
            pageSize={pageSize}
            onOffset={setOffset}
            onPageSize={onPageSize}
            className="mt-3"
          />
        </>
      )}
    </div>
  )
}

function Bm25DocView({
  coll,
  docId,
  onBack,
  onRoot,
}: {
  coll: string
  docId: string
  onBack: () => void
  onRoot: () => void
}) {
  const { data, loading, error } = useAsync<Bm25DocDetail>(
    () => getBm25Doc(coll, docId),
    `${coll}:${docId}`,
  )
  return (
    <div>
      <Breadcrumb
        parts={[
          { label: 'BM25', onClick: onRoot },
          { label: coll, onClick: onBack },
          { label: docId },
        ]}
      />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
      {data && !loading && (
        <div className="space-y-4">
          <p className="text-xs text-muted-foreground">tokens: {data.tokens}</p>
          <section>
            <h3 className="mb-1 text-sm font-medium">metadata</h3>
            <MetadataBlock metadata={data.metadata} />
          </section>
          <section>
            <h3 className="mb-1 text-sm font-medium">正文</h3>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-sm leading-relaxed">
              {data.document || '（空正文）'}
            </pre>
          </section>
        </div>
      )}
    </div>
  )
}

// ── SQLite 面板 ────────────────────────────────────────────────────────────

type SqliteFilters = { userId?: number; timeCol?: string; tsFrom?: number; tsTo?: number }
type SqliteSort = { by?: string; desc: boolean }

function useSqliteListUrlState() {
  const url = useUrlState()
  const page = Math.max(1, url.getInt('page', 1))
  const rawSize = url.getInt('size', DEFAULT_PAGE_SIZE)
  const pageSize = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawSize)
    ? rawSize
    : DEFAULT_PAGE_SIZE
  const offset = (page - 1) * pageSize

  const uidRaw = url.get('uid')
  const fromStr = url.get('from')
  const toStr = url.get('to')
  const filters: SqliteFilters = {
    userId: uidRaw ? Number(uidRaw) : undefined,
    timeCol: url.get('tcol') || undefined,
    tsFrom: dateToEpoch(fromStr, false),
    tsTo: dateToEpoch(toStr, true),
  }
  const sortBy = url.get('sort')
  const sort: SqliteSort = {
    by: sortBy || undefined,
    desc: url.get('dir') !== 'asc',
  }

  const setOffset = (n: number) => {
    const p = Math.floor(n / pageSize) + 1
    url.patch({ page: p <= 1 ? null : p, row: null })
  }
  const setPageSize = (n: number) => {
    url.patch({ size: n === DEFAULT_PAGE_SIZE ? null : n, page: null, row: null })
  }
  const setFilters = (f: SqliteFilters) => {
    url.patch({
      uid: f.userId != null ? f.userId : null,
      tcol: f.timeCol || null,
      from: f.tsFrom ? epochToDateInput(f.tsFrom) : null,
      to: f.tsTo ? epochToDateInput(f.tsTo) : null,
      page: null,
      row: null,
    })
  }
  const setSort = (s: SqliteSort) => {
    url.patch({
      sort: s.by || null,
      dir: s.by && !s.desc ? 'asc' : null,
      page: null,
      row: null,
    })
  }
  const rowKey = url.get('row')

  const setRowKey = (key: string | null) => {
    url.patch({ row: key })
  }

  return {
    offset,
    pageSize,
    filters,
    sort,
    rowKey,
    setOffset,
    setPageSize,
    setFilters,
    setSort,
    setRowKey,
  }
}

function SqlitePanel({
  seg1,
  seg2,
  onPathChange,
}: {
  seg1?: string
  seg2?: string
  onPathChange: (path: string) => void
}) {
  const {
    offset,
    pageSize,
    filters,
    sort,
    rowKey,
    setOffset,
    setPageSize,
    setFilters,
    setSort,
    setRowKey,
  } = useSqliteListUrlState()

  if (seg1 && seg2) {
    return (
      <SqliteRows
        sel={{ dbKey: seg1, file: seg1, table: seg2 }}
        offset={offset}
        setOffset={setOffset}
        pageSize={pageSize}
        onPageSize={setPageSize}
        filters={filters}
        onFilters={setFilters}
        sort={sort}
        onSort={setSort}
        rowKey={rowKey}
        onRowKey={setRowKey}
        onBack={() => onPathChange(databasePath('sqlite', seg1))}
        onRoot={() => onPathChange(databasePath('sqlite'))}
      />
    )
  }
  if (seg1) {
    return (
      <SqliteTables
        db={{ key: seg1, file: seg1 }}
        onOpen={(t) => onPathChange(databasePath('sqlite', seg1, t))}
        onRoot={() => onPathChange(databasePath('sqlite'))}
      />
    )
  }
  return (
    <SqliteDbList onOpen={(key) => onPathChange(databasePath('sqlite', key))} />
  )
}

// 各业务库的中文名（键为文件名去扩展名，与后端 db.key 对齐）
const DB_LABELS: Record<string, string> = {
  session: '对话历史',
  auth: '认证',
  usage: '用量统计',
  rag_golden: 'RAG 评估 Golden集',
  user_memory: '用户记忆',
  learning: '学习计划',
  quiz: '测验',
  srs: '复习（SRS）',
}

// 从完整文件路径取所在目录（兼容 Windows 反斜杠与 POSIX 斜杠）
function dirOf(p: string): string {
  const i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'))
  return i >= 0 ? p.slice(0, i) : p
}

// 各表的简述（键为表名，跨库基本唯一）
const TABLE_DESCRIPTIONS: Record<string, string> = {
  sessions: '对话会话（标题、时间等）',
  messages: '会话内逐条消息记录',
  users: '用户账号',
  auth_sessions: '登录会话 / 令牌',
  user_settings: '用户个人设置',
  user_rules: '用户自定义规则',
  usage_events: '每次调用的 token 用量与计费',
  model_pricing: '各模型单价',
  saving_events: '降本（省钱）事件',
  cache_lookups: '语义缓存命中 / 未命中统计',
  agent_traces: 'Agent 调用链路 trace',
  trace_spans: 'trace 下的 span 明细',
  security_events: '安全事件（拦截 / 告警）',
  rag_golden: 'RAG 评估的 golden 问答集',
  user_memories: '跨会话的长期用户记忆',
  learning_plans: '学习计划',
  learning_tasks: '学习计划下的任务',
  quiz_sets: '测验集',
  quiz_questions: '测验题目',
  srs_cards: '间隔重复记忆卡片',
}

// L1：只列 DB（与 Chroma 的 collection 列表对齐）
function SqliteDbList({ onOpen }: { onOpen: (key: string) => void }) {
  const { data, loading, error } = useAsync<SqliteDatabases>(getSqliteDatabases, 'sqlite-dbs')
  if (loading) return <Spinner />
  if (error) return <ErrorNote msg={error} />
  if (!data) return null
  return (
    <div>
      <Breadcrumb parts={[{ label: 'SQLite' }]} />
      <ul className="space-y-1">
        {data.databases.map((db) => {
          const name = `${DB_LABELS[db.key] ?? db.key}（${db.file}）`
          const dir = dirOf(db.path)
          const middle = db.error
            ? '读取异常'
            : db.exists
              ? `包含 ${db.tables.length} 个表`
              : '文件不存在'
          return (
            <li key={db.key}>
              <button
                type="button"
                onClick={() => db.exists && onOpen(db.key)}
                disabled={!db.exists}
                className="grid w-full grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,2fr)] items-center gap-3 rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-muted/50 disabled:opacity-60"
              >
                <span className="truncate font-medium" title={name}>
                  {name}
                </span>
                <span className="truncate text-muted-foreground">{middle}</span>
                <span className="truncate text-xs text-muted-foreground" title={dir}>
                  目录：{dir}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// L2：某个 DB 下的表列表
function SqliteTables({
  db,
  onOpen,
  onRoot,
}: {
  db: { key: string; file: string }
  onOpen: (table: string) => void
  onRoot: () => void
}) {
  const { data, loading, error } = useAsync<SqliteDatabases>(getSqliteDatabases, 'sqlite-dbs')
  const found = data?.databases.find((d) => d.key === db.key)
  return (
    <div>
      <Breadcrumb
        parts={[
          { label: 'SQLite', onClick: onRoot },
          {
            label: found?.file ?? `${DB_LABELS[db.key] ?? db.key}`,
            onClick: onRoot,
          },
        ]}
      />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
      {data && !loading && (
        <>
          {found?.error && <ErrorNote msg={found.error} />}
          {!found || found.tables.length === 0 ? (
            <p className="text-sm text-muted-foreground">（无表）</p>
          ) : (
            <ul className="space-y-1">
              {found.tables.map((t) => (
                <li key={t.name}>
                  <button
                    type="button"
                    onClick={() => onOpen(t.name)}
                    className="grid w-full grid-cols-[minmax(0,1.5fr)_minmax(0,3fr)_minmax(0,1fr)] items-center gap-3 rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-muted/50"
                  >
                    <span className="truncate font-medium" title={t.name}>
                      {t.name}
                    </span>
                    <span className="truncate text-muted-foreground" title={TABLE_DESCRIPTIONS[t.name] ?? ''}>
                      {TABLE_DESCRIPTIONS[t.name] ?? '—'}
                    </span>
                    <span className="text-xs text-muted-foreground">包含 {t.rows} 条记录</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

// 时间列范围 / user_id 过滤 + 排序条；可选列依据该表实际列动态显示
function SqliteFilterBar({
  columns,
  filters,
  sort,
  onFilters,
  onSort,
}: {
  columns: string[]
  filters: SqliteFilters
  sort: SqliteSort
  onFilters: (f: SqliteFilters) => void
  onSort: (s: SqliteSort) => void
}) {
  const hasUserId = columns.includes('user_id')
  const timeCols = columns.filter(isTimeCol)
  const [userId, setUserId] = useState(filters.userId != null ? String(filters.userId) : '')
  const [timeCol, setTimeCol] = useState(filters.timeCol ?? timeCols[0] ?? '')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  if (!hasUserId && timeCols.length === 0) return null

  const sortFields = [...(hasUserId ? ['user_id'] : []), ...timeCols]
  const inputCls = 'rounded-md border border-border bg-background px-2 py-1 text-foreground'

  const apply = () => {
    const uid = userId.trim()
    onFilters({
      userId: uid === '' ? undefined : Number(uid),
      timeCol: timeCol || undefined,
      tsFrom: dateToEpoch(from, false),
      tsTo: dateToEpoch(to, true),
    })
  }
  const clear = () => {
    setUserId('')
    setFrom('')
    setTo('')
    onFilters({})
  }

  return (
    <div className="mb-3 flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {hasUserId && (
          <label className="flex items-center gap-1 text-muted-foreground">
            user_id
            <input
              value={userId}
              inputMode="numeric"
              onChange={(e) => setUserId(e.target.value.replace(/[^0-9]/g, ''))}
              onKeyDown={(e) => e.key === 'Enter' && apply()}
              placeholder="精确"
              className={cn(inputCls, 'w-20')}
            />
          </label>
        )}
        {timeCols.length > 0 && (
          <label className="flex items-center gap-1 text-muted-foreground">
            <select value={timeCol} onChange={(e) => setTimeCol(e.target.value)} className={inputCls}>
              {timeCols.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls} />
            –
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={inputCls} />
          </label>
        )}
        <button type="button" onClick={apply} className="rounded-md border border-border px-2 py-1 hover:bg-muted/50">
          查询
        </button>
        <button
          type="button"
          onClick={clear}
          className="rounded-md border border-border px-2 py-1 text-muted-foreground hover:bg-muted/50"
        >
          清除
        </button>
      </div>
      <label className="flex items-center gap-1 text-muted-foreground">
        排序
        <select
          value={sort.by ?? ''}
          onChange={(e) => onSort({ ...sort, by: e.target.value || undefined })}
          className={inputCls}
        >
          <option value="">默认</option>
          {sortFields.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => onSort({ ...sort, desc: !sort.desc })}
          disabled={!sort.by}
          className="rounded-md border border-border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
          title={sort.desc ? '降序' : '升序'}
        >
          {sort.desc ? '↓' : '↑'}
        </button>
      </label>
    </div>
  )
}

// L3：表数据
function SqliteRows({
  sel,
  offset,
  setOffset,
  pageSize,
  onPageSize,
  filters,
  onFilters,
  sort,
  onSort,
  rowKey,
  onRowKey,
  onBack,
  onRoot,
}: {
  sel: { dbKey: string; file: string; table: string }
  offset: number
  setOffset: (n: number) => void
  pageSize: number
  onPageSize: (n: number) => void
  filters: SqliteFilters
  onFilters: (f: SqliteFilters) => void
  sort: SqliteSort
  onSort: (s: SqliteSort) => void
  rowKey: string
  onRowKey: (key: string | null) => void
  onBack: () => void
  onRoot: () => void
}) {
  const [ephemeralDetail, setEphemeralDetail] = useState<Record<string, unknown> | null>(null)
  const fKey = `${filters.userId ?? ''}|${filters.timeCol ?? ''}|${filters.tsFrom ?? ''}|${filters.tsTo ?? ''}`
  const { data, loading, error } = useAsync<SqliteTableRows>(
    () =>
      getSqliteTableRows(sel.dbKey, sel.table, {
        limit: pageSize,
        offset,
        userId: filters.userId,
        timeCol: filters.timeCol,
        tsFrom: filters.tsFrom,
        tsTo: filters.tsTo,
        sortBy: sort.by,
        desc: sort.desc,
      }),
    `${sel.dbKey}:${sel.table}:${pageSize}:${offset}:${fKey}:${sort.by ?? ''}:${sort.desc}`,
  )
  // user_id → 用户名 映射（用 admin 用户列表）；auth.db 的 users 表本身不标注
  const { data: users } = useAsync<UserInfo[]>(listUsers, 'sqlite-user-map')
  const userMap: Record<string, string> = {}
  for (const u of users ?? []) userMap[String(u.id)] = u.username
  const annotateUser = !(sel.dbKey === 'auth' && sel.table === 'users')
  const hasIdCol = data?.columns.includes('id') ?? false
  const urlDetail = rowKey
    ? data?.rows.find((r) => hasIdCol && String(r.id) === rowKey) ?? null
    : null
  const detail = urlDetail ?? ephemeralDetail
  return (
    <div>
      <Breadcrumb
        parts={[
          { label: 'SQLite', onClick: onRoot },
          { label: sel.file, onClick: onBack },
          { label: sel.table },
        ]}
      />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
      {data && !loading && (
        <>
          <SqliteFilterBar
            columns={data.columns}
            filters={filters}
            sort={sort}
            onFilters={onFilters}
            onSort={onSort}
          />
          {data.masked_columns.length > 0 && (
            <p className="mb-2 text-xs text-muted-foreground">
              已脱敏列：{data.masked_columns.join(', ')}
            </p>
          )}
          {detail && (
            <div className="mb-3 rounded-md border border-border p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">行详情</span>
                <button
                  type="button"
                  onClick={() => {
                    onRowKey(null)
                    setEphemeralDetail(null)
                  }}
                  className="rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted/50"
                >
                  关闭
                </button>
              </div>
              <dl className="space-y-1.5 text-sm">
                {data.columns.map((c) => {
                  const ann = annotateUserId(c, detail[c], userMap, annotateUser)
                  return (
                    <div key={c} className="grid grid-cols-[10rem_1fr] gap-2">
                      <dt className="truncate font-mono text-xs text-muted-foreground" title={c}>
                        {c}
                      </dt>
                      <dd className="min-w-0">
                        {ann ? <span>{ann}</span> : <SqliteDetailValue col={c} value={detail[c]} />}
                      </dd>
                    </div>
                  )
                })}
              </dl>
            </div>
          )}
          <Pager
            total={data.total}
            offset={offset}
            pageSize={pageSize}
            onOffset={setOffset}
            onPageSize={onPageSize}
            className="mb-3"
          />
          <p className="mb-1 text-xs text-muted-foreground">点击任一行查看完整字段</p>
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 z-10 bg-muted text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-1.5 font-medium">#</th>
                  {data.columns.map((c) => (
                    <th key={c} className="whitespace-nowrap px-3 py-1.5 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr
                    key={i}
                    onClick={() => {
                      if (hasIdCol && row.id != null) {
                        onRowKey(String(row.id))
                        setEphemeralDetail(null)
                      } else {
                        onRowKey(null)
                        setEphemeralDetail(row)
                      }
                    }}
                    className={cn(
                      'cursor-pointer border-t border-border align-top hover:bg-accent/40',
                      i % 2 === 1 && 'bg-muted/20',
                    )}
                  >
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">{offset + i + 1}</td>
                    {data.columns.map((c) => {
                      const isId = c === 'id' || c.toLowerCase().endsWith('_id')
                      const ann = annotateUserId(c, row[c], userMap, annotateUser)
                      const isNum = !ann && typeof row[c] === 'number' && !isTimeCol(c)
                      const text = ann ?? fmtSqliteCell(c, row[c])
                      return (
                        <td
                          key={c}
                          className={cn(
                            'max-w-xs truncate px-3 py-1.5',
                            isId && 'font-mono text-xs',
                            isNum && 'text-right tabular-nums',
                          )}
                          title={text}
                        >
                          {text}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={data.total}
            offset={offset}
            pageSize={pageSize}
            onOffset={setOffset}
            onPageSize={onPageSize}
            className="mt-3"
          />
        </>
      )}
    </div>
  )
}

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// ── 维护面板（破坏性，admin）──────────────────────────────────────────────

function MaintCountsTable({ items, total }: { items: { db: string; table: string; count: number }[]; total: number }) {
  const nonZero = items.filter((i) => i.count > 0)
  if (total === 0) return <p className="text-sm text-muted-foreground">没有符合条件的数据。</p>
  return (
    <div className="rounded-md border border-border">
      <table className="w-full text-left text-sm">
        <thead className="bg-muted/30 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-1.5 font-medium">库</th>
            <th className="px-3 py-1.5 font-medium">表</th>
            <th className="px-3 py-1.5 text-right font-medium">将删行数</th>
          </tr>
        </thead>
        <tbody>
          {nonZero.map((i) => (
            <tr key={`${i.db}.${i.table}`} className="border-t border-border">
              <td className="px-3 py-1.5">{i.db}</td>
              <td className="px-3 py-1.5">{i.table}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{i.count}</td>
            </tr>
          ))}
          <tr className="border-t border-border font-medium">
            <td className="px-3 py-1.5" colSpan={2}>
              合计
            </td>
            <td className="px-3 py-1.5 text-right tabular-nums">{total}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function Card({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-border p-4">
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mb-3 mt-0.5 text-xs text-muted-foreground">{desc}</p>
      {children}
    </section>
  )
}

const btnCls = 'rounded-md border border-border px-2.5 py-1 hover:bg-muted/50 disabled:opacity-40'
const dangerBtnCls =
  'rounded-md bg-destructive px-2.5 py-1 text-destructive-foreground hover:bg-destructive/90 disabled:opacity-40'
const inputCls = 'rounded-md border border-border bg-background px-2 py-1 text-foreground'

function PrunePanel() {
  const [days, setDays] = useState('90')
  const [preview, setPreview] = useState<PruneResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)

  const doPreview = async () => {
    setBusy(true)
    try {
      setPreview(await getPrunePreview(Number(days)))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '预览失败')
    } finally {
      setBusy(false)
    }
  }
  const doRun = async () => {
    setBusy(true)
    try {
      const r = await runPrune(Number(days))
      toast.success(`已清理 ${r.total} 行`)
      setPreview(null)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '清理失败')
    } finally {
      setBusy(false)
      setConfirm(false)
    }
  }

  return (
    <Card
      title="保留期清理"
      desc="删除事件 / 日志类表中早于保留天数的行（usage_events、agent_traces、trace_spans、cache_lookups、saving_events、security_events）+ 过期登录会话。不动对话、记忆等用户内容。"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1 text-muted-foreground">
          保留天数
          <input
            value={days}
            inputMode="numeric"
            onChange={(e) => setDays(e.target.value.replace(/[^0-9]/g, ''))}
            className={cn(inputCls, 'w-20')}
          />
        </label>
        <button type="button" onClick={doPreview} disabled={busy || !days} className={btnCls}>
          预览
        </button>
        <button
          type="button"
          onClick={() => setConfirm(true)}
          disabled={busy || !preview || preview.total === 0}
          className={dangerBtnCls}
        >
          执行清理
        </button>
      </div>
      {preview && <MaintCountsTable items={preview.items} total={preview.total} />}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="确认清理？"
        description={`将删除约 ${preview?.total ?? 0} 行（保留 ${days} 天内），不可恢复。`}
        loading={busy}
        onConfirm={doRun}
      />
    </Card>
  )
}

function PurgeUserPanel() {
  const [uid, setUid] = useState('')
  const [preview, setPreview] = useState<PurgePreview | null>(null)
  // sessions 表逐行勾选：被取消勾选的 rowid 集合（空集 = 全选）
  const [excluded, setExcluded] = useState<Record<string, Set<number>>>({})
  // 其它表只整表勾选：被关掉的表 key 集合
  const [disabledTables, setDisabledTables] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)

  const doPreview = async () => {
    setBusy(true)
    try {
      setPreview(await getPurgeUserPreview(Number(uid)))
      setExcluded({})
      setDisabledTables(new Set())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '预览失败')
    } finally {
      setBusy(false)
    }
  }

  const buildSelections = (): PurgeSelection[] => {
    if (!preview) return []
    const out: PurgeSelection[] = []
    for (const t of preview.tables) {
      const key = `${t.db}.${t.table}`
      if (t.table === 'sessions') {
        const ex = excluded[key]
        if (!ex || ex.size === 0) {
          out.push({ db: t.db, table: t.table, all: true, rowids: [] })
        } else {
          const ids = t.rows.map((r) => Number(r.rowid)).filter((id) => !ex.has(id))
          if (ids.length) out.push({ db: t.db, table: t.table, all: false, rowids: ids })
        }
      } else if (!disabledTables.has(key)) {
        out.push({ db: t.db, table: t.table, all: true, rowids: [] })
      }
    }
    return out
  }

  const selectedCount = (): number => {
    if (!preview) return 0
    let n = 0
    for (const t of preview.tables) {
      const key = `${t.db}.${t.table}`
      if (t.table === 'sessions') {
        const ex = excluded[key]
        n += !ex || ex.size === 0 ? t.total : t.rows.length - ex.size
      } else if (!disabledTables.has(key)) {
        n += t.total
      }
    }
    return n
  }

  const doRun = async () => {
    setBusy(true)
    try {
      const r = await runPurgeUser(Number(uid), buildSelections())
      toast.success(`已清理 user_id=${uid} 的 ${r.total} 行（含级联）`)
      setPreview(null)
      setExcluded({})
      setDisabledTables(new Set())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '清理失败')
    } finally {
      setBusy(false)
      setConfirm(false)
    }
  }

  const toggleRow = (key: string, rowid: number) => {
    setExcluded((prev) => {
      const next = new Set(prev[key] ?? [])
      if (next.has(rowid)) next.delete(rowid)
      else next.add(rowid)
      return { ...prev, [key]: next }
    })
  }
  const toggleAll = (key: string, rowids: number[], allChecked: boolean) => {
    setExcluded((prev) => ({ ...prev, [key]: allChecked ? new Set(rowids) : new Set() }))
  }
  const toggleTable = (key: string) => {
    setDisabledTables((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const total = selectedCount()

  return (
    <Card
      title="按 user_id 清理数据"
      desc="预览各表将删的行，勾选要清理的行（默认全选）；子表（messages 等）跟随父行级联删除，不单列。不删 users 账号行本身。"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1 text-muted-foreground">
          user_id
          <input
            value={uid}
            inputMode="numeric"
            onChange={(e) => setUid(e.target.value.replace(/[^0-9]/g, ''))}
            className={cn(inputCls, 'w-20')}
          />
        </label>
        <button type="button" onClick={doPreview} disabled={busy || !uid} className={btnCls}>
          预览
        </button>
        <button
          type="button"
          onClick={() => setConfirm(true)}
          disabled={busy || !preview || total === 0}
          className={dangerBtnCls}
        >
          清理选中（{total} 行）
        </button>
      </div>

      {preview && preview.tables.length === 0 && (
        <p className="text-sm text-muted-foreground">该 user_id 没有可清理的数据。</p>
      )}

      <div className="space-y-3">
        {preview?.tables.map((t) => {
          const key = `${t.db}.${t.table}`
          const isSessions = t.table === 'sessions'
          const ex = excluded[key] ?? new Set<number>()
          const rowids = t.rows.map((r) => Number(r.rowid))
          // sessions：逐行勾选，首列显示首问内容；其它表：整表勾选、不列明细
          const tableChecked = isSessions ? ex.size === 0 : !disabledTables.has(key)
          const dispCols = isSessions
            ? ['first_user_msg', 'created_at'].filter((c) => t.columns.includes(c))
            : []
          return (
            <div key={key} className="rounded-md border border-border">
              <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-sm">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={tableChecked}
                    onChange={() =>
                      isSessions ? toggleAll(key, rowids, tableChecked) : toggleTable(key)
                    }
                  />
                  <span className="font-medium">{t.table}</span>
                  <span className="text-xs text-muted-foreground">（{t.db}）</span>
                </label>
                <span className="text-xs text-muted-foreground">
                  共 {t.total} 行{isSessions && t.truncated ? `，仅显示前 ${preview.cap}` : ''}
                  {t.child ? ` · 级联删 ${t.child}` : ''}
                </span>
              </div>
              {isSessions && (
                <div className="max-h-64 overflow-auto border-t border-border">
                  <table className="w-full text-left text-sm">
                    <tbody>
                      {t.rows.map((r) => {
                        const rid = Number(r.rowid)
                        return (
                          <tr key={rid} className="border-t border-border first:border-t-0">
                            <td className="w-8 px-2 py-1">
                              <input
                                type="checkbox"
                                checked={!ex.has(rid)}
                                onChange={() => toggleRow(key, rid)}
                              />
                            </td>
                            {dispCols.map((c) => {
                              const text = fmtSqliteCell(c, r[c])
                              return (
                                <td key={c} className="max-w-md truncate px-2 py-1" title={text}>
                                  {text || (c === 'first_user_msg' ? '（无首问）' : '')}
                                </td>
                              )
                            })}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="确认清理？"
        description={`将删除 user_id=${uid} 选中的约 ${total} 行（含子表级联），不可恢复。`}
        loading={busy}
        onConfirm={doRun}
      />
    </Card>
  )
}

function VacuumPanel() {
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [result, setResult] = useState<VacuumResult | null>(null)

  const doRun = async () => {
    setBusy(true)
    try {
      const r = await runVacuum()
      setResult(r)
      const freed = r.results.reduce((s, x) => s + (x.freed_bytes ?? 0), 0)
      toast.success(`VACUUM 完成，回收 ${freed} 字节`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'VACUUM 失败')
    } finally {
      setBusy(false)
      setConfirm(false)
    }
  }

  return (
    <Card title="VACUUM 回收空间" desc="对全部 SQLite 库执行 VACUUM，回收删除后未释放的磁盘空间。期间相关库会被独占写锁。">
      <button type="button" onClick={() => setConfirm(true)} disabled={busy} className={btnCls}>
        对全部库执行 VACUUM
      </button>
      {result && (
        <ul className="mt-3 space-y-0.5 text-sm">
          {result.results.map((x) => (
            <li key={x.db} className="text-muted-foreground">
              {x.db}：{x.ok ? `回收 ${x.freed_bytes ?? 0} 字节` : `失败（${x.error}）`}
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="确认 VACUUM？"
        description="将对全部 SQLite 库执行 VACUUM，期间短暂占用写锁。"
        loading={busy}
        onConfirm={doRun}
      />
    </Card>
  )
}

function MaintenancePanel() {
  return (
    <div className="space-y-4">
      <PrunePanel />
      <PurgeUserPanel />
      <OrphanSegmentsPanel />
      <VacuumPanel />
      <RepairPanel />
    </div>
  )
}

// 字节 → 人类可读
function fmtBytes(n: number): string {
  if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}

function RepairPanel() {
  const [preview, setPreview] = useState<RepairPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [busyLabel, setBusyLabel] = useState('')
  const [confirm, setConfirm] = useState(false)

  const doPreview = async () => {
    setBusy(true)
    setBusyLabel('正在扫描 BM25 侧车与 Chroma 对齐…')
    try {
      setPreview(await getRepairPreview())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '扫描失败')
    } finally {
      setBusy(false)
      setBusyLabel('')
    }
  }

  const doRun = async () => {
    setBusy(true)
    setBusyLabel('正在对齐并修复 BM25 索引，大库可能需要数十秒…')
    try {
      const r = await runRepair()
      if (r.failed > 0) {
        toast.error(`修复完成：成功 ${r.repaired}，失败 ${r.failed}`)
      } else if (r.repaired > 0) {
        toast.success(`已修复 ${r.repaired} 个 BM25 索引`)
      } else {
        toast.success('没有需要修复的索引')
      }
      setBusyLabel('正在刷新扫描结果…')
      setPreview(await getRepairPreview())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '修复失败')
    } finally {
      setBusy(false)
      setBusyLabel('')
      setConfirm(false)
    }
  }

  const broken = preview?.indexes.filter((i) => i.needs_repair) ?? []
  const misaligned = preview?.indexes.filter((i) => i.needs_align) ?? []
  const needsAction = (preview?.needs_repair ?? 0) + (preview?.needs_align ?? 0)

  return (
    <Card
      title="BM25 修复"
      desc="修复侧车文件（manifest / chunks.jsonl），并以 Chroma 为准对齐 BM25 块数（删孤儿块、补缺失块）。"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <button type="button" onClick={doPreview} disabled={busy} className={btnCls}>
          扫描
        </button>
        <button
          type="button"
          onClick={() => setConfirm(true)}
          disabled={busy || !preview || needsAction === 0}
          className={btnCls}
        >
          修复
        </button>
      </div>

      {busy && (
        <div className="mb-3 flex items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
          {busyLabel || '处理中…'}
        </div>
      )}

      {!busy && preview && needsAction === 0 && (
        <p className="text-sm text-muted-foreground">全部 BM25 索引侧车正常，且与 Chroma 块数一致。</p>
      )}
      {preview && preview.needs_align > 0 && (
        <div className={cn('mb-3 rounded-md border border-border', busy && 'opacity-60')}>
          <div className="border-b border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
            需与 Chroma 对齐 {preview.needs_align} 个索引
          </div>
          <ul className="max-h-48 overflow-auto">
            {misaligned.map((ix) => (
              <li
                key={ix.collection}
                className="border-t border-border/50 px-3 py-2 text-sm first:border-t-0"
              >
                <div className="font-medium">{ix.collection}</div>
                <div className="text-xs text-muted-foreground">
                  BM25 {ix.bm25_chunks?.toLocaleString() ?? '—'} 块 · Chroma{' '}
                  {ix.chroma_chunks?.toLocaleString() ?? '—'} 条
                  {ix.orphan_bm25 ? ` · BM25 孤儿 ${ix.orphan_bm25.toLocaleString()}` : ''}
                  {ix.orphan_chroma ? ` · Chroma 缺 BM25 ${ix.orphan_chroma.toLocaleString()}` : ''}
                </div>
                {ix.align_error && (
                  <div className="text-xs text-destructive">{ix.align_error}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {preview && preview.needs_repair > 0 && (
        <div className={cn('rounded-md border border-border', busy && 'opacity-60')}>
          <div className="border-b border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
            侧车需修复 {preview.needs_repair} 个索引
          </div>
          <ul className="max-h-48 overflow-auto">
            {broken.map((ix) => (
              <li
                key={ix.collection}
                className="border-t border-border/50 px-3 py-2 text-sm first:border-t-0"
              >
                <div className="font-medium">{ix.collection}</div>
                <div className="text-xs text-destructive">{ix.error}</div>
                {ix.manifest_docs != null && (
                  <div className="text-xs text-muted-foreground">
                    manifest {ix.manifest_docs} 块
                    {ix.skipped_lines ? ` · 坏行 ${ix.skipped_lines}` : ''}
                    {ix.duplicate_ids ? ` · 重复 id ${ix.duplicate_ids}` : ''}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="确认修复 BM25？"
        description={`将对 ${preview?.needs_align ?? 0} 个索引做 Chroma 对齐，并重建 ${preview?.needs_repair ?? 0} 个侧车文件。会改动 pkl 与 manifest / chunks.jsonl。`}
        loading={busy}
        confirmLabel="确认"
        onConfirm={doRun}
      />
    </Card>
  )
}

function OrphanSegmentsPanel() {
  const [preview, setPreview] = useState<OrphanSegmentsPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)

  const doPreview = async () => {
    setBusy(true)
    try {
      setPreview(await getOrphanSegmentsPreview())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '预览失败')
    } finally {
      setBusy(false)
    }
  }
  const doRun = async () => {
    setBusy(true)
    try {
      const r = await cleanupOrphanSegments()
      toast.success(`已清理 ${r.removed.length} 个孤儿段，回收 ${fmtBytes(r.freed_bytes)}`)
      if (r.failed.length > 0) {
        toast.error(`${r.failed.length} 个删除失败（可能被占用）`)
      }
      setPreview(null)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '清理失败')
    } finally {
      setBusy(false)
      setConfirm(false)
    }
  }

  return (
    <Card
      title="孤儿段清理"
      desc="清空 / 删除知识库后，Chroma 在磁盘上残留不再被任何库引用的 <uuid>/ 向量段目录（只占空间，不影响检索）。这里把它们物理删除。"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <button type="button" onClick={doPreview} disabled={busy} className={btnCls}>
          扫描
        </button>
        <button
          type="button"
          onClick={() => setConfirm(true)}
          disabled={busy || !preview || !preview.available || preview.count === 0}
          className={dangerBtnCls}
        >
          清理孤儿段
        </button>
      </div>

      {preview && !preview.available && (
        <p className="text-sm text-muted-foreground">
          无法读取 chroma.sqlite3，已跳过（不会误删）。
        </p>
      )}
      {preview && preview.available && preview.count === 0 && (
        <p className="text-sm text-muted-foreground">没有孤儿段，磁盘已干净。</p>
      )}
      {preview && preview.available && preview.count > 0 && (
        <div className="rounded-md border border-border">
          <div className="flex items-center justify-between border-b border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
            <span>共 {preview.count} 个孤儿段</span>
            <span>合计 {fmtBytes(preview.total_bytes)}</span>
          </div>
          <ul className="max-h-64 overflow-auto">
            {preview.items.map((it) => (
              <li
                key={it.uuid}
                className="flex items-center justify-between border-t border-border/50 px-3 py-1 text-sm first:border-t-0"
              >
                <span className="truncate font-mono text-xs" title={it.uuid}>
                  {it.uuid}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {fmtBytes(it.bytes)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="确认清理孤儿段？"
        description={`将物理删除 ${preview?.count ?? 0} 个孤儿段目录（约 ${fmtBytes(preview?.total_bytes ?? 0)}），不可恢复。`}
        loading={busy}
        onConfirm={doRun}
      />
    </Card>
  )
}

// 时间列：列名以 _at 结尾或就叫 timestamp
function isTimeCol(col: string): boolean {
  const c = col.toLowerCase()
  return c.endsWith('_at') || c === 'timestamp' || c.includes('expires')
}

// 时间值 → 可读串：数字/数字串按 epoch；ISO 串把日期与时间间的 T 换成空格。非时间值返回 null。
function fmtTimeCell(v: unknown): string | null {
  if (typeof v === 'number') return Number.isFinite(v) && v > 0 ? fmtTime(v) : null
  if (typeof v === 'string' && v) {
    if (!Number.isNaN(Number(v))) return fmtTime(Number(v))
    const m = v.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/)
    if (m) return `${m[1]} ${m[2]}`
  }
  return null
}

// 表格单元格短展示：时间列渲染成可读时间，其余截断
function fmtSqliteCell(col: string, v: unknown): string {
  if (isTimeCol(col)) {
    const t = fmtTimeCell(v)
    if (t) return t
  }
  return fmtCell(v)
}

// 与后端 config.DEFAULT_USER_ID 默认值对齐：CLI / 关认证时的兜底身份，users 表里通常没有对应行
const DEFAULT_USER_ID = 1

// user_id 列：值后拼用户名，如 "4 (Admin)"；兜底身份标 "(默认/CLI)"；其余未知/未启用返回 null
function annotateUserId(
  col: string,
  v: unknown,
  userMap: Record<string, string>,
  enabled: boolean,
): string | null {
  if (!enabled || col !== 'user_id' || v == null || v === '') return null
  const name = userMap[String(v)]
  if (name) return `${v} (${name})`
  if (String(v) === String(DEFAULT_USER_ID)) return `${v} (默认/CLI)`
  return null
}

// JSON 字符串 → 美化串；非 JSON 返回 null
function tryPrettyJson(s: string): string | null {
  const t = s.trim()
  if (!(t.startsWith('{') || t.startsWith('['))) return null
  try {
    return JSON.stringify(JSON.parse(t), null, 2)
  } catch {
    return null
  }
}

// 行详情里单个值的完整展示：时间格式化；JSON 字符串美化
function SqliteDetailValue({ col, value }: { col: string; value: unknown }) {
  if (isTimeCol(col)) {
    const t = fmtTimeCell(value)
    if (t) {
      return (
        <span>
          {t} <span className="text-xs text-muted-foreground">({String(value)})</span>
        </span>
      )
    }
  }
  const s = fmtCell(value)
  const pretty = tryPrettyJson(s)
  if (pretty) {
    return <pre className="overflow-x-auto rounded-md bg-muted/50 p-2 text-xs leading-relaxed">{pretty}</pre>
  }
  if (!s) return <span className="text-muted-foreground">（空）</span>
  return <span className="whitespace-pre-wrap break-words">{s}</span>
}
