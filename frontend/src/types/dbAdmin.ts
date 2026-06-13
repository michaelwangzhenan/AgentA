// 数据库（/admin/db/*）类型，与后端 src/db_inspect.py 返回结构对齐。

export type Metadata = Record<string, unknown> | null

// ── Chroma ──────────────────────────────────────────────────────────────
export type ChromaCollection = {
  name: string
  space?: string | null
  count?: number | null
  dim?: number | null
  error?: string
}

export type ChromaCollections = {
  root: string
  collections: ChromaCollection[]
  error?: string
}

export type ChromaItem = {
  id: string
  preview: string
  doc_len: number
  metadata: Metadata
}

export type ChromaItemsPage = {
  name: string
  total: number
  items: ChromaItem[]
  truncated?: boolean
  error?: string
}

// Chroma 条目 / BM25 文档块 通用的过滤排序查询参数
export type ItemsQuery = {
  limit?: number
  offset?: number
  filenameQ?: string
  bodyQ?: string
  tsFrom?: number
  tsTo?: number
  sortBy?: 'filename' | 'ingested_at'
  desc?: boolean
}

export type ChromaItemDetail = {
  id: string
  document: string
  metadata: Metadata
}

// ── BM25 ────────────────────────────────────────────────────────────────
export type Bm25Index = {
  file: string
  collection: string
  bytes: number
  docs?: number
  k1?: number
  b?: number
  error?: string
}

export type Bm25Indexes = {
  dir: string
  indexes: Bm25Index[]
}

export type Bm25Doc = {
  id: string
  preview: string
  tokens: number
  metadata: Metadata
}

export type Bm25DocsPage = {
  collection: string
  total: number
  items: Bm25Doc[]
}

export type Bm25DocDetail = {
  id: string
  document: string
  metadata: Metadata
  tokens: number
}

// ── SQLite ──────────────────────────────────────────────────────────────
export type SqliteTable = {
  name: string
  rows: number
}

export type SqliteDatabase = {
  key: string
  label: string
  file: string
  path: string
  exists: boolean
  tables: SqliteTable[]
  error?: string
}

export type SqliteDatabases = {
  databases: SqliteDatabase[]
}

// ── 维护（清理）──────────────────────────────────────────────────────────
export type MaintCount = { db: string; table: string; count: number }

export type PruneResult = {
  days?: number
  cutoff?: number
  items: MaintCount[]
  total: number
  executed?: boolean
  error?: string
}

export type PurgePreviewTable = {
  db: string
  table: string
  total: number
  truncated: boolean
  columns: string[]
  rows: Record<string, unknown>[]
  child: string | null
}

export type PurgePreview = {
  user_id: number
  cap: number
  tables: PurgePreviewTable[]
}

export type PurgeSelection = { db: string; table: string; all: boolean; rowids: number[] }

export type PurgeResult = {
  user_id?: number
  items: { db: string; table: string; deleted: number }[]
  total: number
  executed?: boolean
}

export type VacuumResult = {
  results: { db: string; ok: boolean; freed_bytes?: number; size?: number; error?: string }[]
}

export type SqliteTableRows = {
  db_key: string
  table: string
  total: number
  columns: string[]
  masked_columns: string[]
  rows: Record<string, unknown>[]
  error?: string
}
