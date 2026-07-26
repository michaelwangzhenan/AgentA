import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Download,
  HardDriveDownload,
  Loader2,
  RotateCcw,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import { toast } from 'sonner'

import { ResourcePage } from '@/components/resources/ResourcePage'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  backupDownloadUrl,
  createBackup,
  deleteBackup,
  listBackups,
  restoreBackup,
} from '@/api/client'
import type { BackupSnapshot } from '@/types/backup'
import { useWriteScope } from '@/lib/permissions'

function fmtSize(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  for (const u of units) {
    if (v < 1024 || u === 'GB') return `${v.toFixed(1)}${u}`
    v /= 1024
  }
  return `${n}B`
}

function fmtTimestamp(ts: string): string {
  // 形如 20260613-120000 → 2026-06-13 12:00:00
  const m = ts.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/)
  if (!m) return ts
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`
}

// 可备份类别（key 与后端 build_plan 一致）；含明文密钥 / 体积大的给提示
const BACKUP_CATEGORIES: { key: string; label: string; note?: string }[] = [
  { key: 'A', label: '敏感配置', note: '含明文密钥（.env / api_keys）' },
  { key: 'B', label: '运行期数据库', note: '会话 / 记忆 / 用量 / golden 等 SQLite' },
  { key: 'C', label: '向量库 / 索引', note: '体积大，约 100MB' },
  { key: 'E', label: '黄金集', note: 'rag_eval golden.json' },
  { key: 'F', label: '评估报告', note: 'tools/reports/' },
  { key: 'K', label: '编辑器配置', note: '.vscode / *.code-workspace' },
]

export function BackupView() {
  const { allowed: canWriteBackup, tip: backupTip } = useWriteScope('backup')
  const [snapshots, setSnapshots] = useState<BackupSnapshot[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  // 默认全选；用户可逐项取消
  const [cats, setCats] = useState<Set<string>>(() => new Set(BACKUP_CATEGORIES.map((c) => c.key)))
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listBackups()
      setSnapshots(res.items)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载备份列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  function toggleCat(key: string) {
    if (!canWriteBackup) return
    setCats((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  async function handleCreate() {
    setCreating(true)
    try {
      const ordered = BACKUP_CATEGORIES.map((c) => c.key).filter((k) => cats.has(k))
      const snap = await createBackup(ordered)
      toast.success(`已生成备份：${snap.file_count} 个文件，${fmtSize(snap.zip_bytes)}`)
      await refresh()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '生成备份失败')
    } finally {
      setCreating(false)
      setConfirmOpen(false)
    }
  }

  async function handleDelete(name: string) {
    setDeleting(true)
    try {
      await deleteBackup(name)
      toast.success('已删除备份')
      await refresh()
      setDeleteTarget(null)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  async function handleRestore(file: File) {
    setRestoring(true)
    try {
      const res = await restoreBackup(file)
      toast.success(res.message, { duration: 8000 })
      setPendingFile(null)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '还原失败')
    } finally {
      setRestoring(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  return (
    <ResourcePage
      title="备份与恢复"
      subtitle="管理运行时数据备份（配置 / 数据库 / 向量库 / 报告 / 编辑器配置）"
    >
      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto pb-6">
        {/* 含明文密钥警示 */}
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>备份含明文密钥（.env / api_keys.json），请妥善保管，勿上传公共网盘。</span>
        </div>

        {/* 生成备份 */}
        <section className="space-y-3 rounded-lg border border-border p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <HardDriveDownload className="h-4 w-4" /> 生成备份
          </h3>
          <p className="text-xs text-muted-foreground">勾选要备份的类别（默认全选）：</p>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {BACKUP_CATEGORIES.map((c) => (
              <label
                key={c.key}
                className="flex cursor-pointer items-start gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-muted/50"
              >
                <input
                  type="checkbox"
                  checked={cats.has(c.key)}
                  onChange={() => toggleCat(c.key)}
                  disabled={!canWriteBackup}
                  className="mt-0.5 h-4 w-4 accent-primary"
                />
                <span>
                  {c.label}
                  {c.note && <span className="ml-1 text-xs text-muted-foreground">（{c.note}）</span>}
                </span>
              </label>
            ))}
          </div>
          <Button onClick={() => setConfirmOpen(true)} disabled={!canWriteBackup || creating || cats.size === 0} title={canWriteBackup ? undefined : backupTip}>
            {creating ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
            生成备份
          </Button>
        </section>

        {/* 快照列表 */}
        <section className="space-y-3 rounded-lg border border-border p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">备份快照</h3>
            <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={loading}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : '刷新'}
            </Button>
          </div>
          {snapshots.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无备份。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="py-2 pr-3 font-medium">时间</th>
                    <th className="py-2 pr-3 font-medium">文件数</th>
                    <th className="py-2 pr-3 font-medium">大小</th>
                    <th className="py-2 pr-3 font-medium">向量库</th>
                    <th className="py-2 pr-3 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshots.map((s) => (
                    <tr key={s.name} className="border-b border-border/50">
                      <td className="py-2 pr-3">{fmtTimestamp(s.timestamp)}</td>
                      <td className="py-2 pr-3">{s.file_count}</td>
                      <td className="py-2 pr-3">{fmtSize(s.zip_bytes)}</td>
                      <td className="py-2 pr-3">{s.include_vectors ? '含' : '不含'}</td>
                      <td className="py-2 pr-3">
                        <div className="flex items-center gap-1">
                          <a
                            href={backupDownloadUrl(s.name)}
                            download
                            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-muted"
                          >
                            <Download className="h-3.5 w-3.5" /> 下载
                          </a>
                          <button
                            type="button"
                            onClick={() => setDeleteTarget(s.name)}
                            disabled={!canWriteBackup}
                            title={canWriteBackup ? undefined : backupTip}
                            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-40"
                          >
                            <Trash2 className="h-3.5 w-3.5" /> 删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 还原 */}
        <section className="space-y-3 rounded-lg border border-destructive/40 p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-destructive">
            <RotateCcw className="h-4 w-4" /> 从备份还原
          </h3>
          <p className="text-sm text-muted-foreground">
            上传备份 zip 会<strong className="text-destructive">覆盖</strong>服务器上的
            .env / 数据库等文件。还原后建议重启后端以加载新数据。
          </p>
          <div className="flex items-center gap-3">
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              disabled={!canWriteBackup || restoring}
              title={canWriteBackup ? undefined : backupTip}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) setPendingFile(f)
              }}
              className="text-sm file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-muted file:px-3 file:py-1.5 file:text-sm"
            />
            {restoring ? (
              <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> 还原中…
              </span>
            ) : null}
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={
          <span className="inline-flex items-center gap-2">
            <HardDriveDownload className="h-4 w-4" /> 确认生成备份
          </span>
        }
        description={
          <>
            将备份以下类别：
            {BACKUP_CATEGORIES.filter((c) => cats.has(c.key)).map((c) => c.label).join(' / ')}。
            {cats.has('A') && '（含明文密钥，请妥善保管）'}
          </>
        }
        loading={creating}
        confirmLabel="开始备份"
        destructive={false}
        onConfirm={handleCreate}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="删除备份"
        description={deleteTarget ? `确定删除 ${deleteTarget}？此操作不可恢复。` : ''}
        loading={deleting}
        confirmLabel="删除"
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget)
        }}
      />

      <ConfirmDialog
        open={pendingFile !== null}
        onOpenChange={(o) => !o && setPendingFile(null)}
        title={
          <span className="inline-flex items-center gap-2">
            <UploadCloud className="h-4 w-4" /> 确认还原
          </span>
        }
        description={
          pendingFile ? (
            <>
              即将用 <strong>{pendingFile.name}</strong> 覆盖服务器现有的 .env / 数据库 /
              向量库等文件，且不可撤销。还原后请重启后端。确定继续？
            </>
          ) : (
            ''
          )
        }
        loading={restoring}
        confirmLabel="确认还原"
        onConfirm={async () => {
          if (pendingFile) await handleRestore(pendingFile)
        }}
      />
    </ResourcePage>
  )
}
