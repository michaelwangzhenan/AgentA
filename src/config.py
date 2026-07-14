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
    - gemini  : Google Gemini（走 OpenAI 兼容端点，Flash 系列有永久免费额度）
"""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class ThinkingSpec:
    """单个 Provider 的 thinking（推理）能力声明。

    kind 取值：
    - "anthropic"        : Claude 原生 Extended Thinking（provider.py 走 anthropic SDK）
    - "openai_reasoning" : OpenAI 兼容协议，流式读 delta.reasoning_content（qwen/kimi/glm/minimax/deepseek 通用）
    """
    kind: str
    # 开启 thinking 时 merge 进 extra_body 的固定字段（覆盖 ProviderConfig.extra_body 同名键）
    enable_extra_body: dict[str, Any] | None = None
    # budget 放进 extra_body 的 key 名（None 表示该 provider 不支持设 budget，忽略档位 budget）
    budget_key: str | None = None
    # thinking 专用模型（部分 provider 的常规模型不支持思考，开启时切到此模型）；None 表示沿用当前模型
    thinking_model: str | None = None


# ── 两档配置：厂商（连接）+ 模型（能力） ─────────────────────────────────────
# 连接信息（base_url/api_key/SDK/代理）属于「厂商」，同厂商所有模型共用；
# 能力和约束（model_id/extra_body/temperature/thinking/输出上限）属于「具体模型」。
# 选择状态用单一 ACTIVE_MODEL（model id 全局唯一，厂商从 MODEL_CONFIGS[id].provider 推出）。

@dataclass(frozen=True)
class ProviderConfig:
    """单个 LLM 厂商的连接配置（只管「怎么连上」，不含任何模型能力）"""
    base_url: str
    api_key: str
    label: str = ""          # UI 显示名（如 "Moonshot"）
    sdk: str = "openai"      # "openai"（兼容协议）| "anthropic"（Claude 原生 SDK）
    proxied: bool = False    # 是否需要走 LLM_PROXY（国外服务）


@dataclass(frozen=True)
class ModelConfig:
    """单个具体模型的配置（能力 + 约束，反向指到所属厂商）"""
    provider: str            # 所属厂商，PROVIDER_CONFIGS 的 key
    model_id: str            # 真正发给 API 的模型名
    label: str = ""          # UI 显示名（如 "Kimi K2.5"）
    # 透传给 openai SDK 的额外请求体参数（如 enable_thinking、response_format 等）
    extra_body: dict[str, Any] | None = None
    # 强制覆盖 temperature（个别模型有硬约束，如 kimi 要求 = 0.6；不为 None 时无视调用方传值）
    force_temperature: float | None = None
    # thinking 能力声明（None = 该模型不支持，call_with_thinking 静默降级为普通 chat）
    thinking: "ThinkingSpec | None" = None
    # 单次最大输出 tokens（None 用全局默认）；thinking 时 max_tokens 不能超此上限
    max_output_tokens: int | None = None
    # 能力/价位档位（可选值：min / low / medium / high / max；空字符串 = 不显示徽章）
    tier: str = ""
    # 是否支持多轮工具调用（False 时 agent 整轮不传 tools，降级为纯聊天）
    supports_tools: bool = True


PROVIDER_CONFIGS: dict[str, ProviderConfig] = {
    "kimi": ProviderConfig(
        base_url="https://api.moonshot.cn/v1",
        api_key=os.getenv("MOONSHOT_API_KEY", ""),
        label="Moonshot",
    ),
    "qwen": ProviderConfig(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("QWEN_API_KEY", ""),
        label="通义千问",
    ),
    "deepseek": ProviderConfig(
        base_url="https://api.deepseek.com/v1",
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        label="DeepSeek",
    ),
    "glm": ProviderConfig(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key=os.getenv("GLM_API_KEY", ""),
        label="智谱 GLM",
    ),
    "minimax": ProviderConfig(
        base_url="https://api.minimax.chat/v1",
        api_key=os.getenv("MINIMAX_API_KEY", ""),
        label="MiniMax",
    ),
    "claude": ProviderConfig(
        base_url="",  # 不使用 OpenAI SDK，由 provider.py 原生调用
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        label="Anthropic Claude",
        sdk="anthropic",
        proxied=True,
    ),
    "openai": ProviderConfig(
        base_url="https://api.openai.com/v1",
        api_key=os.getenv("OPENAI_API_KEY", ""),
        label="OpenAI",
        proxied=True,
    ),
    "grok": ProviderConfig(
        base_url="https://api.x.ai/v1",
        api_key=os.getenv("GROK_API_KEY1", ""),
        label="xAI Grok",
        proxied=True,
    ),
    "gemini": ProviderConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.getenv("GEMINI_API_KEY", ""),
        label="Google Gemini",
        proxied=True,
    ),
    "ollama": ProviderConfig(
        base_url="http://localhost:11434/v1",
        api_key="ollama",  # Ollama 不需要真实 key，填占位符即可
        label="Ollama (本地)",
    ),
}

# DashScope 通义千问系列共用：非流式必须 enable_thinking=False，开启 thinking 时翻成 True
# 并用 thinking_budget 控预算（整个 qwen 系列协议一致，故抽出来共用）
_QWEN_EXTRA = {"enable_thinking": False}
_QWEN_THINKING = ThinkingSpec(
    kind="openai_reasoning",
    enable_extra_body={"enable_thinking": True},
    budget_key="thinking_budget",
)

# 模型表（key = model id，全局唯一，也是 ACTIVE_MODEL 的取值）
# 顺序：kimi → qwen → deepseek → glm → minimax → claude → openai → xAI → ollama
MODEL_CONFIGS: dict[str, ModelConfig] = {
    # ── Moonshot Kimi ──────────────────────────────────────────────────────
    "kimi-k2.5": ModelConfig(
        provider="kimi", model_id="kimi-k2.5", label="Kimi K2.5",
        extra_body={"thinking": {"type": "disabled"}},
        force_temperature=0.6,
        # k2.5 开启 thinking：thinking.type=enabled（keep 仅 k2.6 支持，k2.5 传会 400）
        thinking=ThinkingSpec(kind="openai_reasoning", enable_extra_body={"thinking": {"type": "enabled"}}),
        tier="high",
    ),
    "kimi-k2.6": ModelConfig(
        provider="kimi", model_id="kimi-k2.6", label="Kimi K2.6",
        extra_body={"thinking": {"type": "disabled"}},
        force_temperature=0.6,
        # k2.6 支持 keep=all 跨轮保留思考
        thinking=ThinkingSpec(kind="openai_reasoning", enable_extra_body={"thinking": {"type": "enabled", "keep": "all"}}),
        tier="high",
    ),
    # ── 通义千问（DashScope，全系列共用 _QWEN_EXTRA / _QWEN_THINKING）────────
    "qwen3.6-flash": ModelConfig(
        provider="qwen", model_id="qwen3.6-flash", label="Qwen3.6 Flash",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="low",
    ),
    "qwen3.6-flash-2026-04-16": ModelConfig(
        provider="qwen", model_id="qwen3.6-flash-2026-04-16", label="Qwen3.6 Flash (2026-04-16)",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="low",
    ),
    "qwen3.6-27b": ModelConfig(
        provider="qwen", model_id="qwen3.6-27b", label="Qwen3.6 27B",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="low",
    ),
    "qwen3.6-35b-a3b": ModelConfig(
        provider="qwen", model_id="qwen3.6-35b-a3b", label="Qwen3.6 35B-A3B",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="low",
    ),
    "qwen3.6-plus": ModelConfig(
        provider="qwen", model_id="qwen3.6-plus", label="Qwen3.6 Plus",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="medium",
    ),
    "qwen3.6-plus-2026-04-02": ModelConfig(
        provider="qwen", model_id="qwen3.6-plus-2026-04-02", label="Qwen3.6 Plus (2026-04-02)",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="medium",
    ),
    "qwen3.6-max-preview": ModelConfig(
        provider="qwen", model_id="qwen3.6-max-preview", label="Qwen3.6 Max (preview)",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="high",
    ),
    # 保留的旧版（其余 qwen3.5 已下线）
    "qwen3.5-plus-2026-04-20": ModelConfig(
        provider="qwen", model_id="qwen3.5-plus-2026-04-20", label="Qwen3.5 Plus (2026-04-20)",
        extra_body=_QWEN_EXTRA, thinking=_QWEN_THINKING, tier="medium",
    ),
    # ── DeepSeek ───────────────────────────────────────────────────────────
    "deepseek-v4-flash": ModelConfig(
        provider="deepseek", model_id="deepseek-v4-flash", label="DeepSeek V4 Flash",
        # V4 自带思考，开启时直接读 reasoning_content（不切模型、不加额外开关）
        thinking=ThinkingSpec(kind="openai_reasoning"), tier="min",
    ),
    "deepseek-chat": ModelConfig(
        provider="deepseek", model_id="deepseek-chat", label="DeepSeek Chat",
        # 思考能力在 deepseek-reasoner（thinking-only，无开关）；开启时切模型
        thinking=ThinkingSpec(kind="openai_reasoning", thinking_model="deepseek-reasoner"),
        tier="medium",
    ),
    "deepseek-v4-pro": ModelConfig(
        provider="deepseek", model_id="deepseek-v4-pro", label="DeepSeek V4 Pro",
        thinking=ThinkingSpec(kind="openai_reasoning"), tier="high",
    ),
    # ── 智谱 GLM ───────────────────────────────────────────────────────────
    "glm-4-flash": ModelConfig(
        provider="glm", model_id="glm-4-flash", label="GLM-4 Flash (free)",
        # glm-4-flash 不支持 thinking；开启时切到 glm-4.6
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"thinking": {"type": "enabled"}},
            thinking_model="glm-4.6",
        ),
        tier="low",
    ),
    "glm-4.5-flash": ModelConfig(
        provider="glm", model_id="glm-4.5-flash", label="GLM-4.5 Flash (free)",
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"thinking": {"type": "enabled"}},
        ),
        tier="low",
    ),
    "glm-4.7-flash": ModelConfig(
        provider="glm", model_id="glm-4.7-flash", label="GLM-4.7 Flash (free)",
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"thinking": {"type": "enabled"}},
        ),
        tier="low",
    ),
    "glm-4.5": ModelConfig(
        provider="glm", model_id="glm-4.5", label="GLM-4.5",
        # 默认不思考；开启时加 thinking.type=enabled（GLM 思考开关）
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"thinking": {"type": "enabled"}},
        ),
        tier="medium",
    ),
    "glm-4.6": ModelConfig(
        provider="glm", model_id="glm-4.6", label="GLM-4.6",
        # 默认不思考；开启时加 thinking.type=enabled（GLM 思考开关）
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"thinking": {"type": "enabled"}},
        ),
        tier="high",
    ),
    "glm-5.1": ModelConfig(
        provider="glm", model_id="glm-5.1", label="GLM-5.1",
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"thinking": {"type": "enabled"}},
        ),
        tier="high",
    ),
    # ── MiniMax ────────────────────────────────────────────────────────────
    "MiniMax-Text-01": ModelConfig(
        provider="minimax", model_id="MiniMax-Text-01", label="MiniMax Text-01",
        # 推理走 M1（思考默认开）；reasoning_split=True 才把思考拆到 reasoning_content
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"reasoning_split": True},
            thinking_model="MiniMax-M1",
        ),
        tier="medium",
    ),
    "MiniMax-M2": ModelConfig(
        provider="minimax", model_id="MiniMax-M2", label="MiniMax M2",
        # M 系列本身即推理模型；reasoning_split=True 把思考拆到 reasoning_content
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"reasoning_split": True},
        ),
        tier="medium",
    ),
    "MiniMax-M2.7-highspeed": ModelConfig(
        provider="minimax", model_id="MiniMax-M2.7-highspeed", label="MiniMax M2.7 (高速)",
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"reasoning_split": True},
        ),
        tier="high",
    ),
    "MiniMax-M3": ModelConfig(
        provider="minimax", model_id="MiniMax-M3", label="MiniMax M3",
        thinking=ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"reasoning_split": True},
        ),
        tier="high",
    ),
    # ── Anthropic Claude ───────────────────────────────────────────────────
    "claude-sonnet-4-5": ModelConfig(
        provider="claude", model_id="claude-sonnet-4-5", label="Claude Sonnet 4.5",
        # 原生 Extended Thinking，budget 由 budget_tokens 原生参数控制
        thinking=ThinkingSpec(kind="anthropic"),
        max_output_tokens=64_000, tier="high",
    ),
    "claude-sonnet-4-6": ModelConfig(
        provider="claude", model_id="claude-sonnet-4-6", label="Claude Sonnet 4.6",
        thinking=ThinkingSpec(kind="anthropic"),
        max_output_tokens=64_000, tier="high",
    ),
    "claude-opus-4-7": ModelConfig(
        provider="claude", model_id="claude-opus-4-7", label="Claude Opus 4.7",
        thinking=ThinkingSpec(kind="anthropic"),
        max_output_tokens=64_000, tier="max",
    ),
    "claude-opus-4-8": ModelConfig(
        provider="claude", model_id="claude-opus-4-8", label="Claude Opus 4.8",
        thinking=ThinkingSpec(kind="anthropic"),
        max_output_tokens=64_000, tier="max",
    ),
    # ── OpenAI ─────────────────────────────────────────────────────────────
    # GPT-5 / Codex 走 chat completions，思考过程 API 不透出 reasoning_content，故不声明 thinking
    "gpt-4o": ModelConfig(provider="openai", model_id="gpt-4o", label="GPT-4o", tier="medium"),
    "gpt-5.3-codex": ModelConfig(provider="openai", model_id="gpt-5.3-codex", label="GPT-5.3 Codex", tier="high"),
    "gpt-5.4": ModelConfig(provider="openai", model_id="gpt-5.4", label="GPT-5.4", tier="max"),
    # ── Google Gemini（OpenAI 兼容端点；Flash 系列永久免费额度）──────────────
    # 经 OpenAI 兼容 shim 调用，思考过程不稳定透出，统一不声明 thinking。
    # 2.5 系列可正常多轮工具调用；3.x 系列每次工具调用会返回 thought_signature 且要求
    # 下一轮原样回传，OpenAI 兼容层带不上 → 触发工具的第二轮必 400，故 3.x 仅适合纯聊天。
    "gemini-2.5-flash-lite": ModelConfig(
        provider="gemini", model_id="gemini-2.5-flash-lite",
        label="Gemini 2.5 Flash-Lite (free)", tier="min",
    ),
    "gemini-2.5-flash": ModelConfig(
        provider="gemini", model_id="gemini-2.5-flash",
        label="Gemini 2.5 Flash (free)", tier="low",
    ),
    "gemini-3.1-flash-lite": ModelConfig(
        provider="gemini", model_id="gemini-3.1-flash-lite",
        label="Gemini 3.1 Flash-Lite (free) · 仅聊天", tier="min",
        supports_tools=False,
    ),
    "gemini-3.5-flash": ModelConfig(
        provider="gemini", model_id="gemini-3.5-flash",
        label="Gemini 3.5 Flash (free) · 仅聊天", tier="low",
        supports_tools=False,
    ),
    # ── xAI Grok ───────────────────────────────────────────────────────────
    "grok-3-latest": ModelConfig(provider="grok", model_id="grok-3-latest", label="Grok 3", tier="high"),
    # ── Ollama 本地 ────────────────────────────────────────────────────────
    "qwen2.5:7b": ModelConfig(provider="ollama", model_id="qwen2.5:7b", label="Qwen2.5 7B (本地)", tier="min"),
}

# 全局默认 LLM 模型（model id，厂商从 MODEL_CONFIGS 反推）。CLI 直接用它；Web 端每用户在
# 聊天页自选、未选时回落到此；评估脚本生成答案也用它。可选值见 MODEL_CONFIGS 的 key
# AGENTA_EVAL_ACTIVE_MODEL 优先：离线评估子进程注入它临时指定测试模型——该变量不在 .env 里，
# 故能扛住各 eval 入口的 load_dotenv(override=True)（否则会被 .env 的 ACTIVE_MODEL 覆盖回去）。
ACTIVE_MODEL: str = os.getenv("AGENTA_EVAL_ACTIVE_MODEL") or os.getenv("ACTIVE_MODEL", "kimi-k2.5")

# ChromaDB 存储路径（仅向量库元数据 + 段目录；BM25 默认另见 BM25_INDEX_DIR）
CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "./db/chroma")

# 对话历史 SQLite 路径，可通过 .env 中的 MEMORY_DB_PATH 覆盖
MEMORY_DB_PATH: str = os.getenv("MEMORY_DB_PATH", "./db/sqlite/session.db")

# ── 多用户 / 认证 ────────────────────────────────────────────────────────────
# 是否启用多用户认证（可选值：true / false）；false 时不校验登录，全部落到 DEFAULT_USER_ID
AUTH_ENABLED: bool = os.getenv("AUTH_ENABLED", "true").lower() == "true"
# 账号 / 登录态 / 每用户 rules 的 SQLite 路径
AUTH_DB_PATH: str = os.getenv("AUTH_DB_PATH", "./db/sqlite/auth.db")
# 该用户名注册后自动成为 admin，其余均为普通用户
AUTH_ADMIN_USERNAME: str = os.getenv("AUTH_ADMIN_USERNAME", "admin")
# 登录态有效天数
AUTH_SESSION_TTL_DAYS: int = int(os.getenv("AUTH_SESSION_TTL_DAYS", "30"))
# 存放 session token 的 cookie 名
AUTH_COOKIE_NAME: str = os.getenv("AUTH_COOKIE_NAME", "agenta_session")
# CLI / 测试 / 关认证时使用的用户 id
DEFAULT_USER_ID: int = int(os.getenv("DEFAULT_USER_ID", "1"))

# ── Token 用量统计 ──────────────────────────────────────────────────────────
# token 用量记录数据库路径
USAGE_DB_PATH: str = os.getenv("USAGE_DB_PATH", "./db/sqlite/usage.db")
# 估算成本展示用的币种符号（可选值：任意符号，如 ¥ / $）
USAGE_CURRENCY: str = os.getenv("USAGE_CURRENCY", "¥")
# 内置默认单价 {model_id: (输入价, 输出价)}，单位：每 1M token，币种见 USAGE_CURRENCY。
# 数值为美元公开价按汇率 7.1 折算的人民币。未列出的模型按 0 计成本（token 仍照常统计）。
MODEL_PRICING_DEFAULTS: dict[str, tuple[float, float]] = {
    # Moonshot Kimi
    "kimi-k2.5": (3.91, 20.95),
    "kimi-k2.6": (6.75, 28.40),
    # 通义千问（阶梯价取低档；qwen3.6 价格暂沿用 3.5 同档估算，待确认真实单价）
    "qwen3.6-flash": (0.36, 2.84),
    "qwen3.6-flash-2026-04-16": (0.36, 2.84),
    "qwen3.6-27b": (0.71, 2.84),
    "qwen3.6-35b-a3b": (0.71, 2.84),
    "qwen3.6-plus": (0.85, 4.90),
    "qwen3.6-plus-2026-04-02": (0.85, 4.90),
    "qwen3.6-max-preview": (2.84, 8.52),
    "qwen3.5-plus-2026-04-20": (0.85, 4.90),
    # DeepSeek（v4-pro 为促销价；deepseek-chat 现映射 V4 Flash）
    "deepseek-v4-flash": (0.99, 1.99),
    "deepseek-chat": (0.99, 1.99),
    "deepseek-v4-pro": (3.12, 6.18),
    # 智谱 GLM（Flash 系列免费；其余为估算）
    "glm-4-flash": (0.0, 0.0),
    "glm-4.5-flash": (0.0, 0.0),
    "glm-4.7-flash": (0.0, 0.0),
    "glm-4.5": (2.13, 2.13),
    "glm-4.6": (4.97, 4.97),
    "glm-5.1": (4.97, 14.20),
    # MiniMax（highspeed 翻倍；部分估算）
    "MiniMax-Text-01": (1.42, 7.81),
    "MiniMax-M2": (2.13, 8.52),
    "MiniMax-M2.7-highspeed": (4.26, 17.04),
    "MiniMax-M3": (2.13, 8.52),
    # Anthropic Claude
    "claude-sonnet-4-5": (21.30, 106.50),
    "claude-sonnet-4-6": (21.30, 106.50),
    "claude-opus-4-7": (35.50, 177.50),
    "claude-opus-4-8": (35.50, 177.50),
    # OpenAI
    "gpt-4o": (17.75, 71.00),
    "gpt-5.3-codex": (12.43, 99.40),
    "gpt-5.4": (17.75, 106.50),
    # Google Gemini（标 free，给付费档参考价；走免费额度可在 UI 改 0）
    "gemini-2.5-flash-lite": (0.71, 2.84),
    "gemini-2.5-flash": (2.13, 17.75),
    "gemini-3.1-flash-lite": (1.78, 10.65),
    "gemini-3.5-flash": (3.55, 21.30),
    # xAI Grok（grok-3 已退役，现价随 4.3）
    "grok-3-latest": (8.88, 17.75),
    # Ollama 本地（无 API 费）
    "qwen2.5:7b": (0.0, 0.0),
}

# ── 评估 + 可观测 ────────────────────────────────────────────────────────────
# 是否采集每次对话的分阶段 trace（检索 / LLM / tool 耗时 + token + 成本）；
# 写入复用 usage.db 的 trace 表。出错只记日志、不影响对话（可选值：true / false）
TRACE_ENABLED: bool = os.getenv("TRACE_ENABLED", "true").lower() == "true"
# RAG golden 数据集库路径（带来源 / 审核状态，支持在线 CRUD）
RAG_GOLDEN_DB_PATH: str = os.getenv("RAG_GOLDEN_DB_PATH", "./db/sqlite/rag_golden.db")
# 生成 golden 默认 LLM：none | kimi-k2.5 | deepseek-v4-flash（UI/CLI 未指定时回落）
EVAL_GOLDEN_LLM: str = os.getenv("EVAL_GOLDEN_LLM", "none")
# UI/CLI 未指定数量时的默认出题条数
EVAL_GOLDEN_MAX_Q: int = int(os.getenv("EVAL_GOLDEN_MAX_Q", "3"))
# 跑评估时是否纳入未审核（pending）的 golden；默认只用已审核（approved）的
EVAL_GOLDEN_USE_PENDING: bool = os.getenv("EVAL_GOLDEN_USE_PENDING", "false").lower() == "true"
# 答案质量评委（faithfulness / 相关度）用的模型 id；空则回落 ACTIVE_MODEL。
# 建议填一个与被评模型不同的，避免同模型自评偏高（取值见 MODEL_CONFIGS 的 key）
EVAL_JUDGE_MODEL: str = os.getenv("EVAL_JUDGE_MODEL", "")

# ── 降本：模型路由 + 语义缓存 ──────────────────────────────────────────────────
# 是否启用模型路由（按难度向更便宜的模型降级；可选值：true / false）
MODEL_ROUTING_ENABLED: bool = os.getenv("MODEL_ROUTING_ENABLED", "true").lower() == "true"
# 路由难度判定方式（可选值：rule / classifier / hybrid）
#   rule       纯规则启发（长度 / 是否带 tool 倾向 / 关键词），零额外开销
#   classifier 调小模型给难度打分（多一次调用，有成本 / 延迟）
#   hybrid     规则先判，拿不准再调小模型
MODEL_ROUTING_MODE: str = os.getenv("MODEL_ROUTING_MODE", "rule").lower()
# classifier / hybrid 模式下做难度打分的小模型 id；为空则降级为纯规则
MODEL_ROUTING_CLASSIFIER_MODEL: str = os.getenv("MODEL_ROUTING_CLASSIFIER_MODEL", "")
# 是否启用语义缓存（相近 query 命中历史答案，跳过整次检索 + 生成；可选值：true / false）
# 出错只记日志、不阻断对话
SEMANTIC_CACHE_ENABLED: bool = os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() == "true"
# 语义缓存用的 ChromaDB collection 名（存在 CHROMA_DB_PATH 下，与 KB collection 分开）
SEMANTIC_CACHE_COLLECTION: str = os.getenv("SEMANTIC_CACHE_COLLECTION", "semantic_cache")
# 命中相似度阈值（余弦相似度，0~1；偏严避免误命中）
SEMANTIC_CACHE_THRESHOLD: float = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.95"))
# 缓存条目过期天数；查询时过滤掉过期条目
SEMANTIC_CACHE_TTL_DAYS: int = int(os.getenv("SEMANTIC_CACHE_TTL_DAYS", "7"))

# 同时在跑的 agent.run 并发上限（信号量）；超出的请求排队等待，
# 防止并发把 LLM 配额 / CPU（含 search_knowledge 精排）打满。
MAX_CONCURRENT_AGENT_RUNS: int = int(os.getenv("MAX_CONCURRENT_AGENT_RUNS", "4"))

# ── Deep Research（深度研究）──────────────────────────────────────────────────
# 一句话换回一篇带引用的调研报告：规划拆子问题 → 并行子代理查 KB+web → 反思补查 → 综述成稿。
# 定位"重质量不重速度"；走独立 ResearchEngine，不复用普通 chat 主循环。
# 是否启用深度研究（关则前端开关隐藏、收到 deep_research 请求降级为普通对话；可选值：true / false）
DEEP_RESEARCH_ENABLED: bool = os.getenv("DEEP_RESEARCH_ENABLED", "true").lower() == "true"
# 规划阶段子问题数上限（实际数量裁剪到 3~该值）
DEEP_RESEARCH_MAX_SUBQUESTIONS: int = int(os.getenv("DEEP_RESEARCH_MAX_SUBQUESTIONS", "5"))
# 子代理并行上限（同时在跑的子代理数；放大会成倍占用 LLM 配额，故封顶）
DEEP_RESEARCH_MAX_PARALLEL_SUBAGENTS: int = int(os.getenv("DEEP_RESEARCH_MAX_PARALLEL_SUBAGENTS", "3"))
# 单个子代理的工具调用轮次上限（到上限即让它就该子问题产出小结）
DEEP_RESEARCH_SUBAGENT_MAX_ROUNDS: int = int(os.getenv("DEEP_RESEARCH_SUBAGENT_MAX_ROUNDS", "4"))
# 单个子代理最多采纳的来源数（KB + web 合计），防单路检索失控
DEEP_RESEARCH_MAX_SOURCES_PER_SUBAGENT: int = int(os.getenv("DEEP_RESEARCH_MAX_SOURCES_PER_SUBAGENT", "5"))
# 整次研究的总来源上限，防全局失控
DEEP_RESEARCH_MAX_TOTAL_SOURCES: int = int(os.getenv("DEEP_RESEARCH_MAX_TOTAL_SOURCES", "20"))
# 是否开反思补查（综述前评估缺口，最多补查 1 轮；可选值：true / false）
DEEP_RESEARCH_REFLECT_ENABLED: bool = os.getenv("DEEP_RESEARCH_REFLECT_ENABLED", "true").lower() == "true"

# 单次问答里最多调几轮工具（baseline，无 active plan 时用），防止 LLM 工具调用死循环
MAX_TOOL_ROUNDS: int = int(os.getenv("MAX_TOOL_ROUNDS", "8"))

# 单次问答的总推理轮次上限（含工具调用 + 最终回答，baseline），超出强制兜底回答
MAX_TOTAL_ROUNDS: int = int(os.getenv("MAX_TOTAL_ROUNDS", "12"))

# plan 自适应放大后的硬上限：再多步的 plan 也不超此值，防极端
MAX_HARD_CAP_ROUNDS: int = int(os.getenv("MAX_HARD_CAP_ROUNDS", "50"))

# ── Embedding 模型配置 ────────────────────────────────────────────────────────
# 预定义的 embedding 模型别名，每个别名绑定一个独立的 ChromaDB collection，
# 不同模型向量维度不同（MiniLM=384, bge-small-zh=512, bge-m3=1024），必须分开存储。
# 别名格式：{ 别名: (模型名称, collection名称) }
EMBEDDING_MODELS: dict[str, tuple[str, str]] = {
    "en": ("all-MiniLM-L6-v2", "kb_en"),       # 英文/多语言，384维
    "zh": ("BAAI/bge-small-zh", "kb_zh"),      # 中文优化，512维
    "m3": ("BAAI/bge-m3", "kb_m3"),            # 多语言（dense），1024维
}

# 默认 embedding 别名，可通过 .env 中的 EMBEDDING_MODEL 覆盖（en/zh/m3/api-m3，或直接填模型名）
DEFAULT_EMBEDDING_ALIAS: str = os.getenv("EMBEDDING_MODEL", "en")


def alias_is_api(alias: str) -> bool:
    """别名是否显式要求走云端 API（目前仅 api-m3）。

    用于「入库按次选来源」：入库传 api-m3 即云端、传 m3 即本地，与全局默认解耦。
    """
    return alias == "api-m3"


def default_embedding_is_api() -> bool:
    """默认 embedding 是否走云端 API（EMBEDDING_MODEL 选了 api-m3）。

    决定「检索」与「语义缓存」里 m3 的编码来源（全局）；入库来源另由所选别名决定。
    """
    return alias_is_api(DEFAULT_EMBEDDING_ALIAS)


def resolve_embedding(model_alias: str) -> tuple[str, str]:
    """
    将别名（en/zh/m3/api-m3）或模型名称解析为 (model_name, collection_name)。

    - api-m3 走云端 bge-m3，与本地 m3 共用同一 (bge-m3, kb_m3)，仅编码来源不同。
    - 若传入已知别名，直接查表返回。
    - 若传入自定义模型名（含 /），以模型名的最后一段作为 collection 名前缀。

    Returns:
        (model_name, collection_name) 元组
    """
    if model_alias == "api-m3":
        return EMBEDDING_MODELS["m3"]
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

# Web UI 拖拽上传的落盘目录（独立子目录避免污染 git tracked 的 data_*）
WEB_UPLOAD_DIR: str = os.getenv("WEB_UPLOAD_DIR", "./datasets/web_uploads")

# Web UI 单次上传文件大小上限（MB），超限返回 413
WEB_MAX_UPLOAD_MB: int = int(os.getenv("WEB_MAX_UPLOAD_MB", "10"))

# DOCX 解压总量超过此值时改用流式解析（MiB）；不再直接拒绝
DOCX_MAX_UNZIP_MB: int = int(os.getenv("DOCX_MAX_UNZIP_MB", "16"))
# DOCX 解压总量硬上限（MiB）；防 zip bomb，超过则拒绝
DOCX_HARD_MAX_UNZIP_MB: int = int(os.getenv("DOCX_HARD_MAX_UNZIP_MB", "512"))
# DOCX 隔离解析子进程的内存上限（MiB）
DOCX_PARSE_MEMORY_MB: int = int(os.getenv("DOCX_PARSE_MEMORY_MB", "512"))
# DOCX 隔离解析的执行时间上限（秒）
DOCX_PARSE_TIMEOUT_SEC: int = int(os.getenv("DOCX_PARSE_TIMEOUT_SEC", "120"))
# 同时执行的入库任务上限（Web 上传与 CLI 共用）
INGEST_MAX_CONCURRENT: int = int(os.getenv("INGEST_MAX_CONCURRENT", "1"))

# fetch_url / Jina Reader 响应体下载上限（字节）
MAX_FETCH_BYTES: int = int(os.getenv("MAX_FETCH_BYTES", str(4 * 1024 * 1024)))
# 管理员上传备份 zip 大小上限（MiB）
BACKUP_MAX_UPLOAD_MB: int = int(os.getenv("BACKUP_MAX_UPLOAD_MB", "256"))
# 备份 zip 解压后成员总大小上限（MiB），防 zip bomb
BACKUP_MAX_UNZIP_MB: int = int(os.getenv("BACKUP_MAX_UNZIP_MB", "1024"))
# 备份 zip 最大压缩比（解压总大小 / 压缩包大小）
BACKUP_MAX_COMPRESSION_RATIO: int = int(os.getenv("BACKUP_MAX_COMPRESSION_RATIO", "100"))
# 聊天消息最大字节数（UTF-8），含用户输入与内嵌附件正文
CHAT_MESSAGE_MAX_BYTES: int = int(os.getenv("CHAT_MESSAGE_MAX_BYTES", str(512 * 1024)))
# 单条消息附件数量上限
CHAT_ATTACHMENT_MAX_COUNT: int = int(os.getenv("CHAT_ATTACHMENT_MAX_COUNT", "5"))

# 运行时数据备份目录（tools/cli/backup_cli.py 与 /admin/backup 生成的 zip 落此；含明文密钥，已 gitignore）
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "./backups")

# RAG 检索返回的最大文档片段数
# Iter-2 默认从 5 提升到 8：枚举/对比类问题 5 条往往不够；当前 LLM 上下文 8K~32K 富裕。
RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "8"))
# 同一来源文件最多保留几条 chunk（避免一个长文档霸屏）；<=0 表示不去重
RAG_K_PER_SOURCE: int = int(os.getenv("RAG_K_PER_SOURCE", "3"))

# HTTP 代理配置
# 格式示例：http://10.144.1.10:8080
# 置空则不使用代理
LLM_PROXY: str = os.getenv("LLM_PROXY", "")

# 文本分块配置
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "600"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))

# Claude 单次响应最大 output token 数
CLAUDE_MAX_TOKENS: int = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))

# ── Reranker 配置 ────────────────────────────────────────────────────────────
# 单下拉值即「唯一真相」，一个值同时表达「开不开 / 云端还是本地 / 哪个模型」：
#   disable                              → 关闭精排
#   api:<模型名>（如 api:BAAI/bge-reranker-v2-m3） → 走硅基云端精排
#   <本地模型名>（如 BAAI/bge-reranker-base）      → 本地 CrossEncoder
# 靠 disable 特判 + api: 前缀自解释，代码里不再有独立的 enabled / backend 开关。
RERANKER_MODEL: str = os.getenv(
    "RERANKER_MODEL", "BAAI/bge-reranker-base"
)
# 召回窗口倍数：精排前取 top_k × N 条候选；调大召回更全但精排更慢，默认 2
RERANKER_RECALL_MULTIPLIER: int = int(os.getenv("RERANKER_RECALL_MULTIPLIER", "2"))

_RERANK_API_PREFIX = "api:"


def rerank_enabled() -> bool:
    """是否开启精排：RERANKER_MODEL 非 disable 即开。"""
    return RERANKER_MODEL != "disable"


def rerank_is_api() -> bool:
    """精排是否走云端 API：RERANKER_MODEL 以 api: 前缀开头即 api。"""
    return RERANKER_MODEL.startswith(_RERANK_API_PREFIX)


def rerank_model_name() -> str:
    """去掉 api: 前缀后的真实模型名。

    本地加载 / api 模型 id / 阈值查表都用它——api 与本地同款模型去前缀后同名，
    自动命中同一条 per-model 阈值。
    """
    return RERANKER_MODEL[len(_RERANK_API_PREFIX):] if rerank_is_api() else RERANKER_MODEL


# ── SiliconFlow 云端 API（embedding / rerank）────────────────────────────────
# embedding 是否走云端由 EMBEDDING_MODEL=api-m3 决定；rerank 由 RERANKER_MODEL 的
# api: 前缀决定。二者都只对 ONLINE_API_MODELS 表内模型生效，表外模型走本地。
# SiliconFlow 连接配置（domestic，直连不走 LLM_PROXY）
SILICONFLOW_API_KEY: str = os.getenv("SILICONFLOW_API_KEY", "")
SILICONFLOW_BASE_URL: str = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_TIMEOUT_SEC: float = float(os.getenv("SILICONFLOW_TIMEOUT_SEC", "30"))
SILICONFLOW_MAX_RETRIES: int = int(os.getenv("SILICONFLOW_MAX_RETRIES", "2"))

# 本地模型名 → (厂商, API 模型 id)：表内模型才有云端版，表外一律本地。
ONLINE_API_MODELS: dict[str, tuple[str, str]] = {
    "BAAI/bge-m3": ("siliconflow", "BAAI/bge-m3"),                          # embedding
    "BAAI/bge-reranker-v2-m3": ("siliconflow", "BAAI/bge-reranker-v2-m3"),  # rerank
}


def online_api_model(model_name: str) -> tuple[str, str] | None:
    """查模型是否有 API 版；返回 (厂商, API 模型 id)，表外返回 None。"""
    return ONLINE_API_MODELS.get(model_name)


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
# 默认 0.30：按默认 reranker bge-reranker-base 标定；分布不同的模型见下方 PER_MODEL。
# 标定常量，不进 .env；需扫阈值时仍可用同名 env 覆盖。
RAG_RERANK_MIN_SCORE: float = float(os.getenv("RAG_RERANK_MIN_SCORE", "0.30"))

# 精排阈值按 reranker 模型名覆盖，优先于全局 RAG_RERANK_MIN_SCORE，找不到时回退全局。
# v2-m3 分数分布比 base 低，单独给 0.0。
RAG_RERANK_MIN_SCORE_PER_MODEL: dict[str, float] = {
    "BAAI/bge-reranker-v2-m3": float(os.getenv("RAG_RERANK_MIN_SCORE_V2_M3", "0.0")),
}


def min_rerank_score_for_model(model_name: str) -> float:
    """按 reranker 模型名返回 per-model 精排阈值；找不到则回退全局 RAG_RERANK_MIN_SCORE。"""
    if model_name in RAG_RERANK_MIN_SCORE_PER_MODEL:
        return RAG_RERANK_MIN_SCORE_PER_MODEL[model_name]
    return RAG_RERANK_MIN_SCORE

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
# BM25 索引目录（bm25_<collection>.pkl）；留空则回落到 CHROMA_DB_PATH 同目录
BM25_INDEX_DIR: str = os.getenv("BM25_INDEX_DIR", "./db/bm25")

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
# query 字符数 < 该值时跳过 multi-query / HyDE 改写（短/精确 query 改写收益低且费时）；0 表示不跳过
RAG_REWRITE_MIN_QUERY_LEN: int = int(os.getenv("RAG_REWRITE_MIN_QUERY_LEN", "6"))
# 开启 HyDE：让 LLM 先产出"假设性答案"，把答案也作为 embedding 检索 query；
# 适合 query 与文档词汇分布差异大的场景（口语 → 文档术语），但每轮多花 1 次 LLM 调用，默认关。
RAG_HYDE_ENABLED: bool = os.getenv("RAG_HYDE_ENABLED", "false").lower() == "true"

# Iter-5：跨语言翻译轴。开启后 expand_queries 会探测原 query 语种（zh/en），
# 让 LLM 翻译成另一种语言再追加进检索 query 列表。
# 解决场景："用中文问 3GPP 术语，英文文档库 dense 命中差 / BM25 跨语言失效"。
# 每次查询多 1 次 LLM 调用，但对中英混合知识库收益显著；翻译失败静默降级。
RAG_TRANSLATE_QUERY_ENABLED: bool = os.getenv("RAG_TRANSLATE_QUERY_ENABLED", "true").lower() == "true"

# ── Extended Thinking 配置 ────────────────────────────────────────────────────
# true 开启 Extended Thinking；支持的 provider 见各 ProviderConfig.thinking，其余静默降级
THINKING_ENABLED: bool = os.getenv("THINKING_ENABLED", "false").lower() == "true"
# thinking budget_tokens — 推荐：简单推理 1024~3000，复杂分析 8000~16000，AI Agent 32000+
# （仅 claude 与 qwen 真正消费此值；其余 reasoning provider 不支持设 budget，忽略）
THINKING_BUDGET: int = int(os.getenv("THINKING_BUDGET", "8000"))

# ── 跨 session 用户记忆配置 ──────────────────────────────────────────────────
# true 开启跨 session 记忆功能；false 完全禁用（不读取也不写入）
USER_MEMORY_ENABLED: bool = os.getenv("USER_MEMORY_ENABLED", "false").lower() == "true"
# 用户记忆 SQLite 数据库路径（与对话历史独立存储）
USER_MEMORY_DB_PATH: str = os.getenv("USER_MEMORY_DB_PATH", "./db/sqlite/user_memory.db")
# 注入 system prompt 的记忆文本最大字符数（防止占用过多 context）
USER_MEMORY_MAX_CHARS: int = int(os.getenv("USER_MEMORY_MAX_CHARS", "1500"))
# true 每次对话结束后自动提取记忆（每轮额外一次 LLM 调用，默认关闭需手动开启）
USER_MEMORY_AUTO_EXTRACT: bool = os.getenv("USER_MEMORY_AUTO_EXTRACT", "false").lower() == "true"
# 自动提取触发频率：每 N 轮 user 消息才触发一次（显式触发"请记住"不受此限）
USER_MEMORY_EXTRACT_EVERY_N: int = int(os.getenv("USER_MEMORY_EXTRACT_EVERY_N", "5"))

# ── 学习计划配置 ────────────────────────────────────────────────
# 学习计划 SQLite 数据库路径（与对话历史 / 用户记忆独立存储，便于单独 backup / migration）
LEARNING_PLAN_DB_PATH: str = os.getenv("LEARNING_PLAN_DB_PATH", "./db/sqlite/learning.db")
# 注入 system prompt 的 active 学习计划文本最大字符数（超出截断）
LEARNING_PLAN_MAX_INJECT_CHARS: int = int(os.getenv("LEARNING_PLAN_MAX_INJECT_CHARS", "1500"))

# ── Quiz 出题配置 ───────────────────────────────────────────────
# Quiz SQLite 路径（独立文件，便于单独 backup / migration）
QUIZ_DB_PATH: str = os.getenv("QUIZ_DB_PATH", "./db/sqlite/quiz.db")
# create_quiz 默认题数（未传 num_questions 时使用；可选值 5-15）
QUIZ_DEFAULT_NUM_QUESTIONS: int = int(os.getenv("QUIZ_DEFAULT_NUM_QUESTIONS", "10"))
# /quiz list / query_quiz_history 默认返回条数上限
QUIZ_HISTORY_LIST_LIMIT: int = int(os.getenv("QUIZ_HISTORY_LIST_LIMIT", "20"))

# ── SRS 主动复习配置 ────────────────────────────────────────────
# SRS SQLite 路径（独立文件，单表 srs_cards）
SRS_DB_PATH: str = os.getenv("SRS_DB_PATH", "./db/sqlite/srs.db")
# /srs due / query_srs_due 默认返回条数上限
SRS_DEFAULT_DUE_QUERY_LIMIT: int = int(os.getenv("SRS_DEFAULT_DUE_QUERY_LIMIT", "20"))
# SM-2 算法：repetitions=1 时的 interval（首次复习答对的下次回炉天数）
SRS_FIRST_INTERVAL_DAYS: int = int(os.getenv("SRS_FIRST_INTERVAL_DAYS", "1"))
# SM-2 算法：repetitions=2 时的 interval（第二次复习答对的下次回炉天数）
SRS_SECOND_INTERVAL_DAYS: int = int(os.getenv("SRS_SECOND_INTERVAL_DAYS", "6"))

# ── Critic 自检配置 ────────────────────────────────────────────
# 是否对 grade_quiz 批改结果做自检（可选值：true / false）
CRITIC_QUIZ_ENABLED: bool = os.getenv("CRITIC_QUIZ_ENABLED", "true").lower() == "true"
# 是否对 search_knowledge 召回片段做相关性自检（可选值：true / false）；
# 开启会多 1 次 LLM 调用（超时阈值见 CRITIC_LLM_TIMEOUT_SEC），默认关以降低召回延迟
CRITIC_RAG_ENABLED: bool = os.getenv("CRITIC_RAG_ENABLED", "false").lower() == "true"
# critic 单次 LLM 调用超时（秒），超时静默降级
CRITIC_LLM_TIMEOUT_SEC: float = float(os.getenv("CRITIC_LLM_TIMEOUT_SEC", "15"))
# Q1 quiz 批改自检阈值（critic 总分 < 该值标 critic_flagged，0-5 分）
CRITIC_GRADING_THRESHOLD: float = float(os.getenv("CRITIC_GRADING_THRESHOLD", "3.5"))
# 自动提取到点后，最近窗口里需至少有一条 ≥此字符数的 user 消息才触发（整窗都是寒暄则跳过；设为 0 禁用此过滤）
USER_MEMORY_EXTRACT_MIN_INPUT_LEN: int = int(os.getenv("USER_MEMORY_EXTRACT_MIN_INPUT_LEN", "20"))
# 记忆总条数软上限（提示 LLM 合并时控制规模，超出时合并 / 删除最旧条目）
USER_MEMORY_MAX_ENTRIES: int = int(os.getenv("USER_MEMORY_MAX_ENTRIES", "30"))

# 是否启用用户 rules 注入（每用户一份，存数据库；可选值：true / false）
USER_RULES_ENABLED: bool = os.getenv("USER_RULES_ENABLED", "true").lower() == "true"

# 单用户 rules 文本最大字符数；写入超出此值的 PUT /api/rules 返回 400，防止挤占 context
USER_RULES_MAX_CHARS: int = int(os.getenv("USER_RULES_MAX_CHARS", "4000"))

# Skills 禁用列表文件路径（相对项目根；文件不存在视作"未禁用任何 skill"）
SKILLS_DISABLED_FILE: str = os.getenv("SKILLS_DISABLED_FILE", ".agenta/skills/disabled.json")


# ── 每请求 LLM 偏好覆盖（多用户隔离）─────────────────────────────────────────
# Web 层处理某用户请求时用 use_llm_prefs(...) 把该用户选的模型 / thinking 压进
# contextvar；provider 与 agent 在本请求内读到的就是这个用户的偏好，互不干扰。
# CLI / 未设置时回落到下面的全局默认（ACTIVE_MODEL / THINKING_*）。
_MODEL_OVERRIDE: ContextVar[str | None] = ContextVar("llm_model_override", default=None)
_THINKING_OVERRIDE: ContextVar["tuple[bool, int] | None"] = ContextVar(
    "llm_thinking_override", default=None
)


def current_active_model() -> str:
    """当前生效的 model id：优先本请求覆盖，否则全局 ACTIVE_MODEL。"""
    return _MODEL_OVERRIDE.get() or ACTIVE_MODEL


def current_thinking_override() -> "tuple[bool, int] | None":
    """本请求的 thinking 覆盖 (enabled, budget)；未设置返回 None（调用方回落默认）。"""
    return _THINKING_OVERRIDE.get()


@contextmanager
def use_llm_prefs(
    active_model: str, thinking_enabled: bool, thinking_budget: int
) -> Iterator[None]:
    """在 with 块内把当前请求的模型 / thinking 偏好压进 contextvar，退出复位。

    三个参数均为已算好的生效值（调用方负责把"用户设置 or 全局默认"合并好）。
    contextvar 不随线程池传播，须在实际执行 agent 的线程内进入本上下文。
    """
    m_token = _MODEL_OVERRIDE.set(active_model)
    t_token = _THINKING_OVERRIDE.set((bool(thinking_enabled), int(thinking_budget)))
    try:
        yield
    finally:
        _MODEL_OVERRIDE.reset(m_token)
        _THINKING_OVERRIDE.reset(t_token)


def get_active_model() -> "tuple[ProviderConfig, ModelConfig]":
    """返回当前激活模型的 (厂商连接配置, 模型配置)，不存在则抛异常。

    model id 取 `current_active_model()`：本请求有覆盖用覆盖，否则全局 ACTIVE_MODEL。
    """
    model_id = current_active_model()
    model = MODEL_CONFIGS.get(model_id)
    if model is None:
        supported = ", ".join(MODEL_CONFIGS.keys())
        raise ValueError(
            f"不支持的 ACTIVE_MODEL: '{model_id}'，支持的值为: {supported}"
        )
    provider = PROVIDER_CONFIGS.get(model.provider)
    if provider is None:
        raise ValueError(
            f"模型 '{model_id}' 指向未知厂商 '{model.provider}'"
        )
    return provider, model

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

# logger 输出级别（可选值：DEBUG / INFO / WARNING / ERROR / CRITICAL）
# 同时应用于终端 stderr 输出与日志文件；非法值降级 INFO 并 warn
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# CLI 终端输出落盘模式（可选值：NONE / SINGLE / MULTI；大小写不敏感）
# NONE   不写文件
# SINGLE 固定 ./logs/agenta.log，跨启动追加（append）
# MULTI  每次启动新建 ./logs/agenta-YYYYMMDD-HHMMSS.log（write 覆盖）
# 非法值降级 NONE 并 warn
CLI_LOG_MODE: str = os.getenv("CLI_LOG_MODE", "NONE").upper()

# 单个日志文件大小上限（字节），超过即滚动成新文件；默认 5MB。0 表示不限
LOG_MAX_BYTES: int = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))

# 日志滚动 / 归档保留的备份份数（不含当前文件）；默认 3
LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "3"))

# ── 防 prompt injection 配置 ──────────────────────────────────────
# tool 名单门工作模式（可选值：normal / strict）
# normal：fail-open + TOOL_BLOCKLIST，不在黑名单即放行
# strict：fail-close + TOOL_ALLOWLIST，必须在白名单才放行（空白名单 = 全拒）
SECURITY_MODE: str = os.getenv("SECURITY_MODE", "normal")
# tool 黑名单（normal 模式生效，逗号分隔；如 "fetch_url,web_search"）
TOOL_BLOCKLIST: str = os.getenv("TOOL_BLOCKLIST", "")
# tool 白名单（仅 strict 模式生效，逗号分隔；如 "search_knowledge,make_plan"）
TOOL_ALLOWLIST: str = os.getenv("TOOL_ALLOWLIST", "")
# 是否启用 plan-execute 用户审批 mode（可选值：true / false）
# 开启后 LLM 调 make_plan 后 CLI 弹 yes/no 提问；no → 当前 query 中止
PLAN_PERMISSION_MODE: bool = os.getenv("PLAN_PERMISSION_MODE", "false").lower() == "true"

# ── MCP（Model Context Protocol）接入配置 ────────────────────────
# 是否启用 MCP 接入（可选值：true / false）
# false 时跳过 MCP 初始化；true 但配置文件不存在/为空仍静默跳过
MCP_ENABLED: bool = os.getenv("MCP_ENABLED", "true").lower() == "true"

# MCP server 配置文件路径（相对项目根；文件不存在静默跳过）
MCP_CONFIG_FILE: str = os.getenv("MCP_CONFIG_FILE", ".agenta/mcp/config.json")

# MCP server 禁用列表文件路径（JSON 数组；文件不存在视作"未禁用任何 server"）
MCP_DISABLED_FILE: str = os.getenv("MCP_DISABLED_FILE", ".agenta/mcp/disabled.json")

# server 启动 + initialize 握手单步超时（秒，整数）
MCP_CONNECT_TIMEOUT_SEC: int = int(os.getenv("MCP_CONNECT_TIMEOUT_SEC", "10"))

# 单次 tools/call 调用超时（秒，整数）
MCP_CALL_TIMEOUT_SEC: int = int(os.getenv("MCP_CALL_TIMEOUT_SEC", "30"))

# ── UT 测试专用 ──────────────────────────────────────────────
# UT 跑真实 LLM 调用（integration 档）时用的 model id；空则回落 ACTIVE_MODEL。
# 用于把测试统一指到一个便宜 / 快的模型，避免动用生产默认模型（取值见 MODEL_CONFIGS 的 key）
UT_LLM_MODEL: str = os.getenv("UT_LLM_MODEL", "")


def resolve_ut_llm_model() -> str:
    """解析 UT 真实 LLM 测试该用的 model id：UT_LLM_MODEL 合法则用它，否则回落 ACTIVE_MODEL。"""
    model = UT_LLM_MODEL or ACTIVE_MODEL
    return model if model in MODEL_CONFIGS else ACTIVE_MODEL
