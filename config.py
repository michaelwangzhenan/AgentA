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

from dotenv import load_dotenv

load_dotenv(override=True)  # override=True 确保 .env 覆盖系统环境变量


@dataclass(frozen=True)
class ProviderConfig:
    """单个 LLM Provider 的配置"""
    base_url: str
    api_key: str
    model: str


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
    "qwen": ProviderConfig(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("QWEN_API_KEY", ""),
        model="qwen-plus",
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

# ── Embedding 模型配置 ────────────────────────────────────────────────────────
# 预定义的 embedding 模型别名，每个别名绑定一个独立的 ChromaDB collection，
# 不同模型向量维度不同（MiniLM=384, bge-small-zh=512），必须分开存储。
#
# 别名格式：{ 别名: (模型名称, collection名称) }
EMBEDDING_MODELS: dict[str, tuple[str, str]] = {
    "en": ("all-MiniLM-L6-v2", "kb_en"),       # 英文/多语言，384维
    "zh": ("BAAI/bge-small-zh", "kb_zh"),        # 中文优化，512维
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
CHUNK_SIZE: int = 600
CHUNK_OVERLAP: int = 100


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
