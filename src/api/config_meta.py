"""Config 编辑面板 metadata registry。

每个 ConfigItem 描述一个可编辑配置项的元数据：
  - key：对应 src.config 模块的属性名（写回时的 setattr target）
  - group：UI 分组（llm / rag / memory / rules / mcp / security / web / log）
  - type：值类型 → 决定前端控件
  - brief / detail：UI 简要说明 / Tooltip 详细说明
  - min / max / options：校验
  - side_effect_hint / danger：UI 副作用提示 / 二次确认

前端按 group + type 渲染控件；后端按 type / range / options 校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

import src.config as _cfg


class ItemType(str, Enum):
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    PATH = "path"
    ENUM_STR = "enum_str"
    MULTI_ENUM_STR = "multi_enum_str"


@dataclass(frozen=True)
class ConfigItem:
    key: str
    group: str
    type: ItemType
    brief: str
    detail: str
    min: float | None = None
    max: float | None = None
    options: tuple[str, ...] | None = None
    side_effect_hint: str | None = None
    danger: bool = False
    editable: bool = True
    options_provider: Callable[[], list[str]] | None = None

    def resolve_options(self) -> list[str] | None:
        if self.options_provider is not None:
            return list(self.options_provider())
        if self.options is not None:
            return list(self.options)
        return None


REGISTRY: list[ConfigItem] = [
    # ─── LLM ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="ACTIVE_PROVIDER",
        group="llm",
        type=ItemType.ENUM_STR,
        brief="LLM Provider",
        detail="当前激活的 LLM provider；切换后下一次调用立即生效，无需重启。",
        options_provider=lambda: sorted(_cfg.PROVIDER_CONFIGS.keys()),
    ),
    ConfigItem(
        key="THINKING_ENABLED",
        group="llm",
        type=ItemType.BOOL,
        brief="Extended Thinking",
        detail="开启后让模型先 reasoning 再答；仅 Claude / Qwen3 实际生效，其他 provider 静默降级。",
    ),
    ConfigItem(
        key="THINKING_BUDGET",
        group="llm",
        type=ItemType.INT,
        brief="Thinking Budget",
        detail="thinking 阶段最多用多少 tokens。简单推理 1024~3000；复杂分析 8000~16000；Agent 32000+。",
        min=512,
        max=64000,
    ),
    ConfigItem(
        key="THINKING_ADAPTIVE",
        group="llm",
        type=ItemType.BOOL,
        brief="Adaptive Thinking",
        detail="开启后每次推理前自动估算合适的 budget，而非用固定值；仅在 THINKING_ENABLED=true 时生效。",
    ),
    # ─── RAG ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="RAG_TOP_K",
        group="rag",
        type=ItemType.INT,
        brief="检索 top_k",
        detail="RAG 检索返回的最大文档片段数；上调可提高召回但增加 LLM 上下文消耗。",
        min=1,
        max=50,
    ),
    ConfigItem(
        key="RAG_K_PER_SOURCE",
        group="rag",
        type=ItemType.INT,
        brief="单文件最多 chunk",
        detail="同一来源文件最多保留几条 chunk，避免长文档霸屏；0 = 不去重。",
        min=0,
        max=20,
    ),
    ConfigItem(
        key="RAG_ACTIVE_EMBEDDINGS",
        group="rag",
        type=ItemType.MULTI_ENUM_STR,
        brief="启用的 embedding 模型",
        detail="检索时同时查询哪几个 collection。多选可跨语言 / 模型联合召回（RRF 融合）。",
        options=("en", "zh", "m3"),
        side_effect_hint="首次切到新 alias 会触发 embedding 模型加载（几秒）",
    ),
    ConfigItem(
        key="DEFAULT_EMBEDDING_ALIAS",
        group="rag",
        type=ItemType.ENUM_STR,
        brief="默认 embedding",
        detail="未指定 alias 时使用的 embedding；新入库文档默认用此模型切片向量化。",
        options=("en", "zh", "m3"),
    ),
    ConfigItem(
        key="RERANKER_ENABLED",
        group="rag",
        type=ItemType.BOOL,
        brief="Reranker 精排",
        detail="开启后用 Cross-Encoder 对 Bi-Encoder 召回结果二阶段精排，提高最终相关性。",
    ),
    ConfigItem(
        key="RERANKER_MODEL",
        group="rag",
        type=ItemType.STRING,
        brief="Reranker 模型",
        detail="Cross-Encoder 模型名。中英推荐 BAAI/bge-reranker-base；多语言换 BAAI/bge-reranker-v2-m3。",
        side_effect_hint="切换后第一次检索会重新加载模型（几秒）",
    ),
    ConfigItem(
        key="RAG_QUERY_REWRITE_ENABLED",
        group="rag",
        type=ItemType.BOOL,
        brief="Query 改写",
        detail="开启后 LLM 对用户 query 生成多条同义改写一起检索，命中率更高，但每次多 1 次 LLM 调用。",
    ),
    ConfigItem(
        key="RAG_OCR_FALLBACK_ENABLED",
        group="rag",
        type=ItemType.BOOL,
        brief="PDF OCR 兜底",
        detail="扫描版 PDF 文本层提取失败时自动用 OCR；需安装 rapidocr-onnxruntime + pymupdf，否则静默禁用。",
    ),
    ConfigItem(
        key="CHUNK_SIZE",
        group="rag",
        type=ItemType.INT,
        brief="Chunk size",
        detail="入库时文本切片字符数。",
        min=100,
        max=4000,
        side_effect_hint="仅影响新入库文档；已入库的 chunk 不会重切",
    ),
    ConfigItem(
        key="CHUNK_OVERLAP",
        group="rag",
        type=ItemType.INT,
        brief="Chunk overlap",
        detail="相邻 chunk 重叠字符数，保留语义边界。",
        min=0,
        max=2000,
        side_effect_hint="仅影响新入库文档",
    ),
    # ─── Memory ───────────────────────────────────────────────────────────
    ConfigItem(
        key="USER_MEMORY_ENABLED",
        group="memory",
        type=ItemType.BOOL,
        brief="跨 session 记忆",
        detail="开启后 Agent 会把用户偏好 / 背景 / 指令等持久化到独立 SQLite，跨 session 复用。",
    ),
    ConfigItem(
        key="USER_MEMORY_AUTO_EXTRACT",
        group="memory",
        type=ItemType.BOOL,
        brief="自动提取记忆",
        detail="每 N 轮对话结束后让 LLM 自动从对话提取记忆；额外 LLM 调用，默认关。",
    ),
    ConfigItem(
        key="USER_MEMORY_MAX_CHARS",
        group="memory",
        type=ItemType.INT,
        brief="注入字符上限",
        detail="注入到 system prompt 的记忆文本最大字符数；超出截断，防止占用过多 context。",
        min=100,
        max=10000,
    ),
    # ─── Rules ────────────────────────────────────────────────────────────
    ConfigItem(
        key="USER_RULES_ENABLED",
        group="rules",
        type=ItemType.BOOL,
        brief="项目 Rules",
        detail="开启后启动时把 .agenta/rules.md 注入 system prompt 的 <project_rules> 块。",
    ),
    ConfigItem(
        key="USER_RULES_FILE",
        group="rules",
        type=ItemType.PATH,
        brief="Rules 文件路径",
        detail="相对项目根；文件不存在时静默跳过。",
    ),
    ConfigItem(
        key="USER_RULES_MAX_CHARS",
        group="rules",
        type=ItemType.INT,
        brief="注入字符上限",
        detail="rules 文本注入字符数上限；超出截断。",
        min=100,
        max=20000,
    ),
    # ─── MCP ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="MCP_ENABLED",
        group="mcp",
        type=ItemType.BOOL,
        brief="启用 MCP",
        detail="启用 Model Context Protocol 接入；false 时跳过 MCP 初始化。",
        side_effect_hint="切换会触发 MCP manager start_all / stop_all",
    ),
    ConfigItem(
        key="MCP_CONFIG_FILE",
        group="mcp",
        type=ItemType.PATH,
        brief="MCP 配置文件",
        detail="相对项目根；文件不存在或为空时静默跳过。",
        side_effect_hint="切换路径会停 当前所有 server + 加载新配置重连",
    ),
    ConfigItem(
        key="MCP_CONNECT_TIMEOUT_SEC",
        group="mcp",
        type=ItemType.INT,
        brief="握手超时",
        detail="server 启动 + initialize 单步超时（秒）。",
        min=1,
        max=60,
    ),
    ConfigItem(
        key="MCP_CALL_TIMEOUT_SEC",
        group="mcp",
        type=ItemType.INT,
        brief="调用超时",
        detail="单次 tools/call 调用超时（秒）。",
        min=1,
        max=300,
    ),
    # ─── Security ─────────────────────────────────────────────────────────
    ConfigItem(
        key="SECURITY_MODE",
        group="security",
        type=ItemType.ENUM_STR,
        brief="安全模式",
        detail="normal=fail-open + 黑名单；strict=fail-close + 白名单（白名单空时全拒）。",
        options=("normal", "strict"),
        danger=True,
    ),
    ConfigItem(
        key="PLAN_PERMISSION_MODE",
        group="security",
        type=ItemType.BOOL,
        brief="Plan 用户审批",
        detail="开启后 LLM 调 make_plan 时弹 yes/no 提问；no 中止本次 query。",
        danger=True,
    ),
    # ─── Web ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="WEB_UPLOAD_DIR",
        group="web",
        type=ItemType.PATH,
        brief="上传落盘目录",
        detail="拖拽上传的文件保存位置；相对项目根。",
        side_effect_hint="仅影响新上传，已上传文件留在原目录",
    ),
    ConfigItem(
        key="WEB_MAX_UPLOAD_MB",
        group="web",
        type=ItemType.INT,
        brief="单次上传上限 (MB)",
        detail="单次上传文件大小上限；超限返回 413。",
        min=1,
        max=500,
    ),
    # ─── Log ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="LOG_LEVEL",
        group="log",
        type=ItemType.ENUM_STR,
        brief="日志级别",
        detail="logger 输出级别，立即生效。",
        options=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    ),
    # ─── UI ───────────────────────────────────────────────────────────────
    ConfigItem(
        key="USER_DISPLAY_NAME",
        group="ui",
        type=ItemType.STRING,
        brief="显示用户名",
        detail="聊天空状态欢迎语「下午好，<名字>」里显示的名字；多用户支持前用固定值。",
    ),
]


GROUP_LABELS: dict[str, str] = {
    "llm": "LLM",
    "rag": "RAG",
    "memory": "Memory",
    "rules": "Rules",
    "mcp": "MCP",
    "security": "Security",
    "web": "Web",
    "log": "Log",
    "ui": "界面",
}


def get_item(key: str) -> ConfigItem | None:
    for item in REGISTRY:
        if item.key == key:
            return item
    return None


def validate_value(item: ConfigItem, value: Any) -> Any:
    """按 type / range / options 校验，返回规范化后的值；失败 raise ValueError。"""
    t = item.type
    if t == ItemType.BOOL:
        if not isinstance(value, bool):
            raise ValueError(f"{item.key} 需要 bool 类型，收到 {type(value).__name__}")
        return value
    if t == ItemType.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{item.key} 需要 int 类型")
        if item.min is not None and value < item.min:
            raise ValueError(f"{item.key} 不能小于 {int(item.min)}")
        if item.max is not None and value > item.max:
            raise ValueError(f"{item.key} 不能大于 {int(item.max)}")
        return value
    if t == ItemType.FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{item.key} 需要数字类型")
        v = float(value)
        if item.min is not None and v < item.min:
            raise ValueError(f"{item.key} 不能小于 {item.min}")
        if item.max is not None and v > item.max:
            raise ValueError(f"{item.key} 不能大于 {item.max}")
        return v
    if t in (ItemType.STRING, ItemType.PATH):
        if not isinstance(value, str):
            raise ValueError(f"{item.key} 需要 str 类型")
        return value
    if t == ItemType.ENUM_STR:
        if not isinstance(value, str):
            raise ValueError(f"{item.key} 需要 str 类型")
        opts = item.resolve_options() or []
        if value not in opts:
            raise ValueError(f"{item.key} 取值必须在 {opts} 中")
        return value
    if t == ItemType.MULTI_ENUM_STR:
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{item.key} 需要 str 列表")
        opts = set(item.resolve_options() or [])
        bad = [v for v in value if v not in opts]
        if bad:
            raise ValueError(f"{item.key} 包含非法值 {bad}")
        return list(value)
    raise ValueError(f"{item.key} 未知类型 {t}")
