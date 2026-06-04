import { useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, FileText, Trash2 } from 'lucide-react'

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

export function DocumentList({ documents, loading, onDelete }: DocumentListProps) {
  const [deleteTarget, setDeleteTarget] = useState<KBDocument | null>(null)
  // 默认按"入库时间"倒序，跟后端 list_kb_documents 默认排序一致
  const [sortKey, setSortKey] = useState<SortKey>('ingested_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const sortedDocs = useMemo(
    // Array.prototype.sort 是稳定排序 (ES2019+)，同键值时保持原顺序
    () => [...documents].sort((a, b) => compareDocs(a, b, sortKey, sortDir)),
    [documents, sortKey, sortDir],
  )

  const handleSort = (col: SortColumn) => {
    if (col.key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(col.key)
      setSortDir(col.defaultDir)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    await onDelete(deleteTarget.doc_id)
    setDeleteTarget(null)
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

  return (
    <>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
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
            <th className="w-10 px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {sortedDocs.map((d) => (
            <tr
              key={d.doc_id}
              className="border-b border-border/50 hover:bg-muted/30"
            >
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

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(o: boolean) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent>
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
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
