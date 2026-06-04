import { useState } from 'react'
import { FileText, Trash2 } from 'lucide-react'

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
import type { KBDocument } from '@/types/kb'

export type DocumentListProps = {
  documents: KBDocument[]
  loading: boolean
  onDelete: (docId: string) => Promise<void> | void
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
            <th className="px-3 py-2 font-medium">文件名</th>
            <th className="px-3 py-2 font-medium">语言</th>
            <th className="px-3 py-2 font-medium text-right">chunks</th>
            <th className="px-3 py-2 font-medium text-right">字符</th>
            <th className="px-3 py-2 font-medium">修改时间</th>
            <th className="px-3 py-2 w-10" />
          </tr>
        </thead>
        <tbody>
          {documents.map((d) => (
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
