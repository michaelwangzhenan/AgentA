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
    # 强制覆盖 temperature（不为 None 时无视调用方传入值）。
    # 用于个别模型对 temperature 有硬约束的情况，例如：
    #   kimi-k2.6 要求 temperature 必须 = 1（其他值会返回 400）；
    #   Claude Extended Thinking 也要求 temperature = 1（已在 provider.py 中单独处理）。
    force_temperature: float | None = None


# 当前激活的 Provider，从环境变量读取，默认 kimi
ACTIVE_PROVIDER: str = os.getenv("LLM_PROVIDER", "kimi").lower()

# 所有 Provider 配置表（统一使用 OpenAI SDK 格式，claude 除外）
PROVIDER_CONFIGS: dict[str, ProviderConfig] = {
    # kimi-k2.6 是 Moonshot 2026-04 发布的最新旗舰，262K context，K2 系列专门强化
    # agentic tool use（旧的 moonshot-v1-8k 在我们 Agent 链路里有"该调工具不调"的
    # 顽疾，K2.6 显著改善）。旧的 K2 系列（kimi-k2-0905-preview 等）将于 2026-05-25
    # 下线，已不必再考虑回退。若想省钱，可换成 kimi-k2-turbo-preview（更快/便宜）。
    #
    # 关键 quirk：K2.6 默认开 thinking。开了 thinking 之后多轮 tool calling 时，
    # 历史的 assistant tool_call 消息必须把 reasoning_content 字段一并回传给 API，
    # 否则 400 ("thinking is enabled but reasoning_content is missing in assistant
    # tool call message")。我们的 Agent 链路是"决策 + 工具调用"而非"长链推理"，
    # thinking 既无收益还引入这个 bug，故显式关闭。litellm #26156 / openclaw #70392
    # 都遇到同一个坑。
    "kimi": ProviderConfig(
        base_url="https://api.moonshot.cn/v1",
        api_key=os.getenv("MOONSHOT_API_KEY", ""),
        model="kimi-k2.6",
        extra_body={"thinking": {"type": "disabled"}},
        # K2.6 在两种 thinking 模式下分别强制不同的 temperature（其他值一律 400）：
        #   thinking 开启 → 只允许 temperature=1.0
        #   thinking 关闭 → 只允许 temperature=0.6
        # 我们关了 thinking，所以这里必须是 0.6。
        force_temperature=0.6,
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
    #
    # 模型选型：默认换成 qwen3-max 旗舰（阿里官方文档强调"upgraded for agent
    # programming and tool invocation"），适合本项目"Agent + RAG 工具调用"链路。
    # 备选（按价格/速度递减、精度递增）：
    #   qwen-plus-latest    较便宜，常规对话足够
    #   qwen3.5-plus        2026-02 新出，hybrid 线性注意力 + 稀疏 MoE
    #   qwen3-max           ★ 当前默认，旗舰
    #   qwen3-235b-a22b     开源 MoE 版，能力相当但延迟更高
    "qwen": ProviderConfig(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("QWEN_API_KEY", ""),
        model="qwen3-max-preview",
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
# 不同模型向量维度不同（MiniLM=384, bge-small-zh=512, bge-m3=1024），必须分开存储。
# 别名格式：{ 别名: (模型名称, collection名称) }
EMBEDDING_MODELS: dict[str, tuple[str, str]] = {
    "en": ("all-MiniLM-L6-v2", "kb_en"),       # 英文/多语言，384维
    "zh": ("BAAI/bge-small-zh", "kb_zh"),      # 中文优化，512维
    "m3": ("BAAI/bge-m3", "kb_m3"),            # 多语言（dense），1024维
}

# 默认 embedding 别名，可通过 .env 中的 EMBEDDING_MODEL 覆盖（填别名 en/zh/m3，或直接填模型名）
DEFAULT_EMBEDDING_ALIAS: str = os.getenv("EMBEDDING_MODEL", "en")

def resolve_embedding(model_alias: str) -> tuple[str, str]:
    """
    将别名（en/zh/m3）或模型名称解析为 (model_name, collection_name)。

    - 若传入已知别名（en/zh/m3），直接查表返回。
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


# retriever 时查询的 collection 列表
RAG_ACTIVE_EMBEDDINGS: list[str] = [
    a.strip() for a in os.getenv("RAG_ACTIVE_EMBEDDINGS", "en,zh").split(",") if a.strip()
]


def iter_active_embeddings() -> list[tuple[str, str, str]]:
    """
    返回 RAG_ACTIVE_EMBEDDINGS 配置的 embedding 列表 [(alias, model_name, collection_name), ...]
    若 RAG_ACTIVE_EMBEDDINGS 配置错误（全部别名未知），回退到 EMBEDDING_MODELS
    """
    items: list[tuple[str, str, str]] = []
    for alias in RAG_ACTIVE_EMBEDDINGS:
        if alias in EMBEDDING_MODELS:
            model_name, coll = EMBEDDING_MODELS[alias]
            items.append((alias, model_name, coll))
    if items:
        return items
    return [(alias, mn, c) for alias, (mn, c) in EMBEDDING_MODELS.items()]


# 默认 (model_name, collection_name)，供未指定时使用
DEFAULT_EMBEDDING_MODEL: str
DEFAULT_COLLECTION: str
DEFAULT_EMBEDDING_MODEL, DEFAULT_COLLECTION = resolve_embedding(DEFAULT_EMBEDDING_ALIAS)

# 向后兼容：保留 EMBEDDING_MODEL / CHROMA_COLLECTION 名称，指向默认值
EMBEDDING_MODEL: str = DEFAULT_EMBEDDING_MODEL
CHROMA_COLLECTION: str = DEFAULT_COLLECTION

# 私有文档目录（默认 ./datasets/data_en；中文资料放 ./datasets/data_zh，按需通过 .env 切换）
DOCS_DIR: str = os.getenv("DOCS_DIR", "./datasets/data_en")

# RAG 检索返回的最大文档片段数
# Iter-2 默认从 5 提升到 8：枚举/对比类问题 5 条往往不够；当前 LLM 上下文 8K~32K 富裕。
RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "8"))
# 同一来源文件最多保留几条 chunk（避免一个长文档霸屏）；<=0 表示不去重
RAG_K_PER_SOURCE: int = int(os.getenv("RAG_K_PER_SOURCE", "3"))

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
# 默认改为 BAAI/bge-reranker-base（中英双语，中文场景显著优于 ms-marco MiniLM）。
# 多语言/混合语种推荐换为 BAAI/bge-reranker-v2-m3（更准但更大）。
RERANKER_MODEL: str = os.getenv(
    "RERANKER_MODEL", "BAAI/bge-reranker-base"
)
# true 开启二阶段精排；false 跳过精排，直接使用 round-robin 结果（向后兼容）
RERANKER_ENABLED: bool = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
# 召回窗口倍数：精排前取 top_k × N 条候选，默认 3
RERANKER_RECALL_MULTIPLIER: int = int(os.getenv("RERANKER_RECALL_MULTIPLIER", "3"))

# ── RAG 召回质量阈值 ─────────────────────────────────────────────────────────
# Dense 检索后按 cosine 相似度（= 1 - distance，cosine 空间下 ∈ [-1, 1]）过滤；
# 低于此阈值的 chunk 直接丢弃，避免低质量片段污染 LLM 上下文。
# 设为 0 或负数则禁用阈值过滤（向后兼容）。
RAG_DENSE_MIN_SCORE: float = float(os.getenv("RAG_DENSE_MIN_SCORE", "0.30"))

# Iter-5：按 model 校准的 dense 阈值。不同模型的 cosine 相似度分布差异显著：
#   all-MiniLM-L6-v2 同主题对 ~0.40-0.60，正确阈值约 0.25
#   BAAI/bge-small-zh 训练目标更紧 ~0.55-0.85，正确阈值约 0.40
#   BAAI/bge-m3       介于两者之间 ~0.45-0.75，正确阈值约 0.35
# 全局 RAG_DENSE_MIN_SCORE=0.30 对 MiniLM 偏紧（误杀好结果）、对 bge-zh 偏松
# （放进噪声），双库联合命中率被拉低。本字典优先级高于全局值；命中其中一个
# alias 时用本字典，找不到时回退到全局 RAG_DENSE_MIN_SCORE。
RAG_DENSE_MIN_SCORE_PER_MODEL: dict[str, float] = {
    "en": float(os.getenv("RAG_DENSE_MIN_SCORE_EN", "0.25")),
    "zh": float(os.getenv("RAG_DENSE_MIN_SCORE_ZH", "0.40")),
    "m3": float(os.getenv("RAG_DENSE_MIN_SCORE_M3", "0.35")),
}


def min_dense_score_for_collection(collection_name: str) -> float:
    """根据 collection 名反查对应 alias，返回 per-model 阈值；找不到则用全局值。"""
    for alias, (_model, coll) in EMBEDDING_MODELS.items():
        if coll == collection_name and alias in RAG_DENSE_MIN_SCORE_PER_MODEL:
            return RAG_DENSE_MIN_SCORE_PER_MODEL[alias]
    return RAG_DENSE_MIN_SCORE
# Cross-Encoder 精排后的最低相关性分（不同 reranker 输出尺度不同：
#   bge-reranker-base / v2-m3 输出 sigmoid 概率，约 [0, 1]，建议阈值 0.30~0.50；
#   ms-marco MiniLM 输出 raw logit，区间 [-10, 10]，建议阈值 -3 ~ 0）。
# 默认 0.0：保守不过滤，迁移到 bge-reranker 后可上调。
RAG_RERANK_MIN_SCORE: float = float(os.getenv("RAG_RERANK_MIN_SCORE", "0.0"))

# ── BM25 + RRF 混合检索配置 ──────────────────────────────────────────────────
# 开启后 retriever 会同时跑 dense（向量）与 sparse（BM25 关键词）召回，并用
# Reciprocal Rank Fusion 融合排名；对专有名词/缩写/版本号等"低 embedding 区分度"
# 的查询命中率显著优于纯 dense。
BM25_ENABLED: bool = os.getenv("BM25_ENABLED", "true").lower() == "true"
# BM25 Okapi 经典超参：k1 控制 term frequency 饱和度（1.2~2.0），b 控制文档长度归一化（0~1）
BM25_K1: float = float(os.getenv("BM25_K1", "1.5"))
BM25_B: float = float(os.getenv("BM25_B", "0.75"))
# RRF 融合常数 k，论文推荐 60；越大越平滑（rank 之间差异被压制），越小越极端
RRF_K: int = int(os.getenv("RRF_K", "60"))
# BM25 索引存储目录；为空则与 CHROMA_DB_PATH 同级，保持工程目录干净
BM25_INDEX_DIR: str = os.getenv("BM25_INDEX_DIR", "")

# ── PDF OCR 兜底（Iter-4） ──────────────────────────────────────────────────
# 当 PDF 文本层提取的"平均每页字符数"低于阈值时，自动尝试 OCR（rapidocr-onnxruntime）。
# 处理扫描版 / 图片版 PDF。若 rapidocr-onnxruntime 与 pymupdf 未安装，本功能静默禁用。
RAG_OCR_FALLBACK_ENABLED: bool = os.getenv("RAG_OCR_FALLBACK_ENABLED", "true").lower() == "true"
# 平均每页字符数小于此阈值时触发 OCR（默认 50：纯文本 PDF 通常每页数百到数千字符，
# 扫描版往往每页 < 30 字符甚至 0）
RAG_OCR_TRIGGER_CHARS_PER_PAGE: int = int(os.getenv("RAG_OCR_TRIGGER_CHARS_PER_PAGE", "50"))
# OCR 渲染 DPI；越大越清晰但越慢（200 是质量/速度平衡点）
RAG_OCR_DPI: int = int(os.getenv("RAG_OCR_DPI", "200"))

# ── Query 改写 / Multi-Query / HyDE 配置（Iter-3） ───────────────────────────
# 开启后，_tool_search_knowledge 在调用 retriever.search 前会让 LLM 生成 N 条同义改写，
# 与原 query 一起送入检索；命中通过 RRF 自然合并。改写失败时静默降级为只用原 query。
RAG_QUERY_REWRITE_ENABLED: bool = os.getenv("RAG_QUERY_REWRITE_ENABLED", "true").lower() == "true"
# 单次 multi-query 最多生成几条改写（不含原 query），1~5；上调会增加 LLM token 与延迟
RAG_REWRITE_MAX_QUERIES: int = int(os.getenv("RAG_REWRITE_MAX_QUERIES", "3"))
# 开启 HyDE：让 LLM 先产出"假设性答案"，把答案也作为 embedding 检索 query；
# 适合 query 与文档词汇分布差异大的场景（口语 → 文档术语），但每轮多花 1 次 LLM 调用，默认关。
RAG_HYDE_ENABLED: bool = os.getenv("RAG_HYDE_ENABLED", "false").lower() == "true"

# Iter-5：跨语言翻译轴。开启后 expand_queries 会探测原 query 语种（zh/en），
# 让 LLM 翻译成另一种语言再追加进检索 query 列表。
# 解决场景："用中文问 3GPP 术语，英文文档库 dense 命中差 / BM25 跨语言失效"。
# 每次查询多 1 次 LLM 调用，但对中英混合知识库收益显著；翻译失败静默降级。
RAG_TRANSLATE_QUERY_ENABLED: bool = os.getenv("RAG_TRANSLATE_QUERY_ENABLED", "true").lower() == "true"

# ── Extended Thinking 配置 ────────────────────────────────────────────────────
# true 开启 Extended Thinking；目前 Claude（原生 SDK）和 Qwen3 支持，其余 provider 静默降级
THINKING_ENABLED: bool = os.getenv("THINKING_ENABLED", "false").lower() == "true"
# thinking budget_tokens — 推荐：简单推理 1024~3000，复杂分析 8000~16000，AI Agent 32000+
# 当 THINKING_ADAPTIVE=true 时，本值作为自动估算的上限，而非固定值。
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
# 自动提取触发频率：每 N 轮 user 消息才触发一次（显式触发"请记住"不受此限）
USER_MEMORY_EXTRACT_EVERY_N: int = int(os.getenv("USER_MEMORY_EXTRACT_EVERY_N", "5"))

# ── 学习计划（Phase 2.2）配置 ────────────────────────────────────────────────
# 学习计划 SQLite 数据库路径（与对话历史 / 用户记忆独立存储，便于单独 backup / migration）
LEARNING_PLAN_DB_PATH: str = os.getenv("LEARNING_PLAN_DB_PATH", "./sqlite_db/learning.db")
# 注入 system prompt 的 active 学习计划文本最大字符数（超出截断）
LEARNING_PLAN_MAX_INJECT_CHARS: int = int(os.getenv("LEARNING_PLAN_MAX_INJECT_CHARS", "1500"))

# ── Quiz 出题（Phase 2.3）配置 ───────────────────────────────────────────────
# Quiz SQLite 路径（独立文件，便于单独 backup / migration）
QUIZ_DB_PATH: str = os.getenv("QUIZ_DB_PATH", "./sqlite_db/quiz.db")
# create_quiz 默认题数（未传 num_questions 时使用；可选值 5-15）
QUIZ_DEFAULT_NUM_QUESTIONS: int = int(os.getenv("QUIZ_DEFAULT_NUM_QUESTIONS", "10"))
# /quiz list / query_quiz_history 默认返回条数上限
QUIZ_HISTORY_LIST_LIMIT: int = int(os.getenv("QUIZ_HISTORY_LIST_LIMIT", "20"))

# ── SRS 主动复习（Phase 2.4）配置 ────────────────────────────────────────────
# SRS SQLite 路径（独立文件，单表 srs_cards）
SRS_DB_PATH: str = os.getenv("SRS_DB_PATH", "./sqlite_db/srs.db")
# /srs due / query_srs_due 默认返回条数上限
SRS_DEFAULT_DUE_QUERY_LIMIT: int = int(os.getenv("SRS_DEFAULT_DUE_QUERY_LIMIT", "20"))
# SM-2 算法：repetitions=1 时的 interval（首次复习答对的下次回炉天数）
SRS_FIRST_INTERVAL_DAYS: int = int(os.getenv("SRS_FIRST_INTERVAL_DAYS", "1"))
# SM-2 算法：repetitions=2 时的 interval（第二次复习答对的下次回炉天数）
SRS_SECOND_INTERVAL_DAYS: int = int(os.getenv("SRS_SECOND_INTERVAL_DAYS", "6"))

# ── Harness 自检（Phase 2.5）配置 ────────────────────────────────────────────
# 是否对 grade_quiz 批改结果做自检（可选值：true / false）
HARNESS_QUIZ_ENABLED: bool = os.getenv("HARNESS_QUIZ_ENABLED", "true").lower() == "true"
# 是否对 search_knowledge 召回片段做相关性自检（可选值：true / false）
HARNESS_RAG_ENABLED: bool = os.getenv("HARNESS_RAG_ENABLED", "true").lower() == "true"
# critic 单次 LLM 调用超时（秒），超时静默降级
HARNESS_LLM_TIMEOUT_SEC: float = float(os.getenv("HARNESS_LLM_TIMEOUT_SEC", "15"))
# Q1 quiz 批改自检阈值（critic 总分 < 该值标 harness_flagged，0-5 分）
HARNESS_GRADING_THRESHOLD: float = float(os.getenv("HARNESS_GRADING_THRESHOLD", "3.5"))
# 用户输入短于此字符数不触发自动提取（显式触发不受此限；设为 0 禁用）
USER_MEMORY_EXTRACT_MIN_INPUT_LEN: int = int(os.getenv("USER_MEMORY_EXTRACT_MIN_INPUT_LEN", "20"))

# 是否启用项目级 rules 注入（可选值：true / false）
USER_RULES_ENABLED: bool = os.getenv("USER_RULES_ENABLED", "true").lower() == "true"
# rules 文件路径（相对项目根；文件不存在静默跳过）
USER_RULES_FILE: str = os.getenv("USER_RULES_FILE", ".agenta/rules.md")
# 注入字符上限，超出截断（防止占用过多 context）
USER_RULES_MAX_CHARS: int = int(os.getenv("USER_RULES_MAX_CHARS", "4000"))


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

# ── 网络搜索配置 ──────────────────────────────────────────────────────────────
# Serper.dev API Key（用于 web_search 工具；在 .env 中配置 SERPAPI_API_KEY）
SERPAPI_API_KEY: str = os.getenv('SERPAPI_API_KEY', '')

# 是否把 CLI 终端的所有输出同步写到日志文件（可选值：true / false）
# 开启后每次启动新建 ./logs/agenta-YYYYMMDD-HHMMSS.log，关闭时完全无副作用
CLI_LOG_TO_FILE: bool = os.getenv("CLI_LOG_TO_FILE", "false").lower() == "true"
