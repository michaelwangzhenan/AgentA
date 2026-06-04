// 跟后端 src/api/schemas/config.py 对齐

export type LLMConfig = {
  active_provider: string
  model: string
  force_temperature: number | null
  thinking_enabled: boolean
  thinking_budget: number
  available_providers: string[]
}

export type RAGConfig = {
  top_k: number
  k_per_source: number
  active_embeddings: string[]
  default_embedding: string
  reranker_enabled: boolean
  reranker_model: string
  query_rewrite_enabled: boolean
  ocr_fallback_enabled: boolean
  chunk_size: number
  chunk_overlap: number
}

export type MemoryConfig = {
  enabled: boolean
  auto_extract: boolean
  max_chars: number
}

export type RulesConfig = {
  enabled: boolean
  file: string
  max_chars: number
}

export type MCPConfig = {
  enabled: boolean
  config_file: string
  connect_timeout_sec: number
  call_timeout_sec: number
}

export type SecurityConfig = {
  mode: string
  plan_permission_mode: boolean
}

export type WebConfig = {
  upload_dir: string
  max_upload_mb: number
}

export type LogConfig = {
  level: string
}

export type AppConfig = {
  llm: LLMConfig
  rag: RAGConfig
  memory: MemoryConfig
  rules: RulesConfig
  mcp: MCPConfig
  security: SecurityConfig
  web: WebConfig
  log: LogConfig
}
