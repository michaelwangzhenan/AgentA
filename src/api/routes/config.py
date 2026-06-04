"""Config 只读视图端点 —— 暴露当前 Agent / RAG / Memory / Rules / MCP / Security / Web / Log 配置摘要。

**严禁**返回任何 API key；只暴露 scalar / list / 简单 dict。
"""

from fastapi import APIRouter

import src.config as _cfg
from src.api.schemas.config import (
    ConfigResponse,
    LLMConfig,
    LogConfig,
    MCPConfig,
    MemoryConfig,
    RAGConfig,
    RulesConfig,
    SecurityConfig,
    WebConfig,
)

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    provider_cfg = _cfg.PROVIDER_CONFIGS.get(_cfg.ACTIVE_PROVIDER)
    return ConfigResponse(
        llm=LLMConfig(
            active_provider=_cfg.ACTIVE_PROVIDER,
            model=provider_cfg.model if provider_cfg else "",
            force_temperature=provider_cfg.force_temperature if provider_cfg else None,
            thinking_enabled=_cfg.THINKING_ENABLED,
            thinking_budget=_cfg.THINKING_BUDGET,
            available_providers=sorted(_cfg.PROVIDER_CONFIGS.keys()),
        ),
        rag=RAGConfig(
            top_k=_cfg.RAG_TOP_K,
            k_per_source=_cfg.RAG_K_PER_SOURCE,
            active_embeddings=_cfg.RAG_ACTIVE_EMBEDDINGS,
            default_embedding=_cfg.DEFAULT_EMBEDDING_ALIAS,
            reranker_enabled=_cfg.RERANKER_ENABLED,
            reranker_model=_cfg.RERANKER_MODEL,
            query_rewrite_enabled=_cfg.RAG_QUERY_REWRITE_ENABLED,
            ocr_fallback_enabled=_cfg.RAG_OCR_FALLBACK_ENABLED,
            chunk_size=_cfg.CHUNK_SIZE,
            chunk_overlap=_cfg.CHUNK_OVERLAP,
        ),
        memory=MemoryConfig(
            enabled=_cfg.USER_MEMORY_ENABLED,
            auto_extract=_cfg.USER_MEMORY_AUTO_EXTRACT,
            max_chars=_cfg.USER_MEMORY_MAX_CHARS,
        ),
        rules=RulesConfig(
            enabled=_cfg.USER_RULES_ENABLED,
            file=_cfg.USER_RULES_FILE,
            max_chars=_cfg.USER_RULES_MAX_CHARS,
        ),
        mcp=MCPConfig(
            enabled=_cfg.MCP_ENABLED,
            config_file=_cfg.MCP_CONFIG_FILE,
            connect_timeout_sec=_cfg.MCP_CONNECT_TIMEOUT_SEC,
            call_timeout_sec=_cfg.MCP_CALL_TIMEOUT_SEC,
        ),
        security=SecurityConfig(
            mode=_cfg.SECURITY_MODE,
            plan_permission_mode=_cfg.PLAN_PERMISSION_MODE,
        ),
        web=WebConfig(
            upload_dir=_cfg.WEB_UPLOAD_DIR,
            max_upload_mb=_cfg.WEB_MAX_UPLOAD_MB,
        ),
        log=LogConfig(
            level=_cfg.LOG_LEVEL,
        ),
    )
