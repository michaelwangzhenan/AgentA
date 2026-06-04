import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2 } from 'lucide-react'

import {
  deleteKBDocument,
  listKBDocuments,
  uploadKBFile,
} from '@/api/client'
import type { KBDocument } from '@/types/kb'
import { DropZone } from '@/components/kb/DropZone'
import { DocumentList } from '@/components/kb/DocumentList'

type Toast = {
  id: number
  kind: 'success' | 'error'
  message: string
}

export function KnowledgeBaseView() {
  const [documents, setDocuments] = useState<KBDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState<string>('')
  const [toasts, setToasts] = useState<Toast[]>([])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const docs = await listKBDocuments()
      setDocuments(docs)
    } catch (e) {
      console.error('[KB] 拉列表失败', e)
      pushToast('error', `拉取列表失败: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const pushToast = (kind: Toast['kind'], message: string) => {
    const id = Date.now() + Math.random()
    setToasts((prev) => [...prev, { id, kind, message }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4000)
  }

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
            pushToast(
              'success',
              resp.chunks > 0
                ? `已入库 ${file.name}：${resp.chunks} chunks`
                : `${file.name}：${resp.message || '已跳过'}`,
            )
          } catch (e) {
            failCount++
            pushToast('error', `${file.name}：${(e as Error).message}`)
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
          pushToast('success', `已删除，移除 ${resp.chunks_removed} chunks`)
        } else {
          pushToast('error', '文档不存在')
        }
        await refresh()
      } catch (e) {
        pushToast('error', `删除失败：${(e as Error).message}`)
      }
    },
    [refresh],
  )

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
            </div>
          )}

          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border px-3 py-2 text-sm font-medium">
              已入库文档 ({documents.length})
            </div>
            <DocumentList
              documents={documents}
              loading={loading}
              onDelete={handleDelete}
            />
          </div>
        </div>
      </div>

      {/* toast 区（右下角） */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={
              'pointer-events-auto flex items-start gap-2 rounded-md border px-3 py-2 text-sm shadow-md ' +
              (t.kind === 'success'
                ? 'border-green-200 bg-green-50 text-green-900 dark:border-green-900 dark:bg-green-950 dark:text-green-100'
                : 'border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100')
            }
          >
            {t.kind === 'success' ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
