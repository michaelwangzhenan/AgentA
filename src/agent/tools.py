"""
工具层 —— Agent 可调用的工具定义与执行

遵循 OpenAI Function Calling 格式，Agent 通过 LLM 的 tool_calls 决定调用哪个工具。

工具列表：
    - search_knowledge : 搜索私有知识库（ChromaDB 向量检索）
    - web_search       : 通过 Serper.dev 搜索互联网，返回真实 URL 列表及摘要
    - fetch_url        : 抓取网页正文；SPA 页面自动 fallback 到 Jina Reader
"""

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import requests
from bs4 import BeautifulSoup

import src.config as _cfg
from src.rag.retriever import search, format_search_results

if TYPE_CHECKING:
    # 仅用于类型注解；运行期不依赖，避免 retriever → agent.core 反向导入循环
    from src.agent.core.citation_builder import CitationBuilder  # noqa: F401

logger = logging.getLogger(__name__)

# 搜索返回结果上限
MAX_SEARCH_TOP_K: int = 10
# fetch_url HTTP 请求超时秒数
FETCH_URL_TIMEOUT: int = 15
# Jina Reader 额外超时（云端渲染 SPA 需要更长时间）
JINA_EXTRA_TIMEOUT: int = 20
# web_search 单次最大结果数上限
WEB_SEARCH_MAX_NUM: int = 10
# SPA 判定阈值：正文字符数低于此值时触发 Jina fallback
_SPA_MIN_CONTENT_CHARS: int = 200

# fetch_url 支持的文本类 MIME 类型前缀
_TEXT_TYPES: tuple[str, ...] = (
    "text/", "application/json", "application/xml", "application/xhtml"
)
# 常见二进制文件魔术字节 —— 匹配则跳过该 URL
_BINARY_MAGIC: tuple[bytes, ...] = (
    b"PK\x03\x04", b"%PDF", b"\x89PNG", b"GIF8", b"\xff\xd8\xff"
)


# ── 工具结果结构体 ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolResult:
    """
    工具执行结果的结构化封装。

    Attributes:
        status:  执行状态。
                   "ok"    — 成功且有有效内容
                   "empty" — 成功但无有效内容（知识库无命中 / 页面为空）
                   "error" — 执行失败（网络错误、未知工具等）
        content: 原始内容字符串，传给 LLM 前通过 to_llm_str() 格式化。
    """

    status: Literal["ok", "empty", "error"]
    content: str

    def to_llm_str(self) -> str:
        """返回带状态标签的格式化字符串，供 LLM 消费。"""
        match self.status:
            case "ok":
                return self.content
            case "empty":
                return f"[结果为空] {self.content}"
            case "error":
                return f"[工具失败] {self.content}"

# ── Skills 工具支持 ─────────────────────────────────────────────────────────

def get_tools(skill_bodies: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """
    返回当前可用的工具列表。

    永远包含：
      - 基础 3 tool（search_knowledge / web_search / fetch_url）
      - Phase 2.1 plan-execute 3 tool（make_plan / update_step / abort_plan）
      - Phase 2.2 学习计划业务 3 tool（create_study_plan / update_study_progress / query_study_status）
    若传入 skill_bodies，再追加 load_skill 工具定义（name 字段限定为已有名称枚举）。
    """
    tools = list(TOOLS) + list(_PLAN_TOOLS) + list(_STUDY_PLAN_TOOLS)
    if skill_bodies:
        tools.append(_build_load_skill_def(list(skill_bodies.keys())))
    return tools


def _build_load_skill_def(skill_names: list[str]) -> dict[str, Any]:
    """构建 load_skill 工具的 JSON Schema，name 参数使用 enum 约束防止幻觉。"""
    return {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "加载指定 Skill 的完整指令内容。"
                "当任务与某个 Skill 描述匹配时，先调用此工具获取专业指令，再执行任务。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要加载的 Skill 名称。",
                        "enum": skill_names,
                    },
                },
                "required": ["name"],
            },
        },
    }


# ── 工具 JSON Schema 定义（传给 LLM 的 tools 参数）────────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "搜索私有知识库（dense 向量 + BM25 关键词 混合检索），返回与问题最相关的文档片段。"
                "当问题可能在已导入的本地文档中有答案时，优先调用此工具。"
                "查询写法建议："
                "①优先使用包含专有名词/术语/版本号的简短关键词查询（如 '3GPP TS 38.211 PRACH'），"
                "BM25 对此类术语命中显著优于自然语言；"
                "②口语化表达请同时尝试术语化改写（'5G 基站' → 'gNB'）；"
                "③多义/复合问题拆成多个子查询分别调用本工具；"
                "④对'列举/对比/汇总'类问题请把 top_k 设为 10。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用于检索的自然语言或关键词查询语句，尽量与用户问题语义相近。",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回的最大文档片段数，默认为 8，最大不超过 10；枚举/对比类问题建议设为 10。",
                        "default": 8,
                    },
                    "where": {
                        "type": "object",
                        "description": (
                            "可选 metadata 过滤条件，按入库 metadata 字段精筛。"
                            "支持字段：lang('zh'/'en'/'mixed')、ext('.pdf'/'.docx'/...)、"
                            "filename、source（相对路径）、page_no（int）、heading_path（字符串包含）。"
                            "等值用 {字段: 值}；多值用 {字段: {\"$in\": [...]}}。"
                            "示例：{\"lang\": \"zh\"}、{\"ext\": {\"$in\": [\".pdf\", \".docx\"]}}。"
                            "明确知道答案语种或文档类型时使用此参数可大幅提升命中质量。"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "通过互联网搜索引擎查找信息，返回真实网页 URL 及内容摘要列表。"
                "当知识库无法回答问题，或需要获取最新资讯时，优先调用此工具，"
                "再根据返回的 URL 调用 fetch_url 获取详情。"
                "不要凭空猜测 URL，应先通过此工具搜索确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，尽量简洁明确，支持中英文。",
                    },
                    "num": {
                        "type": "integer",
                        "description": "返回结果条数，默认 5，最多 10。",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "抓取指定网页的正文内容并返回纯文本。"
                "必须使用通过 web_search 或知识库返回的真实 URL，不得凭空猜测。"
                "对于动态渲染的 SPA 页面，工具会自动通过 Jina Reader 兜底，无需手动处理。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网页 URL，必须以 http:// 或 https:// 开头。",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "返回内容的最大字符数，默认 3000，避免超出 LLM 上下文窗口。",
                        "default": 3000,
                    },
                },
                "required": ["url"],
            },
        },
    },
]


# ── Phase 2.1 — Plan-Execute 三 tool JSON Schema ─────────────────────────────

_PLAN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "make_plan",
            "description": (
                "对**多步骤复杂任务**（多文档对比 / 学习计划 / 目标+步骤型 / 多源资料综合等）"
                "先列计划再动手执行；单实体查询、单事实回答、简单闲聊请**不要**调用本工具。"
                "调用后会返回结构化 plan ack 与第 1 步指引；下一轮按指引调用对应业务 tool 执行第 1 步，"
                "每完成一步调用 update_step 更新状态。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "3-6 个步骤的简洁描述列表（每步一句话，10-30 字最佳），"
                            "按执行先后顺序排列。例：['列出我的 RAG 项目', '各项目召回策略', '横向对比', '总结']"
                        ),
                        "minItems": 1,
                    },
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_step",
            "description": (
                "完成 plan 的某一步后调用，标记该步状态。仅在已有 active plan（先调过 make_plan）时调用。"
                "调用后会返回当前进度与下一步指引；若 plan 全部完成会提示进入总结。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "step_id": {
                        "type": "integer",
                        "description": "要更新的步骤编号（从 1 起，对应 make_plan 时的步骤顺序）",
                        "minimum": 1,
                    },
                    "status": {
                        "type": "string",
                        "enum": ["success", "failed", "skipped"],
                        "description": "步骤结果：success=完成、failed=失败（可重试或转下一步）、skipped=主动跳过",
                    },
                    "note": {
                        "type": "string",
                        "description": "可选备注（≤ 60 字），如失败原因摘要或本步关键发现。",
                    },
                },
                "required": ["step_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "abort_plan",
            "description": (
                "active plan 出现不可恢复错误（如多次失败、依赖前置数据完全缺失）时主动中止整个 plan。"
                "中止后下一轮请基于已收集信息直接总结答案，并向用户说明未完成原因。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "中止原因摘要（≤ 60 字），将出现在 plan 总结里给用户看。",
                    },
                },
                "required": [],
            },
        },
    },
]


# ── Phase 2.2 — 学习计划业务三 tool JSON Schema ──────────────────────────────
# 与 _PLAN_TOOLS（单次问答内"用完即弃"的执行计划）相对：本组 tool 操作的是
# **跨 session 长期持久化的学习计划**（learning_plans / learning_tasks 表）。
# 触发主路径见 [study-planner skill](../../.agenta/skills/study-planner/SKILL.md)。

_STUDY_PLAN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_study_plan",
            "description": (
                "新建一个**跨 session 持久化的学习计划**，把目标 + 阶段任务清单一次性落库。"
                "适用：用户表达明确学习目标（如『8 周准备 ML 面试』/『学透 RAG』），"
                "Agent 已在前几轮收集到领域知识 / 整理出阶段拆分。"
                "**不要**直接用本 tool —— 应先按 study-planner skill 引导用 make_plan 拆解"
                "（查领域 → 列阶段 → 列任务 → 落库），落库即调本 tool。"
                "新建的 plan 自动设为 active；同时仅一个 active plan，旧 active 自动 archive。"
                "返回新 plan_id 与 task 总数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "学习目标的一句话描述，如 \"8 周准备 ML 面试\" / \"系统学习 RAG 工程\"",
                    },
                    "weeks": {
                        "type": "integer",
                        "description": "计划总周数；用户未指定可填 0（表示无明确周期）。",
                        "default": 0,
                        "minimum": 0,
                    },
                    "tasks": {
                        "type": "array",
                        "description": (
                            "全部任务清单（按阶段顺序）；每项 {stage_idx, order_idx, title}。"
                            "stage_idx：阶段编号从 1 起，对应 Week 1 / Stage 1 等；"
                            "order_idx：阶段内顺序从 1 起；"
                            "title：任务一句话描述（10-40 字），动词起头如 \"完成 Pandas 官方 10min 教程\"。"
                            "建议 ≤ 12 阶段、每阶段 3-6 任务。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "stage_idx": {"type": "integer", "minimum": 1},
                                "order_idx": {"type": "integer", "minimum": 1},
                                "title": {"type": "string"},
                            },
                            "required": ["stage_idx", "order_idx", "title"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["goal", "tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_study_progress",
            "description": (
                "更新某个学习任务的完成状态。"
                "适用：用户口头报告完成 / 跳过任务（如『今天看完了 FastAPI 文档』/『这周太忙跳过 X』）。"
                "调用前若不确定 task_id，先调 query_study_status 查清。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "integer",
                        "description": "任务所属的 plan_id（双重校验防误更新）。",
                        "minimum": 1,
                    },
                    "task_id": {
                        "type": "integer",
                        "description": "要更新的 task_id（从 query_study_status 结果取得）。",
                        "minimum": 1,
                    },
                    "status": {
                        "type": "string",
                        "enum": ["success", "skipped", "pending"],
                        "description": (
                            "success=完成 / skipped=主动跳过 / pending=回退为未完成（很少用）。"
                            "学习任务无 \"failed\" 概念（不像执行 plan）"
                        ),
                    },
                    "note": {
                        "type": "string",
                        "description": "可选备注（≤ 200 字），如关键收获 / 跳过原因。",
                    },
                },
                "required": ["plan_id", "task_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_study_status",
            "description": (
                "查询学习计划进度。"
                "适用：用户问 \"我学到哪了 / 下一步该干啥 / 我有哪些学习计划\"。"
                "**不传 plan_id 默认查当前 active plan**；想看任一 plan 全貌传 plan_id；"
                "想看全部 plan 列表传 list_all=true。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "integer",
                        "description": "可选；不传则查当前 active plan。",
                        "minimum": 1,
                    },
                    "list_all": {
                        "type": "boolean",
                        "description": "true 时返回所有 plan 摘要列表（覆盖 plan_id）。",
                        "default": False,
                    },
                    "detail": {
                        "type": "boolean",
                        "description": (
                            "true 返回完整任务清单（含 task_id / 状态 / 备注）；"
                            "false 仅返回 plan 元信息 + 进度统计 + 下一步建议。"
                            "默认 false 以节省 context"
                        ),
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
]


# ── 工具实现 ──────────────────────────────────────────────────────────────────


def _tool_web_search(query: str, num: int = 5) -> ToolResult:
    """
    通过 Serper.dev 搜索引擎执行网络搜索，返回真实 URL 列表及摘要。

    Args:
        query: 搜索关键词。
        num: 期望返回的结果条数（最多 WEB_SEARCH_MAX_NUM）。

    Returns:
        ToolResult：有结果 → status="ok"；无结果 → status="empty"；请求失败 → status="error"。
    """
    api_key = _cfg.SERPAPI_API_KEY
    if not api_key:
        return ToolResult(
            status="error",
            content="未配置 SERPAPI_API_KEY，无法使用 web_search 工具。请在 .env 中设置该变量。",
        )

    num = min(max(1, num), WEB_SEARCH_MAX_NUM)
    logger.info("[tool] web_search: query=%r, num=%d", query, num)

    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num, "hl": "zh-cn", "gl": "cn"},
            timeout=FETCH_URL_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return ToolResult(status="error", content=f"搜索请求超时（{FETCH_URL_TIMEOUT}s）")
    except requests.exceptions.RequestException as e:
        return ToolResult(status="error", content=f"搜索请求失败 — {e}")
    except Exception as e:
        return ToolResult(status="error", content=f"解析搜索结果失败 — {e}")

    organic = data.get("organic", [])
    if not organic:
        return ToolResult(status="empty", content="搜索未返回任何结果，请尝试换一种关键词。")

    lines: list[str] = []
    for i, item in enumerate(organic[:num], 1):
        title = item.get("title", "(无标题)")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        lines.append(f"[{i}] {title}\n    URL: {link}\n    摘要: {snippet}")

    return ToolResult(status="ok", content="\n\n".join(lines))


def _tool_search_knowledge(
    query: str,
    top_k: int = 8,
    where: dict | None = None,
    citation_builder: "CitationBuilder | None" = None,
) -> ToolResult:
    """
    调用 RAG 检索层（query 扩展 → dense + BM25 混合召回 → 可选 rerank），
    返回格式化的文档片段字符串。

    检索前统一走 expand_queries(query)，各轴由 .env 独立开关（未开启的轴不追加）：
      - RAG_QUERY_REWRITE_ENABLED：Multi-Query，LLM 生成至多 N 条同义改写；
      - RAG_HYDE_ENABLED：HyDE，LLM 生成 1~2 句假设性答案作为额外检索 query；
      - RAG_TRANSLATE_QUERY_ENABLED：翻译轴，按 query 语种追加中/英翻译版。
    列表第 0 项永远是原 query；expand_queries 或 search 异常时退化为单 query。

    Args:
        query:             检索查询语句。
        top_k:             返回的最大片段数（1~MAX_SEARCH_TOP_K，超出会截断）。
        where:             可选 metadata 过滤子句，透传给 retriever，支持 ChromaDB 等值/$in/$ne 算子。
        citation_builder:  Phase 1.4 引用编排器；传入时把 hits 注册进去拿到
                           跨 tool_call 累计的全局编号，并把这些编号写到给
                           LLM 看的格式化文本里（替代默认的 enumerate 1..N）。
                           不传则保持向后兼容行为。

    Returns:
        ToolResult：有命中结果 → status="ok"；知识库为空/无命中 → status="empty"。
    """
    top_k = min(max(1, top_k), MAX_SEARCH_TOP_K)
    if where is not None and not isinstance(where, dict):
        # LLM 偶尔会把 where 当成字符串传过来，宽松处理：直接忽略
        logger.warning("[tool] search_knowledge: where 非 dict，已忽略：%r", where)
        where = None

    # query 三轴扩展（multi-query / HyDE / 翻译）。失败时 expand_queries 退化为 [query]。
    expanded_queries: list[str]
    try:
        from src.rag.query_rewriter import expand_queries
        expanded_queries = expand_queries(query)
    except Exception as e:
        logger.warning("[tool] search_knowledge: query 扩展失败，已降级为单 query — %s", e)
        expanded_queries = [query]

    if len(expanded_queries) > 1:
        logger.info(
            "[tool] search_knowledge: query=%r → 扩展 %d 条（含原 query），top_k=%d, where=%s",
            query, len(expanded_queries), top_k, where or "{}",
        )
        for i, q in enumerate(expanded_queries):
            logger.info("    [%d] %s", i, q)
    else:
        logger.info(
            "[tool] search_knowledge: query=%r, top_k=%d, where=%s",
            query, top_k, where or "{}",
        )

    hits = search(query, top_k=top_k, where=where, queries=expanded_queries)
    if hits:
        # Phase 1.4：若上层传入 CitationBuilder，把 hits 注册进去拿到全局
        # 编号，让 LLM 看到的 [n] 与最终 sources 块的 [n] 对齐
        citation_nums = (
            citation_builder.register(hits) if citation_builder is not None else None
        )
        return ToolResult(
            status="ok",
            content=format_search_results(hits, citation_nums=citation_nums),
        )
    return ToolResult(status="empty", content="知识库中未找到相关内容。")


def _fetch_raw_response(url: str) -> "requests.Response | ToolResult":
    """
    发送 HTTP GET 请求，返回 Response 对象；任何错误提前返回 ToolResult。
    同时校验 Content-Type 和魔术字节，拒绝二进制内容。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(url, headers=headers, timeout=FETCH_URL_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        return ToolResult(status="error", content=f"请求超时（{FETCH_URL_TIMEOUT}s），URL: {url}")
    except requests.exceptions.HTTPError as e:
        return ToolResult(status="error", content=f"HTTP {e.response.status_code}，URL: {url}")
    except requests.exceptions.RequestException as e:
        return ToolResult(status="error", content=f"网络请求失败 — {e}")

    content_type = response.headers.get("Content-Type", "").lower().split(";")[0].strip()
    if not any(content_type.startswith(t) for t in _TEXT_TYPES):
        return ToolResult(
            status="error",
            content=f"不支持下载二进制文件（Content-Type: {content_type}）。请访问对应的 HTML 页面或文档索引。",
        )
    if any(response.content.startswith(magic) for magic in _BINARY_MAGIC):
        return ToolResult(
            status="error",
            content="响应内容为二进制文件（如 .zip/.pdf/.png 等），无法提取文本。请访问对应的 HTML 页面。",
        )
    return response


def _extract_text_from_response(response: "requests.Response", max_chars: int) -> ToolResult:
    """从 HTTP Response 中用 BeautifulSoup 提取正文纯文本，并截断至 max_chars。"""
    try:
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "aside", "header"]):
            tag.decompose()

        lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
        result_lines: list[str] = []
        prev_blank = False
        for line in lines:
            if line:
                result_lines.append(line)
                prev_blank = False
            elif not prev_blank:
                result_lines.append("")
                prev_blank = True

        text = "\n".join(result_lines).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[内容已截断，原文共 {len(text)} 字符]"

        if not text:
            return ToolResult(status="empty", content="页面内容为空或无法提取正文。")
        return ToolResult(status="ok", content=text)
    except Exception as e:
        return ToolResult(status="error", content=f"解析页面失败 — {e}")


def _is_likely_spa(text: str, html: str = "") -> bool:
    """
    判断页面是否可能是 SPA 空壳（JS 渲染后才有内容）。

    启发式规则：
      - 提取的正文字符数极少（< _SPA_MIN_CONTENT_CHARS）
      - HTML 中含典型 SPA 根挂载点（#app / #root）
    """
    if len(text.strip()) < _SPA_MIN_CONTENT_CHARS:
        return True
    if html and ('<div id="app"' in html or '<div id="root"' in html):
        return True
    return False


def _fetch_via_jina(url: str, max_chars: int) -> ToolResult:
    """
    通过 Jina Reader (r.jina.ai) 云端渲染 SPA 并返回 Markdown 正文。

    Jina 会在服务端执行 JS 后返回纯文本 / Markdown，适合绕过 SPA 问题。
    超时时间比普通 fetch 更长（FETCH_URL_TIMEOUT + JINA_EXTRA_TIMEOUT）。
    """
    jina_url = f"https://r.jina.ai/{url}"
    logger.info("[tool] fetch_url: SPA detected → Jina Reader fallback: %r", jina_url)
    try:
        resp = requests.get(
            jina_url,
            headers={"Accept": "text/markdown", "User-Agent": "Mozilla/5.0"},
            timeout=FETCH_URL_TIMEOUT + JINA_EXTRA_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.text.strip()
    except requests.exceptions.RequestException as e:
        return ToolResult(status="error", content=f"Jina Reader 请求失败 — {e}")

    if not text:
        return ToolResult(status="empty", content="Jina Reader 返回内容为空。")

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[内容已截断，原文共 {len(text)} 字符]"
    return ToolResult(status="ok", content=f"[via Jina Reader]\n{text}")


def _tool_fetch_url(url: str, max_chars: int = 3000) -> ToolResult:
    """
    抓取网页正文内容，使用 BeautifulSoup 提取纯文本。
    若检测到 SPA 空壳（内容过少或含 #app/#root 挂载点），自动 fallback 到 Jina Reader。

    Args:
        url: 目标网页 URL，必须来自 web_search 或知识库，不得凭空猜测。
        max_chars: 返回内容的最大字符数。

    Returns:
        网页正文纯文本（截断至 max_chars），抓取失败时返回错误说明。
    """
    logger.info("[tool] fetch_url: url=%r, max_chars=%d", url, max_chars)

    if not url.startswith(("http://", "https://")):
        return ToolResult(
            status="error",
            content=f"URL 必须以 http:// 或 https:// 开头，收到：{url!r}",
        )

    raw = _fetch_raw_response(url)
    if isinstance(raw, ToolResult):
        return raw

    result = _extract_text_from_response(raw, max_chars)

    # SPA fallback：正文过短或含典型 SPA 根挂载点时，改用 Jina Reader
    if result.status in ("ok", "empty") and _is_likely_spa(result.content, raw.text):
        return _fetch_via_jina(url, max_chars)

    return result


# ── Phase 2.1 — Plan-Execute tool 实现 ───────────────────────────────────────


def _tool_make_plan(
    steps: Any,
    messages: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """生成 plan。本轮仅记录步骤、不联动执行 step 1（D5 分轮执行 / two-stage）；下一轮 LLM 按 ack 指引推进。"""
    if not isinstance(steps, list) or not steps:
        return ToolResult(
            status="error",
            content="make_plan(steps) 必须是非空字符串列表，例如 steps=['列项目', '对比', '总结']",
        )
    if not all(isinstance(s, str) and s.strip() for s in steps):
        return ToolResult(
            status="error",
            content="make_plan(steps) 每个元素必须是非空字符串。",
        )
    cleaned = [s.strip() for s in steps]
    plan_block = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(cleaned))
    return ToolResult(
        status="ok",
        content=(
            f"📋 已记录 plan，共 {len(cleaned)} 步：\n{plan_block}\n\n"
            "请按 plan 顺序逐步执行：每完成一步调用 update_step 更新状态再继续下一步；"
            "失败步可重试，多次失败考虑 abort_plan。\n"
            f"→ 下一步：第 1 步 — {cleaned[0]}（请调用合适的业务 tool）"
        ),
    )


def _tool_update_step(
    step_id: Any,
    status: Any,
    note: str = "",
    messages: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """更新 plan 某步状态；从 messages reconstruct 当前 plan 后渲染进度 + 下一步指引。"""
    if not isinstance(step_id, int) or step_id < 1:
        return ToolResult(
            status="error",
            content=f"update_step(step_id) 必须是 ≥1 整数，收到：{step_id!r}",
        )
    if status not in ("success", "failed", "skipped"):
        return ToolResult(
            status="error",
            content=(
                "update_step(status) 必须是 success / failed / skipped 之一，"
                f"收到：{status!r}"
            ),
        )

    # 延迟 import 避免 src.agent.core 反向依赖（保持 tools.py 极简依赖链）
    from src.agent.core.plan_manager import reconstruct_from_messages

    state = reconstruct_from_messages(messages or [])
    if state is None or not state.steps:
        return ToolResult(
            status="error",
            content="未找到 active plan — 请先调用 make_plan 创建 plan，再 update_step。",
        )
    step = next((s for s in state.steps if s.id == step_id), None)
    if step is None:
        return ToolResult(
            status="error",
            content=(
                f"step_id={step_id} 不在当前 plan 范围内"
                f"（plan 共 {len(state.steps)} 步：1..{len(state.steps)}）"
            ),
        )

    status_icon = {"success": "✓", "failed": "✗", "skipped": "⏭"}[status]
    note_suffix = f"（{note.strip()}）" if note and note.strip() else ""
    head = f"{status_icon} step {step_id}「{step.text}」状态：{status}{note_suffix}"

    done, total = state.progress()
    if state.is_complete():
        return ToolResult(
            status="ok",
            content=(
                f"{head}\n\nplan 已完成 ({done}/{total})。请综合 plan 各步骤结果总结最终答案。"
            ),
        )
    nxt = state.next_pending_step()
    assert nxt is not None  # is_complete() is False 蕴含
    return ToolResult(
        status="ok",
        content=(
            f"{head}\n\n当前进度：{done}/{total}\n"
            f"→ 下一步：第 {nxt.id} 步 — {nxt.text}（请调用合适的业务 tool）"
        ),
    )


def _tool_abort_plan(
    reason: str = "",
    messages: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """中止 active plan；plan 状态由 reconstruct_from_messages 后续感知 aborted=True。"""
    reason_suffix = f"，原因：{reason.strip()}" if reason and reason.strip() else ""
    return ToolResult(
        status="ok",
        content=(
            f"🛑 plan 已中止{reason_suffix}。请基于已收集的信息总结最终答案，"
            "并向用户说明未完成的部分及原因。"
        ),
    )


# ── Phase 2.2 — 学习计划业务 tool 实现 ───────────────────────────────────────
# 实现采用"按需 import + 复用共享 store"模式：避免 tools.py 顶层引入 store 后
# 拖累冷启动 / 测试 mock 复杂度；进程内共享 `learning_plan_store.get_shared_store()`
# 单例，与 agent.py 注入 system block 时读到的是同一实例 / 同一连接。


def _get_study_plan_store() -> Any:
    """延迟 import，返回 learning_plan_store 模块级共享 store。"""
    from src.memory.learning_plan_store import get_shared_store
    return get_shared_store()


def _tool_create_study_plan(
    goal: Any,
    tasks: Any,
    weeks: Any = 0,
) -> ToolResult:
    """新建跨 session 持久化学习计划 + 全量任务，自动设为 active 并 archive 旧 active。"""
    if not isinstance(goal, str) or not goal.strip():
        return ToolResult(
            status="error",
            content="create_study_plan(goal) 必须是非空字符串，如 \"8 周准备 ML 面试\"",
        )
    if not isinstance(weeks, int) or weeks < 0:
        return ToolResult(
            status="error",
            content=f"create_study_plan(weeks) 必须是 ≥0 整数，收到：{weeks!r}",
        )
    if not isinstance(tasks, list) or not tasks:
        return ToolResult(
            status="error",
            content=(
                "create_study_plan(tasks) 必须是非空列表，每项 {stage_idx, order_idx, title}"
            ),
        )
    cleaned: list[dict[str, Any]] = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            return ToolResult(
                status="error",
                content=f"tasks[{i}] 不是 dict，收到：{type(t).__name__}",
            )
        stage_idx = t.get("stage_idx")
        order_idx = t.get("order_idx")
        title = t.get("title")
        if (not isinstance(stage_idx, int) or stage_idx < 1
                or not isinstance(order_idx, int) or order_idx < 1
                or not isinstance(title, str) or not title.strip()):
            return ToolResult(
                status="error",
                content=(
                    f"tasks[{i}] 字段非法（stage_idx/order_idx 必须 ≥1 整数、title 必须非空字符串），"
                    f"收到：{t!r}"
                ),
            )
        cleaned.append({"stage_idx": stage_idx, "order_idx": order_idx, "title": title.strip()})

    store = _get_study_plan_store()
    try:
        plan_id = store.create_plan(goal=goal.strip(), weeks=weeks, set_active=True)
        added = store.add_tasks(plan_id, cleaned)
    except Exception as e:
        return ToolResult(status="error", content=f"持久化学习计划失败 — {e}")

    weeks_suffix = f"（共 {weeks} 周）" if weeks else ""
    return ToolResult(
        status="ok",
        content=(
            f"✓ 已创建学习计划 plan_id={plan_id}：\"{goal.strip()}\"{weeks_suffix}，"
            f"含 {added} 个任务，已设为当前 active plan。\n"
            "→ 可向用户简要展示计划概要并提示：完成任务时告诉我，"
            "我会用 update_study_progress 帮你打勾。"
        ),
    )


def _tool_update_study_progress(
    plan_id: Any,
    task_id: Any,
    status: Any,
    note: str = "",
) -> ToolResult:
    """更新单个学习任务状态；status 仅 success / skipped / pending。"""
    if not isinstance(plan_id, int) or plan_id < 1:
        return ToolResult(
            status="error",
            content=f"update_study_progress(plan_id) 必须是 ≥1 整数，收到：{plan_id!r}",
        )
    if not isinstance(task_id, int) or task_id < 1:
        return ToolResult(
            status="error",
            content=f"update_study_progress(task_id) 必须是 ≥1 整数，收到：{task_id!r}",
        )
    if status not in ("success", "skipped", "pending"):
        return ToolResult(
            status="error",
            content=(
                f"update_study_progress(status) 必须是 success / skipped / pending 之一，"
                f"收到：{status!r}"
            ),
        )

    store = _get_study_plan_store()
    ok = store.update_task_status(plan_id, task_id, status, note=note or "")
    if not ok:
        return ToolResult(
            status="error",
            content=(
                f"未找到 task_id={task_id}（或不属于 plan_id={plan_id}）。"
                "请先调 query_study_status 确认 id 后重试。"
            ),
        )

    # 重新查 plan 给 LLM 进度反馈 + 下一步建议
    plan = store.get_plan_with_tasks(plan_id)
    if plan is None:
        return ToolResult(status="ok", content=f"✓ 已更新 task_id={task_id} 状态为 {status}。")
    tasks = plan.get("tasks", [])
    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "success")
    pending = [t for t in tasks if t["status"] == "pending"]
    icon = {"success": "✓", "skipped": "⏭", "pending": "☐"}[status]
    head = f"{icon} task_id={task_id} → {status}（plan \"{plan['goal']}\"）"

    if not pending:
        # 全部 success / skipped — 自动 mark plan 完成
        if all(t["status"] == "success" for t in tasks):
            store.complete_plan(plan_id)
            return ToolResult(
                status="ok",
                content=f"{head}\n\n🎉 plan 全部完成（{done}/{total}），已自动标记 completed。",
            )
        return ToolResult(
            status="ok",
            content=f"{head}\n\nplan 内任务都已处理完（{done} 完成 / {total - done} 跳过）。",
        )

    nxt = pending[0]
    return ToolResult(
        status="ok",
        content=(
            f"{head}\n\n当前进度：{done}/{total}\n"
            f"→ 下一个待办：[task_id={nxt['id']}] Stage {nxt['stage_idx']}.{nxt['order_idx']} — {nxt['title']}"
        ),
    )


def _render_plan_summary(plan: dict[str, Any], include_tasks: bool) -> str:
    """渲染单个 plan 文本块（query_study_status 内部 helper）。"""
    weeks = plan.get("weeks", 0)
    weeks_suffix = f"，{weeks} 周" if weeks else ""
    active_tag = " [active]" if plan.get("is_active") else ""
    status_tag = f" [{plan['status']}]" if plan.get("status") != "active" else ""
    head = f"### plan_id={plan['id']}{active_tag}{status_tag}\n- 目标：{plan['goal']}{weeks_suffix}"

    tasks = plan.get("tasks", [])
    if not tasks and not include_tasks:
        # list_plans 路径用预聚合字段
        total = plan.get("task_count", 0)
        done = plan.get("done_count", 0)
        head += f"\n- 进度：{done}/{total}"
        return head

    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "success")
    skipped = sum(1 for t in tasks if t["status"] == "skipped")
    head += f"\n- 进度：{done}/{total} 完成"
    if skipped:
        head += f"（跳过 {skipped}）"

    if not include_tasks:
        pending = [t for t in tasks if t["status"] == "pending"]
        if pending:
            nxt = pending[0]
            head += (
                f"\n- 下一个待办：[task_id={nxt['id']}] "
                f"Stage {nxt['stage_idx']}.{nxt['order_idx']} — {nxt['title']}"
            )
        return head

    # detail=True：列全部 task
    lines = [head, ""]
    current_stage = None
    for t in tasks:
        if t["stage_idx"] != current_stage:
            current_stage = t["stage_idx"]
            lines.append(f"**Stage {current_stage}**")
        icon = {"pending": "☐", "success": "✓", "skipped": "⏭"}.get(t["status"], "?")
        note_suffix = f" — {t['note']}" if t["note"] else ""
        lines.append(f"- {icon} [task_id={t['id']}] {t['title']}{note_suffix}")
    return "\n".join(lines)


def _tool_query_study_status(
    plan_id: Any = None,
    list_all: bool = False,
    detail: bool = False,
) -> ToolResult:
    """查学习计划进度：默认 active plan；plan_id 查指定；list_all 列全部摘要。"""
    store = _get_study_plan_store()

    if list_all:
        plans = store.list_plans(include_abandoned=False)
        if not plans:
            return ToolResult(status="empty", content="暂无任何学习计划。")
        blocks = [_render_plan_summary(p, include_tasks=False) for p in plans]
        return ToolResult(status="ok", content="\n\n".join(blocks))

    if plan_id is None:
        plan = store.get_active()
        if plan is None:
            return ToolResult(
                status="empty",
                content="当前没有 active 学习计划。可让用户描述目标后用 create_study_plan 新建。",
            )
    else:
        if not isinstance(plan_id, int) or plan_id < 1:
            return ToolResult(
                status="error",
                content=f"query_study_status(plan_id) 必须是 ≥1 整数，收到：{plan_id!r}",
            )
        plan = store.get_plan_with_tasks(plan_id)
        if plan is None:
            return ToolResult(status="error", content=f"plan_id={plan_id} 不存在")

    return ToolResult(status="ok", content=_render_plan_summary(plan, include_tasks=detail))


def _tool_load_skill(name: str, skill_bodies: dict[str, str]) -> ToolResult:
    """
    加载 Skill 的完整指令正文，返回 <skill_content> 包裹的内容。

    Args:
        name: 已注册的 skill 名称。
        skill_bodies: {name: body} 映射，由 Agent 实例持有并传入。

    Returns:
        ToolResult：找到 → status="ok"；未找到 → status="error"。
    """
    logger.info("[tool] load_skill: name=%r", name)
    body = skill_bodies.get(name)
    if body is None:
        return ToolResult(
            status="error",
            content=f"Skill '{name}' 不存在，可用 skills: {list(skill_bodies.keys())}",
        )
    content = f'<skill_content name="{name}">\n{body}\n</skill_content>'
    return ToolResult(status="ok", content=content)


# ── 统一路由入口 ──────────────────────────────────────────────────────────────


def execute_tool(
    name: str,
    args: dict[str, Any],
    skill_bodies: dict[str, str] | None = None,
    citation_builder: "CitationBuilder | None" = None,
    messages: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """
    根据工具名称路由执行对应工具函数。

    Args:
        name: 工具名称，对应 TOOLS / `_PLAN_TOOLS` 中的 function.name。
        args: 工具参数字典，由 LLM 的 tool_calls 解析而来。
        skill_bodies: {skill_name: body} 映射，供 load_skill 工具使用。
        citation_builder: Phase 1.4 引用编排器；仅 search_knowledge 路径透传，
                          其它工具无引用语义直接忽略。
        messages: 当前轮已累计的 messages（含本次 assistant tool_calls）；仅 plan
                  tools（update_step / abort_plan）用于 reconstruct plan 状态。
                  非 plan 工具忽略此参数。

    Returns:
        ToolResult：status 为 "ok"/"empty"/"error"，content 为工具输出内容。
        任何异常（含未知工具名）均被捕获，以 status="error" 返回，不向上抛出。
    """
    logger.info("执行工具: %s，参数: %s", name, json.dumps(args, ensure_ascii=False))

    try:
        match name:
            case "search_knowledge":
                return _tool_search_knowledge(
                    query=args["query"],
                    top_k=args.get("top_k", 8),
                    where=args.get("where"),
                    citation_builder=citation_builder,
                )
            case "web_search":
                return _tool_web_search(
                    query=args["query"],
                    num=args.get("num", 5),
                )
            case "fetch_url":
                return _tool_fetch_url(
                    url=args["url"],
                    max_chars=args.get("max_chars", 3000),
                )
            case "load_skill":
                return _tool_load_skill(
                    name=args["name"],
                    skill_bodies=skill_bodies or {},
                )
            case "make_plan":
                return _tool_make_plan(steps=args.get("steps"), messages=messages)
            case "update_step":
                return _tool_update_step(
                    step_id=args.get("step_id"),
                    status=args.get("status"),
                    note=args.get("note", ""),
                    messages=messages,
                )
            case "abort_plan":
                return _tool_abort_plan(
                    reason=args.get("reason", ""),
                    messages=messages,
                )
            case "create_study_plan":
                return _tool_create_study_plan(
                    goal=args.get("goal"),
                    tasks=args.get("tasks"),
                    weeks=args.get("weeks", 0),
                )
            case "update_study_progress":
                return _tool_update_study_progress(
                    plan_id=args.get("plan_id"),
                    task_id=args.get("task_id"),
                    status=args.get("status"),
                    note=args.get("note", ""),
                )
            case "query_study_status":
                return _tool_query_study_status(
                    plan_id=args.get("plan_id"),
                    list_all=args.get("list_all", False),
                    detail=args.get("detail", False),
                )
            case _:
                return ToolResult(
                    status="error",
                    content=f"未知工具: '{name}'，支持的工具: {[t['function']['name'] for t in get_tools()]}",
                )
    except Exception as e:
        logger.warning("[tool] execute_tool 异常兜底: %s", e)
        return ToolResult(status="error", content=f"工具执行异常: {e}")
