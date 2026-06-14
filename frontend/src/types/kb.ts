// Knowledge Base 类型 —— 对齐后端 src/api/schemas/kb.py

export type KBDocument = {
  doc_id: string
  filename: string
  source: string
  ext: string
  lang: string
  mtime: number       // 文件修改时间 (unix timestamp)
  ingested_at: number // 入库时间 (unix timestamp)；老数据为 0
  chunks: number
  total_chars: number
}

export type KBDocumentListResponse = {
  documents: KBDocument[]
}

export type KBCollection = {
  alias: string      // en / zh / m3
  model: string      // 模型名称，如 BAAI/bge-m3
  collection: string // Chroma collection 名，如 kb_m3
  doc_count: number
  chunk_count: number
  is_default: boolean // 是否为 .env 默认入库库
}

export type KBCollectionListResponse = {
  collections: KBCollection[]
}

// 入库进度阶段：解析 / 切分 / 嵌入
export type IngestPhase = 'parse' | 'split' | 'embed'

export type IngestProgress = {
  phase: IngestPhase
  done: number // embed 阶段为已写入块数；parse 阶段为 0
  total: number // embed/split 阶段为总块数；parse 阶段为 0
}

// 单文件入库最终结果（SSE done 事件）
export type IngestResult = {
  doc_id: string
  filename: string
  chunks: number
  skipped_unchanged: boolean
  status: string // ingested / skipped_unchanged / empty
  message: string
}

export type KBDeleteResponse = {
  deleted: boolean
  chunks_removed: number
}

export type KBClearAllResponse = {
  docs_removed: number
  chunks_removed: number
  files_removed: number
}
