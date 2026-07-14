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
    # 组内子分区标题（同一 section 的项在 UI 里框在一起）；None = 不分区
    section: str | None = None
    min: float | None = None
    max: float | None = None
    options: tuple[str, ...] | None = None
    side_effect_hint: str | None = None
    danger: bool = False
    editable: bool = True
    # 仍参与 /api/config 读写，但不在设置面板渲染 —— 避免与聊天页控件重复
    hidden: bool = False
    options_provider: Callable[[], list[str]] | None = None

    def resolve_options(self) -> list[str] | None:
        if self.options_provider is not None:
            return list(self.options_provider())
        if self.options is not None:
            return list(self.options)
        return None


def _judge_model_options() -> list[str]:
    """评委模型可选项：空（=跟随回答模型）+「降本」组路由候选池的可用模型。

    懒加载 model_router 避免 import 期循环依赖。
    """
    from src.llm.model_router import effective_pool

    return [""] + sorted(effective_pool())


def _classifier_model_options() -> list[str]:
    """难度分类器模型可选项：空（=不调分类器）+ 路由候选池的可用模型。"""
    from src.llm.model_router import effective_pool

    return [""] + sorted(effective_pool())


REGISTRY: list[ConfigItem] = [
    # ─── LLM ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="LLM_PROXY",
        group="llm",
        type=ItemType.STRING,
        brief="HTTP 代理",
        detail="国外 provider（openai / grok / claude / gemini）走的 HTTP 代理，国内始终直连；留空不走代理（已开 VPN 可留空）。格式 http://host:port",
    ),
    # 以下 3 项是 LLM 偏好的全局默认：聊天页 Composer 走 per-用户 /api/auth/llm-prefs，
    # 各用户自选、下一轮生效；未设置时才回落到这里的全局值。全局值只允许改 .env，
    # 不开放 UI/API 改（editable=False → PATCH/reset 返回 404，override 文件里再出现也不应用）。
    # 仍留在注册表里只为 /api/config 能读到它们当前值（hidden 不在设置面板渲染）。
    ConfigItem(
        key="ACTIVE_MODEL",
        group="llm",
        type=ItemType.ENUM_STR,
        brief="LLM 模型",
        detail="当前激活的模型（厂商从模型反推）；切换后下一次调用立即生效，无需重启。",
        options_provider=lambda: sorted(_cfg.MODEL_CONFIGS.keys()),
        editable=False,
        hidden=True,
    ),
    ConfigItem(
        key="THINKING_ENABLED",
        group="llm",
        type=ItemType.BOOL,
        brief="Extended Thinking",
        detail="开启后让模型先 reasoning 再答；支持 Claude / qwen / kimi / deepseek / glm / minimax，其他 provider 静默降级。",
        editable=False,
        hidden=True,
    ),
    ConfigItem(
        key="THINKING_BUDGET",
        group="llm",
        type=ItemType.INT,
        brief="Thinking Budget",
        detail="thinking 阶段最多用多少 tokens（仅 Claude / qwen 消费此值，其余忽略）。简单 1024~3000；复杂 8000~16000；Agent 32000+。",
        min=512,
        max=64000,
        editable=False,
        hidden=True,
    ),
    ConfigItem(
        key="MAX_TOOL_ROUNDS",
        group="llm",
        type=ItemType.INT,
        brief="工具调用最大次数",
        detail="单次问答调用工具最大次数。",
        min=1,
        max=50,
    ),
    ConfigItem(
        key="MAX_TOTAL_ROUNDS",
        group="llm",
        type=ItemType.INT,
        brief="总推理次数",
        detail="单次问答的总推理最大次数。",
        min=1,
        max=60,
    ),
    ConfigItem(
        key="MAX_HARD_CAP_ROUNDS",
        group="llm",
        type=ItemType.INT,
        brief="最大推理次数",
        detail="plan 模式下自适应放大后的最大推理次数。",
        min=1,
        max=200,
    ),
    ConfigItem(
        key="IMP_METHOD",
        group="llm",
        type=ItemType.ENUM_STR,
        brief="Agent 实现",
        detail="底层 Agent 实现。PYTHON 最稳、支持多用户并发；LANGCHAIN / AUTOGPT 未做并发隔离，仅建议单用户。",
        options=("PYTHON", "LANGCHAIN", "AUTOGPT"),
        side_effect_hint="改后下一次对话即按新实现重建，无需重启",
    ),
    # ─── RAG ──────────────────────────────────────────────────────────────
    # —— 索引与切块（文档入库阶段）——
    ConfigItem(
        key="DEFAULT_EMBEDDING_ALIAS",
        group="rag",
        section="索引与切块",
        type=ItemType.ENUM_STR,
        brief="默认 embedding",
        detail="新入库文档默认用的 embedding 模型。选 api-m3 表示 m3 走硅基流动云端（与本地 m3 共用 kb_m3、免重灌，需配 SiliconFlow key）；同时决定检索里 m3 走本地还是云端。",
        options=("en", "zh", "m3", "api-m3"),
        side_effect_hint="切换后下一次入库 / 检索即生效，无需重启",
    ),
    ConfigItem(
        key="CHUNK_SIZE",
        group="rag",
        section="索引与切块",
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
        section="索引与切块",
        type=ItemType.INT,
        brief="Chunk overlap",
        detail="相邻 chunk 重叠字符数，保留语义边界。",
        min=0,
        max=2000,
        side_effect_hint="仅影响新入库文档",
    ),
    ConfigItem(
        key="RAG_OCR_FALLBACK_ENABLED",
        group="rag",
        section="索引与切块",
        type=ItemType.BOOL,
        brief="PDF OCR 兜底",
        detail="扫描版 PDF 提取失败时自动用 OCR（需装 rapidocr-onnxruntime + pymupdf）。",
    ),
    # —— 召回（向量 + BM25 检索）——
    ConfigItem(
        key="RAG_ACTIVE_EMBEDDINGS",
        group="rag",
        section="召回",
        type=ItemType.MULTI_ENUM_STR,
        brief="启用的 embedding 模型",
        detail="检索时同时查询哪几个 embedding；多选可跨语言联合召回。其中 m3 走本地还是云端跟随「默认 embedding」（选 api-m3 即云端）。",
        options=("en", "zh", "m3"),
        side_effect_hint="首次切到新 alias 会触发 embedding 模型加载（几秒）",
    ),
    ConfigItem(
        key="RAG_TOP_K",
        group="rag",
        section="召回",
        type=ItemType.INT,
        brief="检索 top_k",
        detail="检索返回的最大片段数；越大召回越全，但更费上下文。",
        min=1,
        max=50,
    ),
    ConfigItem(
        key="RAG_K_PER_SOURCE",
        group="rag",
        section="召回",
        type=ItemType.INT,
        brief="单文件最多 chunk",
        detail="同一文件最多保留几条片段，避免长文档霸屏；0 = 不限制。",
        min=0,
        max=20,
    ),
    # —— Query 改写（检索前扩展 query）——
    ConfigItem(
        key="RAG_QUERY_REWRITE_ENABLED",
        group="rag",
        section="Query 改写",
        type=ItemType.BOOL,
        brief="Multi-Query 改写",
        detail="对问题生成多条同义改写一起检索，提升命中率；每次多 1 次 LLM 调用。",
    ),
    ConfigItem(
        key="RAG_REWRITE_MAX_QUERIES",
        group="rag",
        section="Query 改写",
        type=ItemType.INT,
        brief="改写条数上限",
        detail="Multi-Query 单次最多生成几条改写（不含原问题）。",
        min=1,
        max=5,
    ),
    ConfigItem(
        key="RAG_HYDE_ENABLED",
        group="rag",
        section="Query 改写",
        type=ItemType.BOOL,
        brief="HyDE 改写",
        detail="先让 LLM 生成假设答案作为额外检索 query（HyDE），适合口语转术语；每次多 1 次 LLM 调用。",
    ),
    ConfigItem(
        key="RAG_TRANSLATE_QUERY_ENABLED",
        group="rag",
        section="Query 改写",
        type=ItemType.BOOL,
        brief="翻译轴改写",
        detail="把问题翻译成另一语言一起检索（中↔英），改善跨语种召回；每次多 1 次 LLM 调用。",
    ),
    ConfigItem(
        key="RAG_REWRITE_MIN_QUERY_LEN",
        group="rag",
        section="Query 改写",
        type=ItemType.INT,
        brief="改写最短 query",
        detail="问题短于该字符数时跳过改写（短术语改写收益低）；0 = 从不跳过。",
        min=0,
        max=50,
    ),
    # —— 精排（Cross-Encoder 二阶段重排）——
    ConfigItem(
        key="RERANKER_MODEL",
        group="rag",
        section="精排",
        type=ItemType.ENUM_STR,
        brief="精排模型",
        detail=(
            "精排模型："
            "disable=关闭；api:BAAI/bge-reranker-v2-m3=硅基流动云端；"
            "BAAI/bge-reranker-base=本地中英；BAAI/bge-reranker-v2-m3=本地多语言；"
            "cross-encoder/ms-marco-MiniLM-L-6-v2=本地英文轻量。"
        ),
        options=(
            "disable",
            "api:BAAI/bge-reranker-v2-m3",
            "BAAI/bge-reranker-base",
            "BAAI/bge-reranker-v2-m3",
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
        ),
        side_effect_hint="切换本地模型后第一次检索会重新加载（几秒）；api 与 disable 无需加载",
    ),
    ConfigItem(
        key="RERANKER_RECALL_MULTIPLIER",
        group="rag",
        section="精排",
        type=ItemType.INT,
        brief="精排召回倍数",
        detail="精排前取 top_k × 该倍数 条候选；调小更快、略降召回。",
        min=1,
        max=10,
    ),
    # —— 召回自检（LLM 相关性把关）——
    ConfigItem(
        key="CRITIC_RAG_ENABLED",
        group="rag",
        section="召回自检",
        type=ItemType.BOOL,
        brief="RAG 召回自检",
        detail="每次检索后用 LLM 自检召回片段相关性；多 1 次 LLM 调用，超时放行。",
    ),
    ConfigItem(
        key="CRITIC_LLM_TIMEOUT_SEC",
        group="rag",
        section="召回自检",
        type=ItemType.FLOAT,
        brief="自检超时(秒)",
        detail="RAG / Quiz 自检单次 LLM 调用超时（秒），超时放行。",
        min=1,
        max=60,
    ),
    # ─── Memory ───────────────────────────────────────────────────────────
    ConfigItem(
        key="USER_MEMORY_ENABLED",
        group="memory",
        type=ItemType.BOOL,
        brief="跨 session 记忆",
        detail="把用户偏好 / 背景等持久化，跨 session 复用。",
    ),
    ConfigItem(
        key="USER_MEMORY_AUTO_EXTRACT",
        group="memory",
        type=ItemType.BOOL,
        brief="自动提取记忆",
        detail="每隔几轮自动让 LLM 从对话提取记忆；额外 LLM 调用。",
    ),
    ConfigItem(
        key="USER_MEMORY_MAX_CHARS",
        group="memory",
        type=ItemType.INT,
        brief="注入字符上限",
        detail="注入对话的记忆文本最大字符数，超出截断。",
        min=100,
        max=10000,
    ),
    ConfigItem(
        key="USER_MEMORY_MAX_ENTRIES",
        group="memory",
        type=ItemType.INT,
        brief="记忆条数上限",
        detail="记忆条数软上限，超出时合并 / 删除最旧条目。",
        min=5,
        max=200,
    ),
    # ─── Rules ────────────────────────────────────────────────────────────
    ConfigItem(
        key="USER_RULES_ENABLED",
        group="rules",
        type=ItemType.BOOL,
        brief="用户 Rules",
        detail="每轮对话注入当前用户的 rules（在 Rules 页编辑）。",
    ),
    # ─── MCP ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="MCP_ENABLED",
        group="mcp",
        type=ItemType.BOOL,
        brief="启用 MCP",
        detail="启用 MCP（Model Context Protocol）接入。",
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
        section="工具名单门",
        options=("normal", "strict"),
        danger=True,
    ),
    ConfigItem(
        key="TOOL_BLOCKLIST",
        group="security",
        type=ItemType.STRING,
        brief="工具黑名单",
        detail="normal 模式下禁止调用的工具名，逗号分隔（如 fetch_url,web_search）；留空则不禁任何工具。strict 模式下此项不生效。",
        section="工具名单门",
    ),
    ConfigItem(
        key="TOOL_ALLOWLIST",
        group="security",
        type=ItemType.STRING,
        brief="工具白名单",
        detail="strict 模式下唯一允许调用的工具名，逗号分隔（如 search_knowledge,make_plan）；留空则全部拒绝。normal 模式下此项不生效。",
        section="工具名单门",
    ),
    ConfigItem(
        key="PLAN_PERMISSION_MODE",
        group="security",
        type=ItemType.BOOL,
        brief="Plan 用户审批",
        detail="LLM 制定 plan 前弹确认；拒绝则中止本次问答。",
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
        detail="单次上传文件大小上限。",
        min=1,
        max=500,
    ),
    ConfigItem(
        key="DOCX_MAX_UNZIP_MB",
        group="web",
        type=ItemType.INT,
        brief="DOCX 流式阈值 (MiB)",
        detail="DOCX 解压总量超过此值时改用流式逐段解析，不再整包载入 python-docx。",
        min=1,
        max=2048,
    ),
    ConfigItem(
        key="DOCX_HARD_MAX_UNZIP_MB",
        group="web",
        type=ItemType.INT,
        brief="DOCX 硬上限 (MiB)",
        detail="DOCX 解压总量硬上限，仅防 zip bomb；超过则拒绝解析。",
        min=1,
        max=4096,
    ),
    ConfigItem(
        key="DOCX_PARSE_MEMORY_MB",
        group="web",
        type=ItemType.INT,
        brief="DOCX 解析内存 (MiB)",
        detail="单个 DOCX 隔离解析子进程的内存上限。",
        min=128,
        max=4096,
    ),
    ConfigItem(
        key="DOCX_PARSE_TIMEOUT_SEC",
        group="web",
        type=ItemType.INT,
        brief="DOCX 解析超时 (秒)",
        detail="单个 DOCX 隔离解析允许执行的最长时间。",
        min=10,
        max=1800,
    ),
    ConfigItem(
        key="INGEST_MAX_CONCURRENT",
        group="web",
        type=ItemType.INT,
        brief="入库并发上限",
        detail="同时执行的入库任务数；多用户或大文件场景建议保持 1，避免内存打满。",
        min=1,
        max=8,
    ),
    ConfigItem(
        key="MAX_FETCH_BYTES",
        group="web",
        type=ItemType.INT,
        brief="网页抓取字节上限",
        detail="fetch_url 与 Jina Reader 下载响应体的最大字节数；超限立即中止，防止超大页面占满内存。",
        min=65536,
        max=33554432,
    ),
    ConfigItem(
        key="CHAT_MESSAGE_MAX_BYTES",
        group="web",
        type=ItemType.INT,
        brief="聊天消息字节上限",
        detail="单条聊天消息（含内嵌附件正文）的 UTF-8 最大字节数；前后端均应遵守，超限返回 413。",
        min=4096,
        max=8388608,
    ),
    ConfigItem(
        key="CHAT_ATTACHMENT_MAX_COUNT",
        group="web",
        type=ItemType.INT,
        brief="聊天附件数量上限",
        detail="单条消息最多附带的文件数（含图片与文本附件）。",
        min=1,
        max=20,
    ),
    ConfigItem(
        key="SSE_QUEUE_MAXSIZE",
        group="web",
        type=ItemType.INT,
        brief="SSE 队列容量",
        detail="流式聊天事件队列上限；慢客户端时满队列会丢弃可合并的 token/thinking 进度帧。",
        min=16,
        max=4096,
    ),
    ConfigItem(
        key="SSE_TOKEN_MERGE_MAX_CHARS",
        group="web",
        type=ItemType.INT,
        brief="SSE token 合并字符数",
        detail="token_chunk / thinking_chunk 累计到此字符数即合并下发，减轻前端渲染压力。",
        min=0,
        max=8192,
    ),
    ConfigItem(
        key="SSE_TOKEN_MERGE_INTERVAL_MS",
        group="web",
        type=ItemType.INT,
        brief="SSE token 合并间隔 (ms)",
        detail="距上次 flush 超过此毫秒数则下发已缓冲的 token/thinking；0 表示仅按字符数合并。",
        min=0,
        max=2000,
    ),
    ConfigItem(
        key="MAX_CONCURRENT_AGENT_RUNS",
        group="web",
        type=ItemType.INT,
        brief="Agent 并发上限",
        detail="同时在跑的 agent.run 数，超出排队；低内存 VPS 建议 1~2。",
        min=1,
        max=16,
    ),
    ConfigItem(
        key="BACKUP_MAX_UPLOAD_MB",
        group="web",
        type=ItemType.INT,
        brief="备份上传上限 (MiB)",
        detail="管理员通过 Web 上传还原备份 zip 的最大体积；超限返回 413。",
        min=1,
        max=4096,
    ),
    ConfigItem(
        key="BACKUP_MAX_UNZIP_MB",
        group="web",
        type=ItemType.INT,
        brief="备份解压上限 (MiB)",
        detail="还原前校验 zip 内全部成员解压后总大小；防 zip bomb，超限拒绝还原。",
        min=16,
        max=16384,
    ),
    ConfigItem(
        key="BACKUP_MAX_COMPRESSION_RATIO",
        group="web",
        type=ItemType.INT,
        brief="备份最大压缩比",
        detail="解压总大小除以 zip 文件大小的上限；异常高压缩比视为 zip bomb 并拒绝。",
        min=10,
        max=1000,
    ),
    ConfigItem(
        key="BACKUP_DIR",
        group="web",
        type=ItemType.PATH,
        brief="备份目录",
        detail="运行时数据备份 zip 的保存位置；相对项目根。备份含明文密钥，请勿放入公共网盘。",
        side_effect_hint="仅影响新备份，已生成的快照留在原目录",
    ),
    # ─── Deep Research ────────────────────────────────────────────────────
    ConfigItem(
        key="DEEP_RESEARCH_ENABLED",
        group="research",
        type=ItemType.BOOL,
        brief="启用深度研究",
        detail="开启后聊天页出现深度研究开关；关闭则隐藏开关，收到深度研究请求降级为普通对话。深度研究耗时数分钟、费更多 token，换一篇带引用的调研报告。",
    ),
    ConfigItem(
        key="DEEP_RESEARCH_MAX_SUBQUESTIONS",
        group="research",
        type=ItemType.INT,
        brief="子问题数上限",
        detail="规划阶段把问题拆成几个子问题（实际裁剪到 3~该值）；越多越全但越慢越费 token。",
        min=3,
        max=10,
    ),
    ConfigItem(
        key="DEEP_RESEARCH_MAX_PARALLEL_SUBAGENTS",
        group="research",
        type=ItemType.INT,
        brief="子代理并行数",
        detail="同时在跑的检索子代理数；放大成倍占用 LLM 配额，故封顶。",
        min=1,
        max=6,
    ),
    ConfigItem(
        key="DEEP_RESEARCH_SUBAGENT_MAX_ROUNDS",
        group="research",
        type=ItemType.INT,
        brief="子代理工具轮数",
        detail="单个子代理最多调几轮工具，到上限即就该子问题产出小结。",
        min=1,
        max=10,
    ),
    ConfigItem(
        key="DEEP_RESEARCH_MAX_SOURCES_PER_SUBAGENT",
        group="research",
        type=ItemType.INT,
        brief="单子代理来源上限",
        detail="单个子代理最多采纳几条来源（知识库 + web 合计），防单路检索失控。",
        min=1,
        max=15,
    ),
    ConfigItem(
        key="DEEP_RESEARCH_MAX_TOTAL_SOURCES",
        group="research",
        type=ItemType.INT,
        brief="总来源上限",
        detail="整次研究采纳的来源总数上限，防全局失控。",
        min=3,
        max=60,
    ),
    ConfigItem(
        key="DEEP_RESEARCH_REFLECT_ENABLED",
        group="research",
        type=ItemType.BOOL,
        brief="反思补查",
        detail="综述成稿前评估信息缺口，按需再补查 1 轮；提升完整度但多花时间与 token。",
    ),
    # ─── 评估 + 可观测 ────────────────────────────────────────────────────
    ConfigItem(
        key="TRACE_ENABLED",
        group="eval",
        section="会话监控",
        type=ItemType.BOOL,
        brief="采集对话 trace",
        detail="是否采集每次对话的分阶段耗时 / token 写入 usage.db，供会话监控看板展示；出错只记日志、不影响对话。",
    ),
    ConfigItem(
        key="EVAL_GOLDEN_USE_PENDING",
        group="eval",
        section="离线评估",
        type=ItemType.BOOL,
        brief="评估纳入待审 golden",
        detail="跑 RAG 评估时是否纳入未审核（pending）的 golden；默认只用已通过（approved）的。",
        side_effect_hint="下一次跑评估脚本时生效",
    ),
    ConfigItem(
        key="EVAL_JUDGE_MODEL",
        group="eval",
        section="离线评估",
        type=ItemType.ENUM_STR,
        brief="答案质量评委模型",
        detail="runner --llm 跑 faithfulness / 相关度时评委用的模型；留空=跟随回答模型。建议选与被评模型不同的，避免同模型自评偏高。只列出「模型选择」页的可用模型。",
        options_provider=_judge_model_options,
        side_effect_hint="下一次跑评估脚本时生效",
    ),
    # ─── Log ──────────────────────────────────────────────────────────────
    ConfigItem(
        key="LOG_LEVEL",
        group="log",
        type=ItemType.ENUM_STR,
        brief="日志级别",
        detail="日志输出级别。",
        options=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    ),
    # ─── 模型路由（降本） ───────────────────────────────────────────────────
    ConfigItem(
        key="MODEL_ROUTING_ENABLED",
        group="model_routing",
        type=ItemType.BOOL,
        brief="启用模型路由",
        detail="选 auto 档时按问题难度在候选池内向更便宜的模型降级（手选具体模型不路由）。关闭则始终用所选 / 默认模型。",
    ),
    ConfigItem(
        key="MODEL_ROUTING_MODE",
        group="model_routing",
        type=ItemType.ENUM_STR,
        brief="难度判定方式",
        detail="rule=关键词 / 长度规则（零开销）；classifier=调小模型打分（多一次小调用）；hybrid=规则拿不准时才调分类器。",
        options=("rule", "classifier", "hybrid"),
    ),
    ConfigItem(
        key="MODEL_ROUTING_CLASSIFIER_MODEL",
        group="model_routing",
        type=ItemType.ENUM_STR,
        brief="难度分类器模型",
        detail="classifier / hybrid 模式下给问题难度打分的小模型；留空=不调分类器（回落规则）。只列出候选池的可用模型。",
        options_provider=_classifier_model_options,
    ),
    # ─── 语义缓存（降本） ───────────────────────────────────────────────────
    ConfigItem(
        key="SEMANTIC_CACHE_ENABLED",
        group="semantic_cache",
        type=ItemType.BOOL,
        brief="启用语义缓存",
        detail="相近问法命中历史答案，跳过整次检索 + 生成；仅对单轮起步、无个性化、且只用纯检索（无联网 / 写操作）的问答生效。软失败。",
    ),
    ConfigItem(
        key="SEMANTIC_CACHE_THRESHOLD",
        group="semantic_cache",
        type=ItemType.FLOAT,
        brief="命中相似度阈值",
        detail="query 向量相似度 ≥ 此值才算命中。越高越严（误命中少、命中率低），越低越松。",
        min=0.0,
        max=1.0,
    ),
    ConfigItem(
        key="SEMANTIC_CACHE_TTL_DAYS",
        group="semantic_cache",
        type=ItemType.INT,
        brief="缓存过期天数",
        detail="缓存条目写入后多少天过期；过期条目查询时惰性删除、按未命中处理。",
        min=1,
        max=365,
    ),
    ConfigItem(
        key="SEMANTIC_CACHE_COLLECTION",
        group="semantic_cache",
        type=ItemType.STRING,
        brief="缓存 collection 名",
        detail="语义缓存用的 ChromaDB collection 名；改名会弃用旧缓存，属内部项。",
        hidden=True,
    ),
]


GROUP_LABELS: dict[str, str] = {
    "llm": "LLM",
    "model_routing": "模型路由",
    "semantic_cache": "语义缓存",
    "rag": "RAG",
    "memory": "Memory",
    "rules": "Rules",
    "mcp": "MCP",
    "security": "Security",
    "web": "Web",
    "research": "Deep Research",
    "eval": "评估",
    "log": "Log",
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
