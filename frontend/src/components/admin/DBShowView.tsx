import { useEffect, useState } from 'react'
import { Check, ChevronRight, Copy, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { cn } from '@/lib/utils'
import { ResourcePage } from '@/components/resources/ResourcePage'
import {
  getBm25Doc,
  getBm25Docs,
  getBm25Indexes,
  getChromaCollections,
  getChromaItem,
  getChromaItems,
  getSqliteDatabases,
  getSqliteTableRows,
} from '@/api/client'
import type {
  Bm25DocDetail,
  Bm25DocsPage,
  Bm25Indexes,
  ChromaCollections,
  ChromaItemDetail,
  ChromaItemsPage,
  Metadata,
  SqliteDatabases,
  SqliteTableRows,
} from '@/types/dbAdmin'

type Tab = 'chroma' | 'bm25' | 'sqlite'

// 后端 limit 上限 200，候选项不超过它
const PAGE_SIZE_OPTIONS = [20, 50, 100, 200] as const
const DEFAULT_PAGE_SIZE = 50

export function DBShowView() {
  const [tab, setTab] = useState<Tab>('chroma')
  const tabs: { value: Tab; label: string }[] = [
    { value: 'chroma', label: 'Chroma' },
    { value: 'bm25', label: 'BM25' },
    { value: 'sqlite', label: 'SQLite' },
  ]

  return (
    <ResourcePage
      title="DB 秀"
      subtitle="Chroma / BM25 / SQLite 的结构与内容"
      maxWidthClassName="max-w-6xl"
    >
      <div className="flex min-h-0 flex-1 gap-4">
        <nav className="sticky top-0 w-32 shrink-0 self-start">
          <ul className="space-y-0.5">
            {tabs.map((t) => (
              <li key={t.value}>
                <button
                  type="button"
                  onClick={() => setTab(t.value)}
                  className={cn(
                    'w-full rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
                    tab === t.value
                      ? 'bg-muted font-medium text-foreground'
                      : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                  )}
                >
                  {t.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="min-w-0 flex-1">
          {tab === 'chroma' && <ChromaPanel />}
          {tab === 'bm25' && <Bm25Panel />}
          {tab === 'sqlite' && <SqlitePanel />}
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

// chunk 的正文是带换行的大段文本：压成单行、合并空白，作为可扫读的摘要。
function normalizePreview(s: string): string {
  return (s || '').replace(/\s+/g, ' ').trim()
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id
}

function metaStr(m: Metadata, key: string): string | undefined {
  if (!m) return undefined
  const v = (m as Record<string, unknown>)[key]
  return v === undefined || v === null ? undefined : String(v)
}

function CopyId({ id }: { id: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      title={`复制完整 id：${id}`}
      onClick={(e) => {
        e.stopPropagation()
        navigator.clipboard
          ?.writeText(id)
          .then(() => {
            setDone(true)
            toast.success('已复制 id')
            setTimeout(() => setDone(false), 1200)
          })
          .catch(() => toast.error('复制失败'))
      }}
      className="flex items-center gap-1 rounded px-1 font-mono text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      {shortId(id)}
      {done ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    </button>
  )
}

// Chroma / BM25 第二层共用：结构信息（来源/块/语言/行/章节）当主角，正文摘要当配角。
function ChunkRow({
  id,
  preview,
  metadata,
  extra,
  onClick,
}: {
  id: string
  preview: string
  metadata: Metadata
  extra?: React.ReactNode
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
          {chunk && <Pill>{chunk}</Pill>}
          {lang && <Pill>{lang}</Pill>}
          {lines && <Pill>{lines}</Pill>}
          <CopyId id={id} />
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

function ChromaPanel() {
  const [sel, setSel] = useState<{ name: string; itemId?: string } | null>(null)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const changePageSize = (n: number) => {
    setPageSize(n)
    setOffset(0)
  }

  if (sel?.itemId) {
    return (
      <ChromaDetail
        name={sel.name}
        itemId={sel.itemId}
        onBack={() => setSel({ name: sel.name })}
        onRoot={() => setSel(null)}
      />
    )
  }
  if (sel) {
    return (
      <ChromaItems
        name={sel.name}
        offset={offset}
        setOffset={setOffset}
        pageSize={pageSize}
        onPageSize={changePageSize}
        onOpen={(id) => setSel({ name: sel.name, itemId: id })}
        onRoot={() => {
          setSel(null)
          setOffset(0)
        }}
      />
    )
  }
  return (
    <ChromaList
      onOpen={(name) => {
        setSel({ name })
        setOffset(0)
      }}
    />
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
        {data.collections.map((c) => (
          <li key={c.name}>
            <button
              type="button"
              onClick={() => onOpen(c.name)}
              className="flex w-full items-center justify-between rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-muted/50"
            >
              <span className="font-medium">{c.name}</span>
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
  onOpen,
  onRoot,
}: {
  name: string
  offset: number
  setOffset: (n: number) => void
  pageSize: number
  onPageSize: (n: number) => void
  onOpen: (id: string) => void
  onRoot: () => void
}) {
  const { data, loading, error } = useAsync<ChromaItemsPage>(
    () => getChromaItems(name, { limit: pageSize, offset }),
    `${name}:${pageSize}:${offset}`,
  )
  return (
    <div>
      <Breadcrumb parts={[{ label: 'Chroma', onClick: onRoot }, { label: name }]} />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
      {data?.error && <ErrorNote msg={data.error} />}
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
                  id={it.id}
                  preview={it.preview}
                  metadata={it.metadata}
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

function Bm25Panel() {
  const [sel, setSel] = useState<{ coll: string; docId?: string } | null>(null)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const changePageSize = (n: number) => {
    setPageSize(n)
    setOffset(0)
  }

  if (sel?.docId) {
    return (
      <Bm25DocView
        coll={sel.coll}
        docId={sel.docId}
        onBack={() => setSel({ coll: sel.coll })}
        onRoot={() => setSel(null)}
      />
    )
  }
  if (sel) {
    return (
      <Bm25Docs
        coll={sel.coll}
        offset={offset}
        setOffset={setOffset}
        pageSize={pageSize}
        onPageSize={changePageSize}
        onOpen={(id) => setSel({ coll: sel.coll, docId: id })}
        onRoot={() => {
          setSel(null)
          setOffset(0)
        }}
      />
    )
  }
  return (
    <Bm25List
      onOpen={(coll) => {
        setSel({ coll })
        setOffset(0)
      }}
    />
  )
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
        {data.indexes.map((ix) => (
          <li key={ix.file}>
            <button
              type="button"
              onClick={() => !ix.error && onOpen(ix.collection)}
              disabled={!!ix.error}
              className="flex w-full items-center justify-between rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-muted/50 disabled:opacity-60"
            >
              <span className="font-medium">{ix.file}</span>
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
  onOpen,
  onRoot,
}: {
  coll: string
  offset: number
  setOffset: (n: number) => void
  pageSize: number
  onPageSize: (n: number) => void
  onOpen: (id: string) => void
  onRoot: () => void
}) {
  const { data, loading, error } = useAsync<Bm25DocsPage>(
    () => getBm25Docs(coll, { limit: pageSize, offset }),
    `${coll}:${pageSize}:${offset}`,
  )
  return (
    <div>
      <Breadcrumb parts={[{ label: 'BM25', onClick: onRoot }, { label: coll }]} />
      {loading && <Spinner />}
      {error && <ErrorNote msg={error} />}
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
                  id={it.id}
                  preview={it.preview}
                  metadata={it.metadata}
                  extra={<Pill>{it.tokens} tok</Pill>}
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

function SqlitePanel() {
  const [dbSel, setDbSel] = useState<{ key: string; file: string } | null>(null)
  const [table, setTable] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const changePageSize = (n: number) => {
    setPageSize(n)
    setOffset(0)
  }

  if (dbSel && table) {
    return (
      <SqliteRows
        sel={{ dbKey: dbSel.key, file: dbSel.file, table }}
        offset={offset}
        setOffset={setOffset}
        pageSize={pageSize}
        onPageSize={changePageSize}
        onBack={() => {
          setTable(null)
          setOffset(0)
        }}
        onRoot={() => {
          setDbSel(null)
          setTable(null)
          setOffset(0)
        }}
      />
    )
  }
  if (dbSel) {
    return (
      <SqliteTables
        db={dbSel}
        onOpen={(t) => {
          setTable(t)
          setOffset(0)
        }}
        onRoot={() => setDbSel(null)}
      />
    )
  }
  return <SqliteDbList onOpen={(key, file) => setDbSel({ key, file })} />
}

// 各业务库的中文名（键为文件名去扩展名，与后端 db.key 对齐）
const DB_LABELS: Record<string, string> = {
  chat_history: '对话历史',
  auth: '认证',
  usage: '用量统计',
  rag_golden: 'RAG 评估 Golden',
  user_memory: '用户记忆',
  learning: '学习计划',
  quiz: '测验',
  srs: '间隔重复（SRS）',
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
function SqliteDbList({ onOpen }: { onOpen: (key: string, file: string) => void }) {
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
                onClick={() => db.exists && onOpen(db.key, db.file)}
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
      <Breadcrumb parts={[{ label: 'SQLite', onClick: onRoot }, { label: db.file }]} />
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

// L3：表数据
function SqliteRows({
  sel,
  offset,
  setOffset,
  pageSize,
  onPageSize,
  onBack,
  onRoot,
}: {
  sel: { dbKey: string; file: string; table: string }
  offset: number
  setOffset: (n: number) => void
  pageSize: number
  onPageSize: (n: number) => void
  onBack: () => void
  onRoot: () => void
}) {
  const { data, loading, error } = useAsync<SqliteTableRows>(
    () => getSqliteTableRows(sel.dbKey, sel.table, { limit: pageSize, offset }),
    `${sel.dbKey}:${sel.table}:${pageSize}:${offset}`,
  )
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
          {data.masked_columns.length > 0 && (
            <p className="mb-2 text-xs text-muted-foreground">
              已脱敏列：{data.masked_columns.join(', ')}
            </p>
          )}
          <Pager
            total={data.total}
            offset={offset}
            pageSize={pageSize}
            onOffset={setOffset}
            onPageSize={onPageSize}
            className="mb-3"
          />
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/30 text-xs text-muted-foreground">
                <tr>
                  {data.columns.map((c) => (
                    <th key={c} className="whitespace-nowrap px-3 py-1.5 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i} className="border-t border-border align-top">
                    {data.columns.map((c) => (
                      <td key={c} className="max-w-xs truncate px-3 py-1.5" title={fmtCell(row[c])}>
                        {fmtCell(row[c])}
                      </td>
                    ))}
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
