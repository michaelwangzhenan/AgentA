// Knowledge Base 类型 —— 对齐后端 src/api/schemas/kb.py

export type KBDocument = {
  doc_id: string
  filename: string
  source: string
  ext: string
  lang: string
  mtime: number
  chunks: number
  total_chars: number
}

export type KBDocumentListResponse = {
  documents: KBDocument[]
}

export type KBUploadResponse = {
  doc_id: string
  filename: string
  chunks: number
  skipped_unchanged: boolean
  message: string
}

export type KBDeleteResponse = {
  deleted: boolean
  chunks_removed: number
}
