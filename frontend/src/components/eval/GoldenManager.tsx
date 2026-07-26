import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Check, Download, Pencil, Plus, RotateCcw, Trash2, X } from 'lucide-react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { useAuth } from '@/lib/auth'
import { allSettledAreWritePermissionDenied, isWritePermissionDenied, writeDeniedMessage } from '@/lib/permissions'
import { useUrlState } from '@/routes/useUrlState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  createGolden,
  deleteGolden,
  exportGolden,
  getGolden,
  importGolden,
  updateGolden,
} from '@/api/client'
import type { GoldenItem, GoldenList, GoldenStatus } from '@/types/eval'

// 跨页跳转：知识库 L2 点某文档候选数 → 带 docFilter 过来只看该文档
export type GoldenDocFilter = { docId: string; label: string; fromAlias?: string }

// 下拉含来源类型 +「问题」；选「问题」时旁侧输入框改筛 query，否则筛来源文件
const SOURCE_FILTERS: { value: string; label: string }[] = [
  { value: '', label: '全部来源' },
  { value: 'ai', label: 'AI 生成' },
  { value: 'manual', label: '手工' },
  { value: 'query', label: '问题' },
]
const FILTER_QUERY = 'query'

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待审核' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已拒绝' },
]

const STATUS_BADGE: Record<GoldenStatus, string> = {
  pending: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  approved: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  rejected: 'bg-muted text-muted-foreground line-through',
}
const STATUS_TEXT: Record<GoldenStatus, string> = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已拒绝',
}

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const
const DEFAULT_PAGE_SIZE = 10

export function GoldenManager({
  docFilter,
  onClearDocFilter,
  onBackToKb,
}: {
  docFilter?: GoldenDocFilter
  onClearDocFilter?: () => void
  onBackToKb?: () => void
} = {}) {
  const { canWrite, user } = useAuth()
  const canWriteQuality = canWrite('quality')
  const qualityWriteTip = writeDeniedMessage('quality', user?.role)

  const url = useUrlState()
  const status = url.get('status')
  const source = url.get('source')
  const textQApplied = url.get('q')
  const [textQ, setTextQ] = useState(textQApplied)
  const pageNum = Math.max(1, url.getInt('page', 1))
  const rawSize = url.getInt('size', DEFAULT_PAGE_SIZE)
  const pageSize = rawSize > 0 ? rawSize : DEFAULT_PAGE_SIZE
  const offset = (pageNum - 1) * pageSize
  const setOffset = (n: number) => {
    const p = Math.floor(n / pageSize) + 1
    url.patch({ page: p <= 1 ? null : p })
  }
  const setStatus = (v: string) => url.patch({ status: v || null, page: null })
  const setSource = (v: string) => url.patch({ source: v || null, page: null })
  const setPageSize = (n: number) =>
    url.patch({ size: n === DEFAULT_PAGE_SIZE ? null : n, page: null })

  const [data, setData] = useState<GoldenList | null>(null)
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newQuery, setNewQuery] = useState('')
  const [newKeywords, setNewKeywords] = useState('')
  const [newSource, setNewSource] = useState('')
  const [newSourceExact, setNewSourceExact] = useState('')
  const [newType, setNewType] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  // 删除确认：单条或批量都走这里；null 表示对话框关闭
  const [deleteIds, setDeleteIds] = useState<number[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [editItem, setEditItem] = useState<GoldenItem | null>(null)

  // 跨页跳转传入的"按文档过滤"用本地 state 持有：页内 ✕ 可立即清除，不依赖父组件回传时机；
  // 父组件传入新的 docFilter（再次跳转）时同步刷新。
  const [localDoc, setLocalDoc] = useState<GoldenDocFilter | undefined>(docFilter)
  useEffect(() => {
    setLocalDoc(docFilter)
    url.patch({ page: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docFilter])
  const docId = localDoc?.docId
  const isQueryFilter = source === FILTER_QUERY
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getGolden({
        status: status || undefined,
        source: !isQueryFilter && source ? source : undefined,
        doc_id: docId || undefined,
        source_contains: !isQueryFilter && textQApplied ? textQApplied : undefined,
        query_contains: isQueryFilter && textQApplied ? textQApplied : undefined,
        limit: pageSize,
        offset,
      })
      // 删除后当前页可能已无数据，收紧 offset 再拉一次
      if (result.total > 0 && offset >= result.total) {
        const next = Math.floor((result.total - 1) / pageSize) * pageSize
        setOffset(next)
        return
      }
      setData(result)
      setSelected(new Set())
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [status, source, docId, textQApplied, isQueryFilter, offset, pageSize])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 旁侧输入框防抖（300ms）：输入停顿后才触发查询，同时回第一页
  useEffect(() => {
    const t = setTimeout(() => {
      url.patch({ q: textQ.trim() || null, page: null })
    }, 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [textQ])

  const items = data?.items ?? []
  const allSelected = items.length > 0 && items.every((it) => selected.has(it.id))

  const toggleOne = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(items.map((it) => it.id)))
  }

  const setItemStatus = async (it: GoldenItem, s: GoldenStatus) => {
    try {
      await updateGolden(it.id, { status: s })
      toast.success(`已标记为${STATUS_TEXT[s]}`)
      void refresh()
    } catch (e) {
      if (!isWritePermissionDenied(e)) toast.error((e as Error).message)
    }
  }
  const batchSetStatus = async (s: GoldenStatus) => {
    const ids = [...selected]
    if (!ids.length) return
    setBusy(true)
    try {
      const rs = await Promise.allSettled(ids.map((id) => updateGolden(id, { status: s })))
      const failed = rs.filter((r) => r.status === 'rejected').length
      if (failed === 0) toast.success(`${ids.length} 条已标记为${STATUS_TEXT[s]}`)
      else if (!allSettledAreWritePermissionDenied(rs)) {
        toast.error(`${ids.length - failed} 条已标记为${STATUS_TEXT[s]}，${failed} 条失败`)
      }
      void refresh()
    } finally {
      setBusy(false)
    }
  }

  const confirmDelete = async () => {
    const ids = deleteIds ?? []
    if (!ids.length) return
    setBusy(true)
    try {
      const rs = await Promise.allSettled(ids.map((id) => deleteGolden(id)))
      const failed = rs.filter((r) => r.status === 'rejected').length
      if (failed === 0) toast.success(`已删除 ${ids.length} 条`)
      else if (!allSettledAreWritePermissionDenied(rs)) {
        toast.error(`已删除 ${ids.length - failed} 条，${failed} 条失败`)
      }
      void refresh()
    } finally {
      setBusy(false)
      setDeleteIds(null)
    }
  }

  const submitCreate = async () => {
    const q = newQuery.trim()
    if (!q) return
    try {
      await createGolden({
        query: q,
        expected_keywords: newKeywords.split(',').map((s) => s.trim()).filter(Boolean),
        expected_source: newSourceExact.trim(),
        expected_source_contains: newSource.trim(),
        type: newType.trim(),
        note: '',
      })
      setNewQuery('')
      setNewKeywords('')
      setNewSource('')
      setNewSourceExact('')
      setNewType('')
      setShowCreate(false)
      void refresh()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const doImport = async () => {
    try {
      const r = await importGolden()
      toast.success(`从 ${r.source} 导入 ${r.added} 条`)
      void refresh()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const doExport = async () => {
    try {
      if (await exportGolden()) toast.success('已导出 golden')
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const submitEdit = async () => {
    if (!editItem) return
    try {
      await updateGolden(editItem.id, {
        query: editItem.query.trim(),
        expected_keywords: editItem.expected_keywords,
        expected_source: editItem.expected_source.trim(),
        expected_source_contains: editItem.expected_source_contains.trim(),
        type: editItem.type.trim(),
        note: editItem.note,
      })
      toast.success('已保存')
      setEditItem(null)
      void refresh()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const counts = data?.counts ?? {}

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => {
                setOffset(0)
                setStatus(f.value)
              }}
              className={cn(
                'rounded px-2.5 py-1 text-xs transition-colors',
                status === f.value
                  ? 'bg-background font-medium text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {f.label}
              {f.value && counts[f.value] != null ? ` (${counts[f.value]})` : ''}
            </button>
          ))}
        </div>
        <select
          value={source}
          onChange={(e) => {
            setTextQ('')
            url.patch({ q: null })
            setSource(e.target.value)
          }}
          className="rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
        >
          {SOURCE_FILTERS.map((f) => (
            <option key={f.value || '_all'} value={f.value}>{f.label}</option>
          ))}
        </select>
        <input
          type="text"
          value={textQ}
          onChange={(e) => setTextQ(e.target.value)}
          placeholder={isQueryFilter ? '按问题筛选…' : '按来源文件筛选…'}
          className="w-44 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
        />
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" className="gap-1.5" onClick={doExport}>
            <Download className="h-3.5 w-3.5" />
            导出 json
          </Button>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={doImport} disabled={!canWriteQuality} title={canWriteQuality ? undefined : qualityWriteTip}>
            <RotateCcw className="h-3.5 w-3.5" />
            从 golden.json 导入
          </Button>
          <Button size="sm" className="gap-1.5" onClick={() => setShowCreate((v) => !v)} disabled={!canWriteQuality} title={canWriteQuality ? undefined : qualityWriteTip}>
            <Plus className="h-3.5 w-3.5" />
            新增
          </Button>
        </div>
      </div>

      {localDoc && (
        <div className="flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs">
          <span className="text-muted-foreground">仅看文档：</span>
          <span className="font-medium text-foreground" title={localDoc.docId}>{localDoc.label}</span>
          <div className="ml-auto flex items-center gap-1.5">
            {onBackToKb && (
              <button
                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-primary hover:bg-primary/10"
                title="返回知识库该文档列表"
                onClick={() => onBackToKb()}
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                返回知识库{localDoc.fromAlias ? `（${localDoc.fromAlias}）` : ''}
              </button>
            )}
            <button
              className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="清除筛选"
              onClick={() => {
                setOffset(0)
                setLocalDoc(undefined)
                onClearDocFilter?.()
              }}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}

      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
          <span className="text-muted-foreground">已选 {selected.size} 条</span>
          <div className="ml-auto flex items-center gap-2">
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy || !canWriteQuality} title={canWriteQuality ? undefined : qualityWriteTip} onClick={() => batchSetStatus('approved')}>
              <Check className="h-3.5 w-3.5 text-emerald-600" />
              批量通过
            </Button>
            <Button variant="outline" size="sm" className="gap-1.5" disabled={busy || !canWriteQuality} title={canWriteQuality ? undefined : qualityWriteTip} onClick={() => batchSetStatus('rejected')}>
              <X className="h-3.5 w-3.5 text-amber-600" />
              批量拒绝
            </Button>
            <Button variant="outline" size="sm" className="gap-1.5 text-destructive" disabled={busy || !canWriteQuality} title={canWriteQuality ? undefined : qualityWriteTip} onClick={() => setDeleteIds([...selected])}>
              <Trash2 className="h-3.5 w-3.5" />
              批量删除
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setSelected(new Set())}>
              清除选择
            </Button>
          </div>
        </div>
      )}

      {showCreate && (
        <div className="space-y-2 rounded-lg border border-border p-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">问题 query（必填）</label>
            <Input placeholder="评估用的提问" value={newQuery} onChange={(e) => setNewQuery(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">期望关键词（逗号分隔）</label>
            <Input placeholder="如：RAG, 检索, 向量" value={newKeywords} onChange={(e) => setNewKeywords(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">期望来源片段（子串匹配，可选）</label>
            <Input placeholder="命中来源含此片段即算对，如文件名" value={newSource} onChange={(e) => setNewSource(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">期望来源（精确匹配，可选）</label>
            <Input placeholder="命中来源需完全等于此值" value={newSourceExact} onChange={(e) => setNewSourceExact(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">分类 type（可选）</label>
            <Input placeholder="人工标签，如 baseline / hyde" value={newType} onChange={(e) => setNewType(e.target.value)} />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setShowCreate(false)}>
              取消
            </Button>
            <Button size="sm" onClick={submitCreate} disabled={!canWriteQuality || !newQuery.trim()} title={canWriteQuality ? undefined : qualityWriteTip}>
              保存
            </Button>
          </div>
        </div>
      )}

      {data && !loading && (
        <Pager
          total={data.total}
          offset={offset}
          pageSize={pageSize}
          onOffset={setOffset}
          onPageSize={(n) => {
            setPageSize(n)
            setOffset(0)
          }}
          className="mb-3"
        />
      )}

      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
              <th className="w-10 px-3 py-2 text-center font-medium">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 cursor-pointer align-middle accent-primary"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label="全选"
                />
              </th>
              <th className="px-3 py-2 font-medium">问题</th>
              <th className="px-3 py-2 font-medium">期望关键词</th>
              <th className="px-3 py-2 font-medium">来源</th>
              <th className="px-3 py-2 text-center font-medium whitespace-nowrap">状态</th>
              <th className="px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {data && data.items.length > 0 ? (
              data.items.map((it) => (
                <tr
                  key={it.id}
                  className={cn(
                    'border-b border-border last:border-0 align-top',
                    selected.has(it.id) && 'bg-accent/40',
                  )}
                >
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 cursor-pointer align-middle accent-primary"
                      checked={selected.has(it.id)}
                      onChange={() => toggleOne(it.id)}
                      aria-label="选择此条"
                    />
                  </td>
                  <td className="px-3 py-2 max-w-65">
                    {it.query}
                    {it.type ? (
                      <span className="ml-1.5 rounded bg-muted px-1 text-[10px] text-muted-foreground">
                        {it.type}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {it.expected_keywords.join(', ') || '—'}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {it.source === 'ai' ? 'AI 生成' : '手工'}
                    {it.expected_source ? ` · =${it.expected_source}` : ''}
                    {it.expected_source_contains ? ` · ~${it.expected_source_contains}` : ''}
                  </td>
                  <td className="px-3 py-2 text-center whitespace-nowrap">
                    <span className={cn('inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[11px]', STATUS_BADGE[it.status])}>
                      {STATUS_TEXT[it.status]}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40 disabled:pointer-events-none"
                        title={canWriteQuality ? '编辑' : qualityWriteTip}
                        disabled={!canWriteQuality}
                        onClick={() => setEditItem(it)}
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      {it.status !== 'approved' && (
                        <button
                          className="rounded p-1 text-emerald-600 hover:bg-accent disabled:opacity-40 disabled:pointer-events-none"
                          title={canWriteQuality ? '通过' : qualityWriteTip}
                          disabled={!canWriteQuality}
                          onClick={() => setItemStatus(it, 'approved')}
                        >
                          <Check className="h-4 w-4" />
                        </button>
                      )}
                      {it.status !== 'rejected' && (
                        <button
                          className="rounded p-1 text-amber-600 hover:bg-accent disabled:opacity-40 disabled:pointer-events-none"
                          title={canWriteQuality ? '拒绝' : qualityWriteTip}
                          disabled={!canWriteQuality}
                          onClick={() => setItemStatus(it, 'rejected')}
                        >
                          <X className="h-4 w-4" />
                        </button>
                      )}
                      <button
                        className="rounded p-1 text-destructive hover:bg-accent disabled:opacity-40 disabled:pointer-events-none"
                        title={canWriteQuality ? '删除' : qualityWriteTip}
                        disabled={!canWriteQuality}
                        onClick={() => setDeleteIds([it.id])}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-sm text-muted-foreground">
                  {loading ? '加载中…' : '暂无 golden（可点「从 golden.json 导入」或「新增」）'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted-foreground">
        golden 用于 RAG 检索评估的标准基准。入库新资料时会自动生成「待审核」候选；评估脚本默认只用「已通过」的。
      </p>

      {editItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setEditItem(null)}>
          <div className="w-full max-w-lg space-y-2 rounded-lg border border-border bg-background p-4 shadow-lg" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold">编辑 golden #{editItem.id}</h3>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">问题 query</label>
              <Input placeholder="评估用的提问" value={editItem.query} onChange={(e) => setEditItem({ ...editItem, query: e.target.value })} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">期望关键词（逗号分隔）</label>
              <Input placeholder="如：RAG, 检索, 向量" value={editItem.expected_keywords.join(', ')} onChange={(e) => setEditItem({ ...editItem, expected_keywords: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">期望来源片段（子串匹配）</label>
              <Input placeholder="命中来源含此片段即算对，如文件名" value={editItem.expected_source_contains} onChange={(e) => setEditItem({ ...editItem, expected_source_contains: e.target.value })} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">期望来源（精确匹配）</label>
              <Input placeholder="命中来源需完全等于此值（可选）" value={editItem.expected_source} onChange={(e) => setEditItem({ ...editItem, expected_source: e.target.value })} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">分类 type</label>
              <Input placeholder="人工标签，如 baseline / hyde（可选）" value={editItem.type} onChange={(e) => setEditItem({ ...editItem, type: e.target.value })} />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" size="sm" onClick={() => setEditItem(null)}>取消</Button>
              <Button size="sm" onClick={submitEdit} disabled={!canWriteQuality || !editItem.query.trim()} title={canWriteQuality ? undefined : qualityWriteTip}>保存</Button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteIds !== null}
        onOpenChange={(o) => !o && !busy && setDeleteIds(null)}
        title="删除 golden？"
        description={`即将删除 ${deleteIds?.length ?? 0} 条 golden（不可恢复）。`}
        loading={busy && deleteIds !== null}
        confirmLabel="删除"
        onConfirm={confirmDelete}
      />
    </div>
  )
}

// 分页导航栏（对齐 DatabaseView SqliteRows）
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
