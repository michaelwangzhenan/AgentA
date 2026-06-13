// DB 秀（/admin/db/*）类型，与后端 src/db_inspect.py 返回结构对齐。

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
  error?: string
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

export type SqliteTableRows = {
  db_key: string
  table: string
  total: number
  columns: string[]
  masked_columns: string[]
  rows: Record<string, unknown>[]
  error?: string
}
