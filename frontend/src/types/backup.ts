// 运行时数据备份（/admin/backup/*）类型，与后端 src/api/schemas/backup.py 对齐。

export type BackupSnapshot = {
  name: string
  timestamp: string
  created_at: string
  include_vectors: boolean | null
  file_count: number
  zip_bytes: number
  category_stats: Record<string, { files: number; bytes: number }>
}

export type BackupListResponse = {
  items: BackupSnapshot[]
}

export type RestoreResponse = {
  restored: number
  message: string
}
