import type { ReactNode } from 'react'
import { useCallback, useEffect, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Database,
  FileText,
  Layers,
  Loader2,
  RefreshCw,
  Trash2,
} from 'lucide-react'

import {
  clearAllKBDocuments,
  deleteKBDocument,
  generateGolden,
  getKBCollections,
  listKBDocuments,
} from '@/api/client'
import type { KBCollection, KBCollectionListResponse, KBDocument } from '@/types/kb'
import { DocumentList } from '@/components/kb/DocumentList'
import { GOLDEN_LLM_LABELS, GoldenGenControls } from '@/components/kb/GoldenGenControls'
import { IngestPanel } from '@/components/kb/IngestPanel'
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
import { toast } from '@/lib/toast'
import { useAuth } from '@/lib/auth'

export function KnowledgeBaseView({
  onOpenGolden,
  onGotoGolden,
  returnToAlias,
  onReturnConsumed,
}: {
  onOpenGolden?: (docId: string, label: string, alias: string) => void
  onGotoGolden?: () => void
  returnToAlias?: string | null
  onReturnConsumed?: () => void
} = {}) {
  // golden 是 admin 维护的评估集：普通用户完全隐藏 golden 相关入口
  const { isAdmin } = useAuth()
  // null = 第一层（库列表 L1）；否则进第二层（该库的文档列表 L2）
  const [alias, setAlias] = useState<string | null>(null)

  // 从 Golden 管理"返回"过来：直接打开该库 L2（一次性，消费后通知父组件清空）
  useEffect(() => {
    if (returnToAlias) {
      setAlias(returnToAlias)
      onReturnConsumed?.()
    }
  }, [returnToAlias, onReturnConsumed])

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">知识库</h1>
        <p className="text-xs text-muted-foreground">
          选择一个库查看 / 管理其文档；Agent 通过 search_knowledge 工具自动检索
        </p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-6xl space-y-6">
          {alias === null ? (
            <L1View
              onOpen={setAlias}
              onGotoGolden={isAdmin ? onGotoGolden : undefined}
              isAdmin={isAdmin}
            />
          ) : (
            <LibraryView
              alias={alias}
              onBack={() => setAlias(null)}
              showGolden={isAdmin}
              onOpenGolden={isAdmin ? onOpenGolden : undefined}
            />
          )}
        </div>
      </div>
    </div>
  )
}

// ── L1：库列表 ───────────────────────────────────────────────────────────────

// 模块级缓存：在 KB 页反复进出时立即回显上次结果，后台再静默校验，避免每次都干等。
let _cachedKbCollections: KBCollectionListResponse | null = null

function L1View({
  onOpen,
  onGotoGolden,
  isAdmin,
}: {
  onOpen: (alias: string) => void
  onGotoGolden?: () => void
  isAdmin: boolean
}) {
  const [collections, setCollections] = useState<KBCollection[]>(
    () => _cachedKbCollections?.collections ?? [],
  )
  const [defaultIngestAlias, setDefaultIngestAlias] = useState(
    () => _cachedKbCollections?.default_ingest_alias ?? '',
  )
  // 有缓存就先不转圈，直接显示旧数据；无缓存才显示首屏 loading
  const [loading, setLoading] = useState(_cachedKbCollections === null)
  const [refreshing, setRefreshing] = useState(false)

  // force=true 走后端 refresh（跳过进程内缓存，重新扫库统计）
  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true)
    try {
      const data = await getKBCollections(force)
      _cachedKbCollections = data
      setCollections(data.collections)
      setDefaultIngestAlias(data.default_ingest_alias)
    } catch (e) {
      toast.error(`拉取库列表失败：${(e as Error).message}`)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  // 进页：有缓存则后台静默校验，无缓存则首屏拉取
  useEffect(() => {
    void load(false)
  }, [load])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
      </div>
    )
  }

  // 默认入库库排第一，其余保持后端顺序
  const ordered = [...collections].sort(
    (a, b) => Number(b.is_default) - Number(a.is_default),
  )
  const defaultAlias =
    defaultIngestAlias ||
    collections.find((c) => c.is_default)?.alias ||
    collections[0]?.alias ||
    ''

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            共 {ordered.length} 个知识库，点击任意一个可查看并管理其中的文档
          </p>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs text-muted-foreground"
            onClick={() => load(true)}
            disabled={refreshing}
          >
            <RefreshCw className={cn('h-3.5 w-3.5', refreshing && 'animate-spin')} />
            刷新
          </Button>
        </div>
        <ul className="space-y-2.5">
        {ordered.map((c) => (
          <li key={c.alias}>
            <button
              type="button"
              onClick={() => onOpen(c.alias)}
              className={cn(
                'group flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors',
                c.is_default
                  ? 'border-primary/50 bg-primary/5 hover:bg-primary/10'
                  : 'border-border hover:border-foreground/20 hover:bg-muted/40',
              )}
            >
              <span
                className={cn(
                  'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg',
                  c.is_default
                    ? 'bg-primary/15 text-primary'
                    : 'bg-muted text-muted-foreground',
                )}
              >
                <Database className="h-5 w-5" />
              </span>

              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="flex items-center gap-2">
                  <span className="text-sm font-semibold">{c.alias}</span>
                  {c.is_default && (
                    <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-medium text-primary">
                      默认入库
                    </span>
                  )}
                </span>
                <span className="truncate text-xs text-muted-foreground">
                  {c.model}
                  <span className="mx-1.5 text-border">·</span>
                  {c.collection}
                </span>
              </span>

              <span className="ml-auto flex shrink-0 items-center gap-2">
                <Stat icon={<FileText className="h-3.5 w-3.5" />} label="文档" value={c.doc_count} />
                <Stat icon={<Layers className="h-3.5 w-3.5" />} label="chunks" value={c.chunk_count} />
                <ChevronRight className="h-4 w-4 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
              </span>
            </button>
          </li>
        ))}
        </ul>
      </div>

      <IngestPanel
        collections={ordered}
        defaultAlias={defaultAlias}
        isAdmin={isAdmin}
        onIngested={() => load(true)}
        onGotoGolden={onGotoGolden}
      />
    </div>
  )
}

function Stat({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: number
}) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md bg-background/70 px-2 py-1 text-xs text-muted-foreground"
      title={label}
    >
      {icon}
      <span className="font-medium text-foreground">{value.toLocaleString()}</span>
    </span>
  )
}

// ── L2：单个库的文档管理 ─────────────────────────────────────────────────────

function LibraryView({
  alias,
  onBack,
  onOpenGolden,
  showGolden,
}: {
  alias: string
  onBack: () => void
  onOpenGolden?: (docId: string, label: string, alias: string) => void
  showGolden?: boolean
}) {
  const [documents, setDocuments] = useState<KBDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [clearDialogOpen, setClearDialogOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [genDocId, setGenDocId] = useState<string | null>(null)
  const [goldenLlm, setGoldenLlm] = useState('kimi-k2.5')
  const [goldenMaxQ, setGoldenMaxQ] = useState(3)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setDocuments(await listKBDocuments(alias))
    } catch (e) {
      console.error('[KB] 拉列表失败', e)
      toast.error(`拉取列表失败: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [alias])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleDelete = useCallback(
    async (docId: string) => {
      try {
        const resp = await deleteKBDocument(docId, alias)
        if (resp.deleted) {
          toast.success(`已删除，移除 ${resp.chunks_removed} chunks`)
        } else {
          toast.error('文档不存在')
        }
        await refresh()
      } catch (e) {
        toast.error(`删除失败：${(e as Error).message}`)
      }
    },
    [alias, refresh],
  )

  const handleDeleteMany = useCallback(
    async (docIds: string[]) => {
      let removed = 0
      let chunks = 0
      let failed = 0
      for (const id of docIds) {
        try {
          const resp = await deleteKBDocument(id, alias)
          if (resp.deleted) {
            removed++
            chunks += resp.chunks_removed
          }
        } catch {
          failed++
        }
      }
      if (failed > 0) toast.error(`批量删除：成功 ${removed} · 失败 ${failed}`)
      else toast.success(`已删除 ${removed} 个文档（移除 ${chunks} chunks）`)
      await refresh()
    },
    [alias, refresh],
  )

  const handleClearAll = useCallback(async () => {
    setClearing(true)
    try {
      const resp = await clearAllKBDocuments(alias)
      toast.success(
        `已清空：${resp.docs_removed} 个文档 / ${resp.chunks_removed} chunks / ${resp.files_removed} 个物理文件`,
      )
      setClearDialogOpen(false)
      await refresh()
    } catch (e) {
      toast.error(`清空失败：${(e as Error).message}`)
    } finally {
      setClearing(false)
    }
  }, [alias, refresh])

  const handleGenerate = useCallback(
    async (doc: KBDocument) => {
      setGenDocId(doc.doc_id)
      try {
        const r = await generateGolden(alias, doc.source, doc.doc_id, {
          goldenLlm,
          goldenMaxQ,
        })
        const cleared = r.removed_pending ? `（清旧待审 ${r.removed_pending}）` : ''
        toast.success(`已生成 ${r.generated} 条评估题候选${cleared}，去 Golden 管理审核`)
        await refresh()
      } catch (e) {
        toast.error(`生成失败：${(e as Error).message}`)
      } finally {
        setGenDocId(null)
      }
    },
    [alias, refresh, goldenLlm, goldenMaxQ],
  )

  const goldenLlmLabel = GOLDEN_LLM_LABELS[goldenLlm] ?? goldenLlm

  const totalChunks = documents.reduce((sum, d) => sum + d.chunks, 0)

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm">
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-1 rounded px-1 text-muted-foreground hover:bg-muted/50 hover:text-foreground"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          库列表
        </button>
        <span className="text-muted-foreground">/</span>
        <span className="font-medium text-foreground">{alias}</span>
      </div>

      <div className="rounded-lg border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
          <span className="text-sm font-medium">已入库文档 ({documents.length})</span>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {showGolden && (
              <GoldenGenControls
                includeNone={false}
                goldenLlm={goldenLlm}
                goldenMaxQ={goldenMaxQ}
                onGoldenLlmChange={setGoldenLlm}
                onGoldenMaxQChange={setGoldenMaxQ}
                disabled={clearing}
              />
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-xs text-muted-foreground hover:text-destructive"
              disabled={documents.length === 0 || clearing}
              onClick={() => setClearDialogOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              一键清空
            </Button>
          </div>
        </div>
        <DocumentList
          documents={documents}
          loading={loading}
          onDelete={handleDelete}
          onDeleteMany={handleDeleteMany}
          showGolden={showGolden}
          onGenerateGolden={showGolden ? handleGenerate : undefined}
          generatingDocId={genDocId}
          goldenGenPreview={
            showGolden ? { llmLabel: goldenLlmLabel, maxQ: goldenMaxQ } : undefined
          }
          onOpenGolden={
            showGolden ? (docId, label) => onOpenGolden?.(docId, label, alias) : undefined
          }
        />
      </div>

      <AlertDialog
        open={clearDialogOpen}
        onOpenChange={(o: boolean) => !clearing && setClearDialogOpen(o)}
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
              if (!clearing) handleClearAll()
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>清空库 {alias}？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除 <b>{alias}</b> 库的 <b>{documents.length}</b> 个文档（共{' '}
              <b>{totalChunks}</b> chunks），同时清空 <code>web_uploads/</code>{' '}
              下对应的物理文件。该操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearing}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleClearAll}
              disabled={clearing}
              autoFocus
            >
              {clearing ? '清空中...' : '清空'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
