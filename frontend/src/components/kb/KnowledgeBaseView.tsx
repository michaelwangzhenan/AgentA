import { useCallback, useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'

import {
  clearAllKBDocuments,
  deleteKBDocument,
  listKBDocuments,
  uploadKBFile,
} from '@/api/client'
import type { KBDocument } from '@/types/kb'
import { DropZone } from '@/components/kb/DropZone'
import { DocumentList } from '@/components/kb/DocumentList'
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
import { toast } from '@/lib/toast'

export function KnowledgeBaseView() {
  const [documents, setDocuments] = useState<KBDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState<string>('')
  const [elapsedSec, setElapsedSec] = useState(0)
  const [clearDialogOpen, setClearDialogOpen] = useState(false)
  const [clearing, setClearing] = useState(false)

  // 上传期间每秒刷新耗时，给用户"系统活着"的反馈（后端单文件 sync POST，无内部进度回传）
  useEffect(() => {
    if (!uploading) {
      setElapsedSec(0)
      return
    }
    const startedAt = Date.now()
    const id = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)
    return () => window.clearInterval(id)
  }, [uploading])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const docs = await listKBDocuments()
      setDocuments(docs)
    } catch (e) {
      console.error('[KB] 拉列表失败', e)
      toast.error(`拉取列表失败: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return
      setUploading(true)
      let okCount = 0
      let failCount = 0
      try {
        for (const file of files) {
          setUploadStatus(`处理中：${file.name} (${okCount + failCount + 1}/${files.length})`)
          try {
            const resp = await uploadKBFile(file)
            okCount++
            if (resp.chunks > 0) {
              toast.success(`已入库 ${file.name}：${resp.chunks} chunks`)
            } else {
              toast.info(`${file.name}：${resp.message || '已跳过'}`)
            }
          } catch (e) {
            failCount++
            toast.error(`${file.name}：${(e as Error).message}`)
          }
        }
      } finally {
        setUploading(false)
        setUploadStatus('')
        await refresh()
      }
    },
    [refresh],
  )

  const handleDelete = useCallback(
    async (docId: string) => {
      try {
        const resp = await deleteKBDocument(docId)
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
    [refresh],
  )

  const handleClearAll = useCallback(async () => {
    setClearing(true)
    try {
      const resp = await clearAllKBDocuments()
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
  }, [refresh])

  const totalChunks = documents.reduce((sum, d) => sum + d.chunks, 0)

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-base font-semibold tracking-tight">知识库</h1>
        <p className="text-xs text-muted-foreground">
          拖拽文件入库；Agent 通过 search_knowledge 工具自动检索
        </p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-4xl space-y-6">
          <DropZone onFiles={handleFiles} disabled={uploading} />

          {uploading && uploadStatus && (
            <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
              {uploadStatus}
              {elapsedSec > 0 && (
                <span className="ml-2 text-xs">· 已耗时 {elapsedSec}s</span>
              )}
            </div>
          )}

          <div className="rounded-lg border border-border bg-card">
            <div className="flex items-center justify-between border-b border-border px-3 py-2">
              <span className="text-sm font-medium">
                已入库文档 ({documents.length})
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 gap-1 text-xs text-muted-foreground hover:text-destructive"
                disabled={documents.length === 0 || uploading || clearing}
                onClick={() => setClearDialogOpen(true)}
              >
                <Trash2 className="h-3.5 w-3.5" />
                一键清空
              </Button>
            </div>
            <DocumentList
              documents={documents}
              loading={loading}
              onDelete={handleDelete}
            />
          </div>
        </div>
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
            <AlertDialogTitle>清空整个知识库？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除 <b>{documents.length}</b> 个文档（共 <b>{totalChunks}</b>{' '}
              chunks），同时清空 <code>web_uploads/</code>{' '}
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
