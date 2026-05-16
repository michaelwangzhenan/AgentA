"""
全局配置模块 —— LLM Provider 切换核心

通过修改 .env 中的 LLM_PROVIDER 变量即可切换底层模型，无需改动任何业务代码。

支持的 Provider：
    国内直连（无需代理）：
    - kimi    : Moonshot AI（开发/测试，免费额度大）
    - deepseek: DeepSeek（国产，性价比高）
    - qwen    : 阿里云通义千问（国产）
    - minimax : MiniMax（国产）
    - glm     : 智谱 AI GLM（国产）
    - ollama  : 本地 Ollama（完全离线，数据不出本地）
    国外需要代理：
    - openai  : OpenAI GPT 系列（生产环境）
    - grok    : xAI Grok（生产环境）
    - claude  : Anthropic Claude（生产环境，使用原生 SDK）
"""

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderConfig:
    """单个 LLM Provider 的配置"""
    base_url: str
    api_key: str
    model: str
    # 透传给 openai SDK 的额外请求体参数（如 enable_thinking、response_format 等）
    extra_body: dict[str, Any] | None = None


# 当前激活的 Provider，从环境变量读取，默认 kimi
ACTIVE_PROVIDER: str = os.getenv("LLM_PROVIDER", "kimi").lower()

# 所有 Provider 配置表（统一使用 OpenAI SDK 格式，claude 除外）
PROVIDER_CONFIGS: dict[str, ProviderConfig] = {
    "kimi": ProviderConfig(
        base_url="https://api.moonshot.cn/v1",
        api_key=os.getenv("MOONSHOT_API_KEY", ""),
        model="moonshot-v1-8k",
    ),
    "openai": ProviderConfig(
        base_url="https://api.openai.com/v1",
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model="gpt-4o",
    ),
    "deepseek": ProviderConfig(
        base_url="https://api.deepseek.com/v1",
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        model="deepseek-chat",
    ),
    "grok": ProviderConfig(
        base_url="https://api.x.ai/v1",
        api_key=os.getenv("GROK_API_KEY1", ""),
        model="grok-3-latest",
    ),
    "ollama": ProviderConfig(
        base_url="http://localhost:11434/v1",
        api_key="ollama",  # Ollama 不需要真实 key，填占位符即可
        model="qwen2.5:7b",
    ),
    # ── 国内直连 ────────────────────────────────────────────────
    # qwen3 支持 Extended Thinking，但非流式调用必须显式设 enable_thinking=False
    "qwen": ProviderConfig(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("QWEN_API_KEY", ""),
        model="qwen3-8b",  # 可升级为 qwen3-32b / qwen3-235b-a22b 提升效果
        extra_body={"enable_thinking": False},
    ),
    "minimax": ProviderConfig(
        base_url="https://api.minimax.chat/v1",
        api_key=os.getenv("MINIMAX_API_KEY", ""),
        model="MiniMax-Text-01",
    ),
    "glm": ProviderConfig(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key=os.getenv("GLM_API_KEY", ""),
        model="glm-4-flash",
    ),
    # claude 使用原生 anthropic SDK，base_url/api_key 在 provider.py 中单独处理
    "claude": ProviderConfig(
        base_url="",  # 不使用 OpenAI SDK，由 provider.py 原生调用
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model="claude-sonnet-4-5",
    ),
}

# ChromaDB 存储路径
CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "./chroma_db")

# 对话历史 SQLite 路径，可通过 .env 中的 MEMORY_DB_PATH 覆盖
MEMORY_DB_PATH: str = os.getenv("MEMORY_DB_PATH", "./sqlite_db/chat_history.db")

# ── Embedding 模型配置 ────────────────────────────────────────────────────────
# 预定义的 embedding 模型别名，每个别名绑定一个独立的 ChromaDB collection，
# 不同模型向量维度不同（MiniLM=384, bge-small-zh=512），必须分开存储。
#
# 别名格式：{ 别名: (模型名称, collection名称) }
EMBEDDING_MODELS: dict[str, tuple[str, str]] = {
    "en": ("all-MiniLM-L6-v2", "kb_en"),       # 英文/多语言，384维
    "zh": ("BAAI/bge-small-zh", "kb_zh"),      # 中文优化，512维
}

# 默认 embedding 别名，可通过 .env 中的 EMBEDDING_MODEL 覆盖（填别名 en/zh，或直接填模型名）
DEFAULT_EMBEDDING_ALIAS: str = os.getenv("EMBEDDING_MODEL", "en")

def resolve_embedding(model_alias: str) -> tuple[str, str]:
    """
    将别名（en/zh）或模型名称解析为 (model_name, collection_name)。

    - 若传入已知别名（en/zh），直接查表返回。
    - 若传入自定义模型名（含 /），以模型名的最后一段作为 collection 名前缀。

    Returns:
        (model_name, collection_name) 元组
    """
    if model_alias in EMBEDDING_MODELS:
        return EMBEDDING_MODELS[model_alias]
    # 允许直接传入模型名（如 "sentence-transformers/all-mpnet-base-v2"）
    # collection 名取末段，替换特殊字符，确保合法
    safe_name = model_alias.split("/")[-1].replace(".", "_").replace("-", "_")
    return (model_alias, f"kb_{safe_name}")


# ── CLI 目录 ──────────────────────────────────────────────────────────────────
PROMPTS_DIR: str = "advanced/prompts"
SKILLS_DIR: str = "advanced/skills"

# 默认 (model_name, collection_name)，供未指定时使用
DEFAULT_EMBEDDING_MODEL: str
DEFAULT_COLLECTION: str
DEFAULT_EMBEDDING_MODEL, DEFAULT_COLLECTION = resolve_embedding(DEFAULT_EMBEDDING_ALIAS)

# 向后兼容：保留 EMBEDDING_MODEL / CHROMA_COLLECTION 名称，指向默认值
EMBEDDING_MODEL: str = DEFAULT_EMBEDDING_MODEL
CHROMA_COLLECTION: str = DEFAULT_COLLECTION

# 私有文档目录
DOCS_DIR: str = os.getenv("DOCS_DIR", "./docs")

# RAG 检索返回的最大文档片段数
RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))

# HTTP 代理配置
# 格式示例：http://10.144.1.10:8080
# 置空则不使用代理
LLM_PROXY: str = os.getenv("LLM_PROXY", "")

# 需要走代理的 provider（国外服务）
# 国内直连的 provider（kimi / deepseek / ollama）不在此集合中
PROXIED_PROVIDERS: frozenset[str] = frozenset({"openai", "grok", "claude"})

# 文本分块配置
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "600"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))

# Claude 单次响应最大 output token 数
CLAUDE_MAX_TOKENS: int = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))

# ── Reranker 配置 ────────────────────────────────────────────────────────────
# Cross-Encoder 模型，用于在 Bi-Encoder 召回结果上做二阶段精排
RERANKER_MODEL: str = os.getenv(
    "RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
# true 开启二阶段精排；false 跳过精排，直接使用 round-robin 结果（向后兼容）
RERANKER_ENABLED: bool = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
# 召回窗口倍数：精排前取 top_k × N 条候选，默认 3
RERANKER_RECALL_MULTIPLIER: int = int(os.getenv("RERANKER_RECALL_MULTIPLIER", "3"))

# ── Extended Thinking 配置 ────────────────────────────────────────────────────
# true 开启 Extended Thinking；目前 Claude（原生 SDK）和 Qwen3 支持，其余 provider 静默降级
THINKING_ENABLED: bool = os.getenv("THINKING_ENABLED", "false").lower() == "true"
# thinking budget_tokens — 推荐：简单推理 1024~3000，复杂分析 8000~16000，AI Agent 32000+
# 当 THINKING_ADAPTIVE=true 时，本值作为自动估算的上限（cap），而非固定值。
THINKING_BUDGET: int = int(os.getenv("THINKING_BUDGET", "8000"))
# true 开启 Adaptive Thinking：每次推理前自动估算合适的 budget，而非使用固定值。
# 仅在 THINKING_ENABLED=true 时生效。
THINKING_ADAPTIVE: bool = os.getenv("THINKING_ADAPTIVE", "false").lower() == "true"

# ── 跨 session 用户记忆配置 ──────────────────────────────────────────────────
# true 开启跨 session 记忆功能；false 完全禁用（不读取也不写入）
USER_MEMORY_ENABLED: bool = os.getenv("USER_MEMORY_ENABLED", "false").lower() == "true"
# 用户记忆 SQLite 数据库路径（与对话历史独立存储）
USER_MEMORY_DB_PATH: str = os.getenv("USER_MEMORY_DB_PATH", "./sqlite_db/user_memory.db")
# 注入 system prompt 的记忆文本最大字符数（防止占用过多 context）
USER_MEMORY_MAX_CHARS: int = int(os.getenv("USER_MEMORY_MAX_CHARS", "1500"))
# true 每次对话结束后自动提取记忆（每轮额外一次 LLM 调用，默认关闭需手动开启）
USER_MEMORY_AUTO_EXTRACT: bool = os.getenv("USER_MEMORY_AUTO_EXTRACT", "false").lower() == "true"


def get_active_config() -> ProviderConfig:
    """获取当前激活的 Provider 配置，若不存在则抛出异常。"""
    config = PROVIDER_CONFIGS.get(ACTIVE_PROVIDER)
    if config is None:
        supported = ", ".join(PROVIDER_CONFIGS.keys())
        raise ValueError(
            f"不支持的 LLM_PROVIDER: '{ACTIVE_PROVIDER}'，"
            f"支持的值为: {supported}"
        )
    return config

# Agent 实现方式: PYTHON | LANGCHAIN | AUTOGPT
IMP_METHOD: str = os.getenv('IMP_METHOD', 'PYTHON').upper()

# ── Auto-GPT 配置 ─────────────────────────────────────────────────────────────
# 单次规划阶段最多生成几个子任务（Plan 阶段）
AUTOGPT_MAX_PLAN_TASKS: int = int(os.getenv('AUTOGPT_MAX_PLAN_TASKS', '6'))
# 每个子任务迷你 ReAct 子循环最多调用几轮工具（Execute 阶段）
AUTOGPT_MAX_TASK_TOOL_ROUNDS: int = int(os.getenv('AUTOGPT_MAX_TASK_TOOL_ROUNDS', '4'))
