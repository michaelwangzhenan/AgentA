"""Config 只读视图模型。

只暴露 scalar / list / 简单 dict；**严禁包含 API key**。
"""

from pydantic import BaseModel


class LLMConfig(BaseModel):
    active_provider: str
    model: str
    force_temperature: float | None
    thinking_enabled: bool
    thinking_budget: int
    available_providers: list[str]


class RAGConfig(BaseModel):
    top_k: int
    k_per_source: int
    active_embeddings: list[str]
    default_embedding: str
    reranker_enabled: bool
    reranker_model: str
    query_rewrite_enabled: bool
    ocr_fallback_enabled: bool
    chunk_size: int
    chunk_overlap: int


class MemoryConfig(BaseModel):
    enabled: bool
    auto_extract: bool
    max_chars: int


class RulesConfig(BaseModel):
    enabled: bool
    file: str
    max_chars: int


class MCPConfig(BaseModel):
    enabled: bool
    config_file: str
    connect_timeout_sec: int
    call_timeout_sec: int


class SecurityConfig(BaseModel):
    mode: str
    plan_permission_mode: bool


class WebConfig(BaseModel):
    upload_dir: str
    max_upload_mb: int


class LogConfig(BaseModel):
    level: str


class ConfigResponse(BaseModel):
    llm: LLMConfig
    rag: RAGConfig
    memory: MemoryConfig
    rules: RulesConfig
    mcp: MCPConfig
    security: SecurityConfig
    web: WebConfig
    log: LogConfig
