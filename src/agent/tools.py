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
import re
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
      - plan-execute 3 tool（make_plan / update_step / abort_plan）
      - 学习计划业务 3 tool（create_study_plan / update_study_progress / query_study_status）
      - Quiz 业务 3 tool（create_quiz / grade_quiz / query_quiz_history）
      - SRS 业务 4 tool（add_to_srs / query_srs_due / review_srs_card / query_srs_stats）
    若传入 skill_bodies，再追加 load_skill 工具定义（name 字段限定为已有名称枚举）。

    返回前过 security_filter.is_tool_allowed 名单门——按 SECURITY_MODE
    切换 fail-open + BLOCKLIST 或 fail-close + ALLOWLIST。命中拒绝的 tool 静默跳过
    + log warning（已在 is_tool_allowed 内部 log）；execute_tool 入口同样 double-check。

    合流 MCP server 暴露的 tool（带 `<server>.<tool>` namespace 前缀）。
    MCP `fetch` server 启动成功时屏蔽内置 `fetch_url`，让 LLM 只看到
    `fetch.fetch`，避免功能重叠导致选择困难。
    """
    tools = (
        list(TOOLS)
        + list(_PLAN_TOOLS)
        + list(_STUDY_PLAN_TOOLS)
        + list(_QUIZ_TOOLS)
        + list(_SRS_TOOLS)
    )

    mcp_tools = _load_mcp_tools_safe()
    if mcp_tools:
        # fetch.* 接入成功时，从基础工具集移除 fetch_url
        if any(t.get("server") == "fetch" for t in mcp_tools):
            tools = [t for t in tools if t["function"]["name"] != "fetch_url"]
        for mt in mcp_tools:
            tools.append({
                "type": "function",
                "function": {
                    "name": mt["name"],
                    "description": (
                        mt.get("description")
                        or f"MCP tool from {mt.get('server', '?')} server"
                    ),
                    "parameters": mt.get("inputSchema") or {"type": "object"},
                },
            })

    if skill_bodies:
        tools.append(_build_load_skill_def(list(skill_bodies.keys())))

    from src.agent.core.security_filter import is_tool_allowed
    return [t for t in tools if is_tool_allowed(t.get("function", {}).get("name", ""))]


def _load_mcp_tools_safe() -> list[dict[str, Any]]:
    """读 MCP shared manager 的 list_tools；任何异常 / 未启动一律返空，避免阻塞 LLM 主流程。"""
    try:
        from src.agent.core.mcp_manager import get_shared_manager
        return get_shared_manager().list_tools()
    except Exception as exc:
        logger.debug("[tools] MCP list_tools 跳过：%s", exc)
        return []


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


# ── Plan-Execute 三 tool JSON Schema ─────────────────────────────────────────

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


# ── 学习计划业务三 tool JSON Schema ──────────────────────────────────────────
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
                "查询本系统内通过 create_study_plan 创建的**结构化学习计划进度状态**"
                "（plan 元信息 / task 完成状态 / 下一步建议）。"
                "适用：用户问 \"我学到哪了 / 下一步该干啥 / 我有哪些计划\"。"
                "**反例（不要选本工具，请用 search_knowledge）**："
                "用户问学习计划的**具体内容**（如 \"AI Agent 第三天讲什么 / xx 计划里建议怎么学 / 这个计划的步骤是什么\"）"
                "—— 这类问题答案在用户上传的知识库文档里，本工具只返回 task 标题不返回内容。"
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

    # web 搜索结果是"非用户主控"外部数据，进 LLM context 前过 security_filter：
    # ① 每条 snippet 走 scrub_injection 段级删除已知注入模板；
    # ② 整个返回值用 wrap_untrusted(kind="web") 包装。
    from src.agent.core.security_filter import scrub_injection, wrap_untrusted

    lines: list[str] = []
    for i, item in enumerate(organic[:num], 1):
        title = item.get("title", "(无标题)")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        cleaned_snippet, scrubbed = scrub_injection(snippet)
        flag = " [⚠️ 已清洗]" if scrubbed else ""
        lines.append(f"[{i}] {title}{flag}\n    URL: {link}\n    摘要: {cleaned_snippet}")

    return ToolResult(status="ok", content=wrap_untrusted("\n\n".join(lines), kind="web"))


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
        citation_builder:  引用编排器；传入时把 hits 注册进去拿到
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

    # —— critic 相关性过滤（过滤 not_relevant，0 条返 empty 不重召回） ——
    if hits and _cfg.HARNESS_RAG_ENABLED:
        try:
            from src.agent.core.harness_manager import get_harness_manager
            hits = get_harness_manager().filter_chunks(query=query, hits=hits)
        except Exception as e:  # noqa: BLE001 — critic 异常一律软放行原始 hits
            logger.warning("[tool] search_knowledge: harness 过滤失败，保留原始 hits：%s", e)

    if hits:
        # 若上层传入 CitationBuilder，把 hits 注册进去拿到全局
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
        ok 状态的内容会先过 security_filter（scrub + wrap_untrusted(kind="web")）。
    """
    logger.info("[tool] fetch_url: url=%r, max_chars=%d", url, max_chars)

    # SSRF 防御统一入口，拦 file:// / 内网 IP / 解析失败的域名
    from src.agent.core.url_guard import is_url_safe
    if not is_url_safe(url):
        return ToolResult(
            status="error",
            content=(
                f"URL 被安全策略拒绝（须为公网 http(s)，禁内网 IP / localhost / "
                f"file:// 等），收到：{url!r}"
            ),
        )

    raw = _fetch_raw_response(url)
    if isinstance(raw, ToolResult):
        return raw

    result = _extract_text_from_response(raw, max_chars)

    # SPA fallback：正文过短或含典型 SPA 根挂载点时，改用 Jina Reader
    if result.status in ("ok", "empty") and _is_likely_spa(result.content, raw.text):
        result = _fetch_via_jina(url, max_chars)

    # fetch_url 返回正文是"非用户主控"外部数据，过 security_filter；
    # 仅 ok 状态 wrap（empty/error 的 content 是程序错误描述，不该 wrap）。
    if result.status == "ok":
        from src.agent.core.security_filter import scrub_injection, wrap_untrusted
        cleaned, scrubbed = scrub_injection(result.content)
        flag = "[⚠️ 已清洗] " if scrubbed else ""
        wrapped = wrap_untrusted(f"{flag}{cleaned}", kind="web")
        return ToolResult(status="ok", content=wrapped)

    return result


# ── Plan-Execute tool 实现 ───────────────────────────────────────────────────


def _tool_make_plan(
    steps: Any,
    messages: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """生成 plan。本轮仅记录步骤、不联动执行 step 1（分轮执行 / two-stage）；下一轮 LLM 按 ack 指引推进。"""
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
    # 进度放在最前（[done/total]），保证前端截断预览（前 100 字符）也能稳定解析总步数
    if state.is_complete():
        return ToolResult(
            status="ok",
            content=(
                f"[{done}/{total}] {head}\n\nplan 已完成。请综合 plan 各步骤结果总结最终答案。"
            ),
        )
    nxt = state.next_pending_step()
    assert nxt is not None  # is_complete() is False 蕴含
    return ToolResult(
        status="ok",
        content=(
            f"[{done}/{total}] {head}\n"
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


# ── 学习计划业务 tool 实现 ───────────────────────────────────────────────────
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


# ── Quiz 业务三 tool JSON Schema ─────────────────────────────────────────────
# 与 _STUDY_PLAN_TOOLS（学习计划长期跟踪）相对：本组 tool 操作的是**周期性
# 自检练习 + 跨 session 复盘**（quiz_sets / quiz_questions 二表）。
# 触发主路径见 [quiz-maker skill](../../.agenta/skills/quiz-maker/SKILL.md)。

_QUIZ_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_quiz",
            "description": (
                "新建一个**跨 session 持久化的 quiz**，把题目清单一次性落库。"
                "适用：用户表达明确出题需求（如『考考我 RAG』/『给我出 5 道 ML 题』/"
                "『把 active 学习计划 stage 2 出成题』）。"
                "**不要**直接用本 tool —— 应先按 quiz-maker skill 引导用 make_plan 拆解"
                "（解析意图 → 查 KB → 出题 → 落库），落库即调本 tool。"
                "题型按固定 60% MCQ + 40% 简答比例混合；总题数由 questions 列表长度决定，"
                "用户未指定时建议 10 道。"
                "至少传 topic 或 plan_id 之一；返回新 quiz_set_id 与题数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "出题主题一句话描述，如『RAG 检索基础』/『Python 列表与字典』。"
                            "传 plan_id+stage_idx 时可省，工具会用 plan goal + stage 自动填。"
                        ),
                    },
                    "plan_id": {
                        "type": "integer",
                        "description": "可选关联 learning_plan id（绑定到学习计划，便于跨 session 按 plan 查 quiz 历史）。",
                        "minimum": 1,
                    },
                    "stage_idx": {
                        "type": "integer",
                        "description": "可选关联 plan stage（需同时传 plan_id；标明本 quiz 针对哪个学习阶段）。",
                        "minimum": 1,
                    },
                    "questions": {
                        "type": "array",
                        "description": (
                            "题目清单（5-15 道；按题号顺序）；每项 "
                            "{order_idx, q_type, stem, options?, correct_answer, explanation?}。"
                            "order_idx：题号从 1 起；"
                            "q_type：mcq_single / mcq_multi / short_answer 三选一；"
                            "stem：题干文本；"
                            "options：MCQ 选项列表（如 ['北京', '上海', '广州', '深圳']，简答留空 / 省略）；"
                            "correct_answer：MCQ 填字母串（单选 『B』 / 多选 『AC』）；简答填标准答案文本；"
                            "explanation：可选简短考点说明（≤ 80 字）。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "order_idx": {"type": "integer", "minimum": 1},
                                "q_type": {
                                    "type": "string",
                                    "enum": ["mcq_single", "mcq_multi", "short_answer"],
                                },
                                "stem": {"type": "string"},
                                "options": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "correct_answer": {"type": "string"},
                                "explanation": {"type": "string"},
                            },
                            "required": ["order_idx", "q_type", "stem", "correct_answer"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["questions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grade_quiz",
            "description": (
                "批改一个已生成的 quiz：用户在上一轮自然语言作答（如『1.B 2.AC 3. xxx』）→ "
                "你（LLM）根据上下文把答案拼成 {question_id: answer} dict 传给本 tool。"
                "工具按 q_type 分派批改：MCQ 走字符串归一化比对；简答走内置 LLM-judge 给 0-1 分 + 短反馈。"
                "返回总分 + 错题清单。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "quiz_set_id": {
                        "type": "integer",
                        "description": "要批改的 quiz_set_id（从 create_quiz 返回 / query_quiz_history 取得）。",
                        "minimum": 1,
                    },
                    "user_answers": {
                        "type": "object",
                        "description": (
                            "{question_id: 用户答案串} 映射。"
                            "MCQ 答案写字母（单选 『B』 / 多选 『AC』，不区分大小写）；简答写一句话或几句话。"
                            "key 是 question_id 整数字符串（如 『12』 / 『13』）；缺漏的题视为未作答记 0 分。"
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["quiz_set_id", "user_answers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_quiz_history",
            "description": (
                "查 quiz 历史 / 单个 quiz 详情。"
                "适用：用户问『我做过哪些 quiz / 上次 quiz 哪些错了 / 看下 quiz 5』。"
                "**三种查询方式互斥（按优先级取一种）**：① 传 quiz_set_id → 返该 quiz 题目清单"
                "（detail=True 时含 user_answer/correct_answer/反馈，用于复盘错题）；"
                "② 传 plan_id → 列该 plan 关联的全部 quiz 摘要；"
                "③ 都不传 → 列最近若干条 quiz 摘要（不含具体题目）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "quiz_set_id": {
                        "type": "integer",
                        "description": "可选；查单个 quiz 的题目清单。",
                        "minimum": 1,
                    },
                    "plan_id": {
                        "type": "integer",
                        "description": "可选；过滤某 learning_plan 关联的全部 quiz。",
                        "minimum": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "列表模式下返回的最多条数（默认 20）。",
                        "minimum": 1,
                    },
                    "detail": {
                        "type": "boolean",
                        "description": (
                            "仅 quiz_set_id 模式下生效：true 返回每题完整批改细节"
                            "（user_answer / correct_answer / score / feedback）；false 仅返回题干 + 题型。"
                            "默认 false 以节省 context。"
                        ),
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
]


# ── Quiz 业务 tool 实现 ──────────────────────────────────────────────────────
# 实现采用"按需 import + 复用共享 store"模式，与学习计划业务一致。


def _get_quiz_store() -> Any:
    """延迟 import，返回 quiz_store 模块级共享 store。"""
    from src.memory.quiz_store import get_shared_store
    return get_shared_store()


_QUIZ_LETTER_SET: frozenset[str] = frozenset("ABCDEFGH")
_MCQ_TYPES: tuple[str, ...] = ("mcq_single", "mcq_multi")
_ALL_Q_TYPES: tuple[str, ...] = ("mcq_single", "mcq_multi", "short_answer")


def _normalize_mcq_answer(s: str) -> str:
    """归一化 MCQ 答案串：『a,c』/『 AC 』/『abc』→ 『AC』（去重 + 升序 + 大写 A-H）。"""
    if not isinstance(s, str):
        return ""
    letters = sorted({c.upper() for c in s if c.upper() in _QUIZ_LETTER_SET})
    return "".join(letters)


def _grade_one_mcq(user_ans: str, correct: str) -> tuple[float, str]:
    """MCQ 字符串比对：归一化后完全相等 → 1.0；未作答 / 不等 → 0.0。"""
    u = _normalize_mcq_answer(user_ans)
    c = _normalize_mcq_answer(correct)
    if not u:
        return 0.0, "未作答"
    if u == c:
        return 1.0, "正确"
    return 0.0, f"错（你答 {u}，正确 {c}）"


# 简答 LLM-judge system prompt：让 LLM 给 0-1 浮点分 + ≤ 60 字反馈
_SHORT_ANSWER_JUDGE_SYS = """你是一个简答题评卷员。给定题目、标准答案、用户答案，按语义贴合度评分（0.0-1.0）：
- 1.0：完全正确，关键要点全覆盖
- 0.6-0.9：部分对（漏点 / 边角错 / 表述欠准）
- 0.1-0.5：思路对但关键点错 / 缺失多
- 0.0：完全错误或离题

只输出严格 JSON，格式：{"score": <0-1 浮点>, "feedback": "<≤ 60 字简评>"}。不要 markdown / 前后说明。"""


def _grade_one_short_answer(stem: str, user_ans: str, correct: str) -> tuple[float, str]:
    """简答 LLM-judge：内置 chat 调用；任何失败软返回 (0.0, 错误说明)。"""
    if not user_ans.strip():
        return 0.0, "未作答"
    user_msg = (
        f"## 题目\n{stem}\n\n"
        f"## 标准答案\n{correct}\n\n"
        f"## 用户答案\n{user_ans}"
    )
    try:
        from src.llm.provider import chat as _chat
        resp = _chat(
            [{"role": "system", "content": _SHORT_ANSWER_JUDGE_SYS},
             {"role": "user", "content": user_msg}],
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("[tool] short_answer judge LLM 失败: %s", e)
        return 0.0, f"批改异常：{e}"

    m = re.search(r"\{.*?\}", raw.replace("\n", " "), re.DOTALL)
    if not m:
        return 0.0, "judge 返回非 JSON"
    try:
        data = json.loads(m.group(0))
        score = max(0.0, min(1.0, float(data.get("score", 0))))
        fb = str(data.get("feedback", "")).strip()[:120] or "（无反馈）"
        return score, fb
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return 0.0, f"judge JSON 解析失败：{e}"


def _tool_create_quiz(
    questions: Any,
    topic: Any = None,
    plan_id: Any = None,
    stage_idx: Any = None,
) -> ToolResult:
    """新建跨 session 持久化 quiz + 全量 questions 一次性落库。"""
    # —— 入参校验 ——
    if topic is not None and not isinstance(topic, str):
        return ToolResult(status="error", content=f"create_quiz(topic) 必须是字符串，收到：{topic!r}")
    if plan_id is not None and (not isinstance(plan_id, int) or plan_id < 1):
        return ToolResult(status="error", content=f"create_quiz(plan_id) 必须是 ≥1 整数，收到：{plan_id!r}")
    if stage_idx is not None and (not isinstance(stage_idx, int) or stage_idx < 1):
        return ToolResult(status="error", content=f"create_quiz(stage_idx) 必须是 ≥1 整数，收到：{stage_idx!r}")
    if stage_idx is not None and plan_id is None:
        return ToolResult(status="error", content="create_quiz(stage_idx) 必须同时传 plan_id。")

    topic_clean = (topic or "").strip() if isinstance(topic, str) else ""
    if not topic_clean and plan_id is None:
        return ToolResult(
            status="error",
            content="create_quiz 至少要传 topic 或 plan_id 之一（说明这次 quiz 是什么主题 / 关联哪个学习计划）。",
        )

    if not isinstance(questions, list) or not questions:
        return ToolResult(
            status="error",
            content="create_quiz(questions) 必须是非空列表，每项 {order_idx, q_type, stem, options?, correct_answer, explanation?}。",
        )

    cleaned: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            return ToolResult(status="error", content=f"questions[{i}] 不是 dict，收到 {type(q).__name__}")
        order_idx = q.get("order_idx")
        q_type = q.get("q_type")
        stem = q.get("stem")
        correct_answer = q.get("correct_answer")
        if not isinstance(order_idx, int) or order_idx < 1:
            return ToolResult(status="error", content=f"questions[{i}].order_idx 必须 ≥1 整数：{order_idx!r}")
        if q_type not in _ALL_Q_TYPES:
            return ToolResult(
                status="error",
                content=f"questions[{i}].q_type 必须是 mcq_single / mcq_multi / short_answer：{q_type!r}",
            )
        if not isinstance(stem, str) or not stem.strip():
            return ToolResult(status="error", content=f"questions[{i}].stem 必须是非空字符串")
        if not isinstance(correct_answer, str) or not correct_answer.strip():
            return ToolResult(status="error", content=f"questions[{i}].correct_answer 必须是非空字符串")
        # MCQ 必须有 options
        options = q.get("options") or []
        if q_type in _MCQ_TYPES:
            if not isinstance(options, list) or len(options) < 2:
                return ToolResult(
                    status="error",
                    content=f"questions[{i}] MCQ 必须有 options 列表（≥ 2 项），收到 {options!r}",
                )
        cleaned.append({
            "order_idx": order_idx,
            "q_type": q_type,
            "stem": stem.strip(),
            "options": options if q_type in _MCQ_TYPES else [],
            "correct_answer": correct_answer.strip(),
            "explanation": (q.get("explanation") or "").strip(),
        })

    # plan_id + stage_idx 时若 topic 缺，从 LearningPlanStore 拉 goal 作 topic
    if not topic_clean and plan_id is not None:
        try:
            from src.memory.learning_plan_store import get_shared_store as _lp_store
            plan = _lp_store().get_plan(plan_id)
            if plan is None:
                return ToolResult(status="error", content=f"plan_id={plan_id} 不存在，无法派生 topic；请显式传 topic。")
            topic_clean = plan["goal"]
            if stage_idx is not None:
                topic_clean = f"{topic_clean} - Stage {stage_idx}"
        except Exception as e:
            return ToolResult(status="error", content=f"读取关联学习计划失败：{e}")

    # —— 落库 ——
    store = _get_quiz_store()
    try:
        quiz_set_id = store.create_quiz_set(
            topic=topic_clean,
            num_questions=len(cleaned),
            plan_id=plan_id,
            stage_idx=stage_idx,
        )
        added = store.add_questions(quiz_set_id, cleaned)
    except Exception as e:
        return ToolResult(status="error", content=f"持久化 quiz 失败 — {e}")

    plan_suffix = f"，关联 plan_id={plan_id}" + (f"/Stage {stage_idx}" if stage_idx else "") if plan_id else ""
    return ToolResult(
        status="ok",
        content=(
            f"✓ 已创建 quiz_set_id={quiz_set_id}：\"{topic_clean}\"{plan_suffix}，"
            f"含 {added} 道题。\n"
            "→ 可向用户依次呈现题目（含 ABCD 选项 / 简答提示），并提示：作答时按 "
            "『1.B 2.AC 3. <文字>』格式回复；你之后会用 grade_quiz 自动批改。"
        ),
    )


def _tool_grade_quiz(
    quiz_set_id: Any,
    user_answers: Any,
) -> ToolResult:
    """批改指定 quiz：MCQ 字符串比对 + 简答 LLM-judge；落库并返回总分 + 错题清单。"""
    if not isinstance(quiz_set_id, int) or quiz_set_id < 1:
        return ToolResult(
            status="error",
            content=f"grade_quiz(quiz_set_id) 必须是 ≥1 整数，收到：{quiz_set_id!r}",
        )
    if not isinstance(user_answers, dict):
        return ToolResult(
            status="error",
            content=(
                "grade_quiz(user_answers) 必须是 dict {question_id: 答案串}；"
                f"收到 {type(user_answers).__name__}"
            ),
        )

    store = _get_quiz_store()
    quiz = store.get_quiz_with_questions(quiz_set_id)
    if quiz is None:
        return ToolResult(status="error", content=f"quiz_set_id={quiz_set_id} 不存在")
    if quiz["status"] == "archived":
        return ToolResult(status="error", content=f"quiz_set_id={quiz_set_id} 已 archived，无法批改")

    questions: list[dict[str, Any]] = quiz["questions"]
    if not questions:
        return ToolResult(status="error", content=f"quiz_set_id={quiz_set_id} 没有题目，无法批改")

    # —— 归一化 user_answers key：支持 int / str ——
    normalized: dict[int, str] = {}
    for k, v in user_answers.items():
        try:
            qid = int(k)
        except (ValueError, TypeError):
            continue
        normalized[qid] = str(v or "")

    # —— 逐题批改 ——
    gradings: list[dict[str, Any]] = []
    total_raw = 0.0
    wrong_lines: list[str] = []
    for q in questions:
        qid = q["id"]
        user_ans = normalized.get(qid, "")
        q_type = q["q_type"]
        if q_type in _MCQ_TYPES:
            score, fb = _grade_one_mcq(user_ans, q["correct_answer"])
        else:
            score, fb = _grade_one_short_answer(q["stem"], user_ans, q["correct_answer"])
        gradings.append({
            "question_id": qid,
            "user_answer": user_ans,
            "score": score,
            "feedback": fb,
        })
        total_raw += score
        if score < 1.0:
            order = q["order_idx"]
            stem_short = q["stem"][:40] + ("…" if len(q["stem"]) > 40 else "")
            wrong_lines.append(
                f"  第 {order} 题 [{q_type}] {score:.1f}/1.0 — {fb}\n"
                f"    题干：{stem_short}\n"
                f"    标答：{q['correct_answer']}"
            )

    total_score = round(total_raw * 100.0 / len(questions), 1)
    ok = store.update_grading(quiz_set_id, gradings, total_score)
    if not ok:
        return ToolResult(status="error", content=f"持久化批改结果失败（quiz_set_id={quiz_set_id}）")

    # —— critic 自检（仅 short_answer，MCQ 字符串比对是确定性的，跳过省 token） ——
    harness_block = ""
    if _cfg.HARNESS_QUIZ_ENABLED:
        harness_block = _run_quiz_critic(quiz_set_id, questions, gradings, store)

    head = (
        f"📝 已批改 quiz_set_id={quiz_set_id}『{quiz['topic']}』：\n"
        f"  总分 {total_score:.1f}/100（{int(round(total_raw))}/{len(questions)} 题完全正确）"
    )
    if wrong_lines:
        body = "\n\n薄弱点 / 错题（{n} 道）：\n{lines}".format(n=len(wrong_lines), lines="\n".join(wrong_lines))
    else:
        body = "\n\n🎉 全部正确！"
    tail = (
        "\n\n→ 可向用户展示总分 + 错题点评（含标答 + 简短考点），"
        "再问是否要『再考一次 / 换主题 / 看错题详情』。"
    )
    return ToolResult(status="ok", content=head + body + harness_block + tail)


def _run_quiz_critic(
    quiz_set_id: int,
    questions: list[dict[str, Any]],
    gradings: list[dict[str, Any]],
    store: Any,
) -> str:
    """对简答题批改结果做 critic 自检；不达标的题落库 mark + 拼成 warning 块。

    返回值：插入 grade_quiz 输出 head + body 后、tail 前的 harness_warning 段（含前导 `\\n\\n`）；
    无 flagged 题 / 全部 critic 失败 → 返回空串（不在输出里制造噪音）。
    Critic 自身异常一律软返回，不影响主输出。
    """
    try:
        from src.agent.core.harness_manager import get_harness_manager
        manager = get_harness_manager()
    except Exception as e:  # noqa: BLE001 — manager 初始化失败不阻塞 grade_quiz
        logger.warning("[tool] grade_quiz: HarnessManager 加载失败，跳过自检：%s", e)
        return ""

    qmap = {q["id"]: q for q in questions}
    flagged_lines: list[str] = []
    reviewed = 0
    for g in gradings:
        qid = g["question_id"]
        q = qmap.get(qid)
        if q is None or q["q_type"] in _MCQ_TYPES:
            continue
        reviewed += 1
        verdict = manager.review_grading(
            stem=q["stem"],
            user_answer=g["user_answer"],
            correct_answer=q["correct_answer"],
            agent_score=float(g["score"]),
            agent_feedback=g["feedback"],
        )
        if verdict.failure or verdict.passed:
            continue
        store.mark_question_harness_flagged(qid)
        score_str = f"{verdict.score:.1f}" if verdict.score is not None else "—"
        flagged_lines.append(
            f"  第 {q['order_idx']} 题 — critic {score_str}/5：{verdict.reason}"
        )

    if reviewed == 0 or not flagged_lines:
        if reviewed:
            logger.info(
                "[tool] grade_quiz: critic 复审 %d 题，全部通过（quiz_set_id=%d）",
                reviewed, quiz_set_id,
            )
        return ""

    logger.info(
        "[tool] grade_quiz: critic 复审 %d 题，flagged %d 题（quiz_set_id=%d）",
        reviewed, len(flagged_lines), quiz_set_id,
    )
    return (
        f"\n\n⚠️ Agent 自检：以下 {len(flagged_lines)} 题批改可能有偏，"
        f"建议人工复核：\n" + "\n".join(flagged_lines)
    )


def _render_quiz_brief(quiz_set: dict[str, Any]) -> str:
    """单 quiz_set 摘要（query_quiz_history 列表模式 + CLI list 共用）。"""
    qid = quiz_set["id"]
    status = quiz_set["status"]
    n = quiz_set["num_questions"]
    topic = (quiz_set["topic"] or "")[:40]
    score = quiz_set.get("total_score")
    score_part = f"{score:.1f}/100" if isinstance(score, (int, float)) else "—"
    plan_suffix = ""
    if quiz_set.get("plan_id"):
        plan_suffix = f"，plan {quiz_set['plan_id']}"
        if quiz_set.get("stage_idx"):
            plan_suffix += f".S{quiz_set['stage_idx']}"
    return (
        f"- quiz_set_id={qid} [{status}] 「{topic}」 {n}题 / 总分 {score_part}{plan_suffix}"
    )


def _render_quiz_detail(quiz: dict[str, Any], include_grading: bool) -> str:
    """单 quiz 详情：题目清单（detail=True 时含批改细节）。"""
    head_lines = [
        f"## quiz_set_id={quiz['id']}「{quiz['topic']}」 [{quiz['status']}]",
        f"- 题数：{quiz['num_questions']}",
    ]
    if quiz.get("plan_id"):
        head_lines.append(
            f"- 关联：plan_id={quiz['plan_id']}"
            + (f" Stage {quiz['stage_idx']}" if quiz.get("stage_idx") else "")
        )
    if quiz.get("total_score") is not None:
        head_lines.append(f"- 总分：{quiz['total_score']:.1f}/100")
    head_lines.append("")

    body: list[str] = []
    for q in quiz.get("questions", []):
        body.append(f"**第 {q['order_idx']} 题** [{q['q_type']}]")
        body.append(q["stem"])
        if q["q_type"] in _MCQ_TYPES and q["options"]:
            for i, opt in enumerate(q["options"]):
                letter = chr(ord("A") + i)
                body.append(f"  {letter}. {opt}")
        if include_grading:
            body.append(f"  标答：{q['correct_answer']}")
            if q.get("user_answer"):
                body.append(f"  你的答案：{q['user_answer']}")
            if q.get("score") is not None:
                body.append(f"  得分：{q['score']:.1f}/1.0  反馈：{q.get('feedback', '')}")
            if q.get("explanation"):
                body.append(f"  考点：{q['explanation']}")
        body.append("")
    return "\n".join(head_lines + body).rstrip()


def _tool_query_quiz_history(
    quiz_set_id: Any = None,
    plan_id: Any = None,
    limit: Any = None,
    detail: bool = False,
) -> ToolResult:
    """三路径：单 quiz 详情 / plan 关联 quiz 列表 / 全局 quiz 列表。"""
    store = _get_quiz_store()

    # —— 路径 1：quiz_set_id 单查 ——
    if quiz_set_id is not None:
        if not isinstance(quiz_set_id, int) or quiz_set_id < 1:
            return ToolResult(
                status="error",
                content=f"query_quiz_history(quiz_set_id) 必须是 ≥1 整数，收到：{quiz_set_id!r}",
            )
        quiz = store.get_quiz_with_questions(quiz_set_id)
        if quiz is None:
            return ToolResult(status="error", content=f"quiz_set_id={quiz_set_id} 不存在")
        return ToolResult(
            status="ok",
            content=_render_quiz_detail(quiz, include_grading=bool(detail)),
        )

    # —— limit 归一化（plan_id / 全局都用） ——
    if limit is None:
        eff_limit = _cfg.QUIZ_HISTORY_LIST_LIMIT
    elif isinstance(limit, int) and limit > 0:
        eff_limit = limit
    else:
        return ToolResult(
            status="error",
            content=f"query_quiz_history(limit) 必须是 ≥1 整数，收到：{limit!r}",
        )

    # —— 路径 2：plan_id 过滤 ——
    if plan_id is not None:
        if not isinstance(plan_id, int) or plan_id < 1:
            return ToolResult(
                status="error",
                content=f"query_quiz_history(plan_id) 必须是 ≥1 整数，收到：{plan_id!r}",
            )
        quizzes = store.list_quiz_sets(plan_id=plan_id, limit=eff_limit)
        if not quizzes:
            return ToolResult(status="empty", content=f"plan_id={plan_id} 无关联 quiz。")
        lines = [f"# plan_id={plan_id} 关联 quiz（共 {len(quizzes)} 个）"]
        lines += [_render_quiz_brief(q) for q in quizzes]
        return ToolResult(status="ok", content="\n".join(lines))

    # —— 路径 3：全局列表 ——
    quizzes = store.list_quiz_sets(limit=eff_limit)
    if not quizzes:
        return ToolResult(status="empty", content="暂无 quiz 历史。可让用户说『考考我 X』新建一个。")
    lines = [f"# 最近 quiz 历史（共 {len(quizzes)} 个）"]
    lines += [_render_quiz_brief(q) for q in quizzes]
    return ToolResult(status="ok", content="\n".join(lines))


# ── SRS 主动复习业务 tool 定义 ───────────────────────────────────────────────
# 与 _QUIZ_TOOLS（周期性自检练习，一次性出题 + 批改）相对：本组 tool 操作的是
# **跨 session 持久化的 SRS 队列**，按 SM-2 算法按"下次该复习的时刻"调度卡片，
# 用户用 again / hard / good / easy 4 档自评后自动更新调度状态。

_SRS_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add_to_srs",
            "description": (
                "把卡片加入 SRS 复习队列（跨 session 持久化）。两种 source_type："
                "① `quiz_question` — 通常用于把 grade_quiz 后的错题进 SRS（传 question_ids 数组批量）；"
                "② `manual` — 用户手动加自定义卡（传 front 题面 + back 答案）。"
                "新卡 next_review_at 初始化为 now（立即 due，用户首次 review 后 SM-2 给出真正 interval）。"
                "已存在 active/suspended 卡片不重复添加。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "enum": ["quiz_question", "manual"],
                        "description": "卡片来源：quiz_question 用 question_ids 批量；manual 用 front+back 单卡。",
                    },
                    "question_ids": {
                        "type": "array",
                        "description": (
                            "source_type=quiz_question 时必填：question_id 整数数组。"
                            "通常从 grade_quiz 返回的错题清单 / query_quiz_history(detail=true) 拿到。"
                        ),
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "front": {
                        "type": "string",
                        "description": "source_type=manual 时必填：卡片正面（题面 / 提示）。",
                    },
                    "back": {
                        "type": "string",
                        "description": "source_type=manual 时必填：卡片背面（答案 / 解释）。",
                    },
                    "note": {
                        "type": "string",
                        "description": "可选自由备注（≤ 200 字）；如『错题复盘』『面试重点』等标签。",
                    },
                },
                "required": ["source_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_srs_due",
            "description": (
                "查询当前 due（next_review_at <= now 且 status=active）的卡片。"
                "适用：用户问『今天有什么要复习 / 给我出 due 的卡片 / 把 SRS 卡片背一下』。"
                "默认 detail=false 返回摘要列表（id + 题面前 40 字 + 当前 interval + ease）；"
                "detail=true 返回完整 front + back（用户开始 review 时取此）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回 due 卡最多条数；默认 20。",
                        "minimum": 1,
                    },
                    "detail": {
                        "type": "boolean",
                        "description": "true 返回完整 front+back；false 仅摘要。默认 false 节省 context。",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_srs_card",
            "description": (
                "用户对一张 due 卡片完成回忆 + 4 档自评后调用，更新该卡的 SM-2 调度状态："
                "ease_factor / interval_days / repetitions / lapses / next_review_at。"
                "rating 四档语义：`again` = 完全忘了（重置）/ `hard` = 想起来但费劲（间隔略缩）/ "
                "`good` = 正常答对（走 SM-2 主公式）/ `easy` = 太简单（间隔加成）。"
                "返回新 interval + next_review_at 给用户反馈。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {
                        "type": "integer",
                        "description": "要 review 的卡片 id（从 query_srs_due 返回）。",
                        "minimum": 1,
                    },
                    "rating": {
                        "type": "string",
                        "enum": ["again", "hard", "good", "easy"],
                        "description": "用户 4 档自评（Anki 风格）。",
                    },
                },
                "required": ["card_id", "rating"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_srs_stats",
            "description": (
                "返回 SRS 队列摘要统计：总 active 卡 / suspended / archived 数 / 当前 due 数 / "
                "平均 ease / mature 卡（interval ≥ 21 天）数。适用："
                "用户问『我的 SRS 队列有多少卡 / 我对哪类题最弱 / 已经背熟多少』。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ── SRS 业务 tool 实现 ───────────────────────────────────────────────────────


def _get_srs_store() -> Any:
    """延迟 import，返回 srs_store 模块级共享 store。"""
    from src.memory.srs_store import get_shared_store
    return get_shared_store()


# MCQ 题型在 quiz_questions 表里的 q_type 值
_QUIZ_MCQ_TYPES: tuple[str, ...] = ("mcq_single", "mcq_multi")


def _build_card_text_from_question(
    *,
    q_type: str,
    stem: str,
    options_raw: str,
    correct_answer: str,
    explanation: str,
) -> tuple[str, str]:
    """
    把 quiz_questions 行转成 SRS 卡的 (front, back)。

    MCQ 题型：
        - front：`[题型标签] 题干\n\nA. <option0>\nB. <option1>\n...`
        - back：`<correct letters> — <对应选项文本>\n\n考点：<explanation>`
        （多选时选项文本用 ` / ` 分隔；标答字母解析失败则只给字母）
    简答题型：
        - front：`[简答] 题干`
        - back：`<correct_answer>\n\n考点：<explanation>`

    options_raw 解析失败（JSON 损坏）时降级：MCQ 也走"简答"格式，避免卡 add 失败。
    """
    stem = (stem or "").strip()
    correct = (correct_answer or "").strip()
    expl = (explanation or "").strip()

    options: list[str] = []
    if q_type in _QUIZ_MCQ_TYPES and options_raw:
        try:
            parsed = json.loads(options_raw)
            if isinstance(parsed, list):
                options = [str(o).strip() for o in parsed if str(o).strip()]
        except (json.JSONDecodeError, TypeError):
            options = []

    if q_type in _QUIZ_MCQ_TYPES and options:
        type_tag = "单选" if q_type == "mcq_single" else "多选"
        # front：题干 + 编号选项（A. xx / B. yy ...）
        option_lines = [
            f"{chr(ord('A') + i)}. {opt}" for i, opt in enumerate(options)
        ]
        front = f"[{type_tag}] {stem}\n\n" + "\n".join(option_lines)

        # back：把标答字母（"C" / "AC"）翻成对应选项文本，方便用户复习时核对
        letter_to_text: dict[str, str] = {
            chr(ord("A") + i): opt for i, opt in enumerate(options)
        }
        letters = [c for c in correct.upper() if "A" <= c <= "Z"]
        mapped = [letter_to_text[c] for c in letters if c in letter_to_text]
        if mapped:
            back_head = f"{''.join(letters)} — {' / '.join(mapped)}"
        else:
            # 标答字母对不上选项（数据脏 / 简答题误填字母）→ 只给原始 correct
            back_head = correct
        back = back_head + (f"\n\n考点：{expl}" if expl else "")
        return front, back

    # short_answer 或 MCQ options 缺失降级 → 题干 + 标答 + 考点
    front = (f"[简答] {stem}" if q_type == "short_answer" else stem) if stem else ""
    back = correct + (f"\n\n考点：{expl}" if expl else "")
    return front, back


def _render_card_brief(card: dict[str, Any]) -> str:
    """单卡摘要一行：id + 状态 + ease + interval + 题干前 50 字（不带多行选项）。

    MCQ 卡的 front 含 ABCD 多行选项 — 摘要只取第一行避免污染 LLM context；
    用户开始复习时 LLM 会用 detail=true 拿完整 front+back。
    """
    cid = card["id"]
    status = card["status"]
    ef = card["ease_factor"]
    iv = card["interval_days"]
    reps = card["repetitions"]
    first_line = (card["front"] or "").split("\n", 1)[0].strip()
    front_short = first_line[:50] + ("…" if len(first_line) > 50 else "")
    src = card["source_type"]
    src_suffix = (
        f"（quiz_q#{card['source_ref']}）" if src == "quiz_question" and card["source_ref"] else "（manual）"
    )
    return (
        f"  [{cid:>3d}] [{status:<8}] ef={ef:.2f} iv={iv}d reps={reps}  "
        f"{front_short}{src_suffix}"
    )


def _render_card_detail(card: dict[str, Any]) -> str:
    """单卡完整呈现：front + back + 全部调度字段。"""
    lines = [
        f"## 卡片 #{card['id']}（{card['source_type']}，{card['status']}）",
        f"**正面**：{card['front']}",
        f"**背面**：{card['back']}",
    ]
    if card.get("note"):
        lines.append(f"**备注**：{card['note']}")
    lines += [
        "",
        f"- ease_factor: {card['ease_factor']:.2f}",
        f"- interval_days: {card['interval_days']}",
        f"- repetitions: {card['repetitions']}",
        f"- lapses: {card['lapses']}",
        f"- next_review_at: {card['next_review_at']}",
        f"- last_reviewed_at: {card['last_reviewed_at'] or '（未 review）'}",
        f"- created_at: {card['created_at']}",
    ]
    return "\n".join(lines)


def _tool_add_to_srs(
    source_type: Any,
    question_ids: Any = None,
    front: Any = None,
    back: Any = None,
    note: Any = None,
) -> ToolResult:
    """新增 SRS 卡：quiz_question 批量 / manual 单卡两条路径。"""
    if not isinstance(source_type, str) or source_type not in ("quiz_question", "manual"):
        return ToolResult(
            status="error",
            content=f"add_to_srs(source_type) 必须是 'quiz_question' 或 'manual'，收到：{source_type!r}",
        )
    note_str = str(note or "")[:200] if note is not None else ""
    store = _get_srs_store()

    if source_type == "manual":
        if not isinstance(front, str) or not front.strip():
            return ToolResult(status="error", content="add_to_srs(manual) 必须传非空 front（卡片正面）。")
        if not isinstance(back, str) or not back.strip():
            return ToolResult(status="error", content="add_to_srs(manual) 必须传非空 back（卡片背面）。")
        try:
            card_id = store.add_card(
                source_type="manual",
                front=front.strip(),
                back=back.strip(),
                source_ref=None,
                note=note_str,
            )
        except Exception as e:
            return ToolResult(status="error", content=f"add_to_srs 落库失败 — {e}")
        return ToolResult(
            status="ok",
            content=(
                f"✓ 已新建 manual 卡：card_id={card_id}\n"
                f"  正面：{front.strip()[:60]}\n"
                f"  立即 due — 用户可用 query_srs_due 查到这张卡开始复习。"
            ),
        )

    # source_type == "quiz_question" 批量路径
    if not isinstance(question_ids, list) or not question_ids:
        return ToolResult(
            status="error",
            content="add_to_srs(source_type=quiz_question) 必须传非空 question_ids 整数数组。",
        )

    # 从 QuizStore 拿题面 + 标答作为冗余存储
    try:
        from src.memory.quiz_store import get_shared_store as _qz_store
        quiz_store = _qz_store()
    except Exception as e:
        return ToolResult(status="error", content=f"读取 QuizStore 失败 — {e}")

    added: list[int] = []
    skipped: list[tuple[int, str]] = []
    for qid in question_ids:
        if not isinstance(qid, int) or qid < 1:
            skipped.append((qid if isinstance(qid, int) else -1, f"非法 question_id {qid!r}"))
            continue
        # 防重复：已存在 active / suspended 卡（archived 不算）→ 跳过
        existing = store.card_exists_for_source("quiz_question", qid)
        if existing is not None:
            skipped.append((qid, f"已有 card_id={existing}，跳过"))
            continue
        # 反查题面 / 题型 / 选项 / 标答 / 考点 —— MCQ 必须把选项也带进 front，
        # 标答字母也要映射到选项文本，否则用户复习时只看到题干 + 孤零零的"C"无法判正确
        question_row = quiz_store._conn.execute(  # noqa: SLF001 — 内部 join 查询
            "SELECT q_type, stem, options, correct_answer, explanation "
            "FROM quiz_questions WHERE id = ?",
            (qid,),
        ).fetchone()
        if question_row is None:
            skipped.append((qid, "question_id 不存在于 quiz_questions"))
            continue
        q_type = str(question_row["q_type"] or "").strip()
        stem = str(question_row["stem"] or "").strip()
        correct = str(question_row["correct_answer"] or "").strip()
        expl = str(question_row["explanation"] or "").strip()
        front_text, back_text = _build_card_text_from_question(
            q_type=q_type,
            stem=stem,
            options_raw=str(question_row["options"] or ""),
            correct_answer=correct,
            explanation=expl,
        )
        if not front_text or not back_text:
            skipped.append((qid, "题面 / 标答为空，跳过"))
            continue
        try:
            card_id = store.add_card(
                source_type="quiz_question",
                front=front_text,
                back=back_text,
                source_ref=qid,
                note=note_str,
            )
            added.append(card_id)
        except Exception as e:  # noqa: BLE001
            skipped.append((qid, f"落库失败 — {e}"))

    lines = [f"✓ add_to_srs 完成：新增 {len(added)} 张卡，跳过 {len(skipped)} 张。"]
    if added:
        lines.append(f"  新增 card_id: {added}")
    for qid, reason in skipped:
        lines.append(f"  跳过 question_id={qid}：{reason}")
    if not added and skipped:
        # 全部跳过 → 视作 empty 而非 ok（避免 LLM 误以为全成功）
        return ToolResult(status="empty", content="\n".join(lines))
    return ToolResult(status="ok", content="\n".join(lines))


def _tool_query_srs_due(
    limit: Any = None,
    detail: Any = False,
) -> ToolResult:
    """列当前 due 卡片（active 且 next_review_at <= now）。"""
    eff_limit: int | None = None
    if limit is not None:
        if not isinstance(limit, int) or limit < 1:
            return ToolResult(status="error", content=f"query_srs_due(limit) 必须是 ≥1 整数，收到：{limit!r}")
        eff_limit = limit
    store = _get_srs_store()
    cards = store.list_due(limit=eff_limit)
    if not cards:
        return ToolResult(
            status="empty",
            content=(
                "🎉 当前没有 due 卡片需要复习。可让用户新建 quiz 后把错题进 SRS / 手动加 manual 卡。"
            ),
        )
    lines = [f"# 当前 due 卡片（{len(cards)} 张）"]
    if detail:
        # 完整详情模式：每张卡渲染 front + back + 调度字段
        for c in cards:
            lines.append("")
            lines.append(_render_card_detail(c))
            lines.append("")
            lines.append("→ 用户回忆后，调 review_srs_card(card_id, rating='again'/'hard'/'good'/'easy')")
    else:
        lines.append(f"  {'ID':<6}  {'状态':<10}  {'ease':<6}  {'iv':<5}  {'reps':<5}  正面（前 40 字）")
        lines.append(f"  {'-'*4:<6}  {'-'*8:<10}  {'-'*4:<6}  {'-'*3:<5}  {'-'*3:<5}  {'-'*40}")
        for c in cards:
            lines.append(_render_card_brief(c))
        lines.append("")
        lines.append("→ 用户开始复习时，先调 query_srs_due(detail=true) 拿完整正面 / 背面；")
        lines.append("  用户回忆后用 review_srs_card(card_id, rating) 更新调度。")
    return ToolResult(status="ok", content="\n".join(lines))


def _tool_review_srs_card(
    card_id: Any,
    rating: Any,
) -> ToolResult:
    """用户对一张卡完成回忆 + 4 档自评 → SM-2 公式更新调度状态。"""
    if not isinstance(card_id, int) or card_id < 1:
        return ToolResult(status="error", content=f"review_srs_card(card_id) 必须是 ≥1 整数，收到：{card_id!r}")
    if not isinstance(rating, str):
        return ToolResult(status="error", content=f"review_srs_card(rating) 必须是字符串，收到：{type(rating).__name__}")

    from src.agent.core.srs_scheduler import (
        card_state_from_dict,
        parse_rating,
        schedule_review,
    )

    try:
        rating_enum = parse_rating(rating)
    except ValueError as e:
        return ToolResult(status="error", content=str(e))

    store = _get_srs_store()
    card = store.get_card(card_id)
    if card is None:
        return ToolResult(status="error", content=f"card_id={card_id} 不存在")
    if card["status"] != "active":
        return ToolResult(
            status="error",
            content=f"card_id={card_id} 状态 {card['status']}，禁止 review（请先 resume / unarchive）",
        )

    state = card_state_from_dict(card)
    result = schedule_review(state, rating_enum)

    ok = store.update_review_state(
        card_id,
        ease_factor=result.ease_factor,
        interval_days=result.interval_days,
        repetitions=result.repetitions,
        lapses=result.lapses,
        next_review_at=result.next_review_at,
    )
    if not ok:
        return ToolResult(status="error", content=f"持久化 review 结果失败（card_id={card_id}）")

    return ToolResult(
        status="ok",
        content=(
            f"✓ card_id={card_id} 已完成 review（rating={rating_enum.value}）：\n"
            f"  新 ease={result.ease_factor:.2f}, interval={result.interval_days}d, "
            f"reps={result.repetitions}, lapses={result.lapses}\n"
            f"  下次复习：{result.next_review_at}\n"
            "→ 给用户简短反馈：本次评分 + 下次几天后回来。"
        ),
    )


def _tool_query_srs_stats() -> ToolResult:
    """返回 SRS 队列摘要统计。"""
    store = _get_srs_store()
    stats = store.stats()
    if stats["total_active"] == 0 and stats["total_suspended"] == 0 and stats["total_archived"] == 0:
        return ToolResult(
            status="empty",
            content="📭 SRS 队列为空。可让用户做完 quiz 后把错题进 SRS / 手动加 manual 卡。",
        )
    lines = [
        "# SRS 队列统计",
        f"- 总 active：{stats['total_active']} 张",
        f"- 总 suspended：{stats['total_suspended']} 张",
        f"- 总 archived：{stats['total_archived']} 张",
        f"- 当前 due：{stats['due_count']} 张",
        f"- 平均 ease：{stats['avg_ease']:.2f}（≥ 2.5 偏简单 / < 2.0 偏难）",
        f"- mature 卡（interval ≥ 21d）：{stats['mature_count']} 张",
    ]
    return ToolResult(status="ok", content="\n".join(lines))


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


# ── MCP namespaced tool 转发 ─────────────────────────────────────────────────


def _execute_mcp_tool(name: str, args: dict[str, Any]) -> ToolResult:
    """把 `<server>.<tool>` 调用转发到 MCPManager，返回值过 security_filter 包装。

    包装策略与 fetch_url 同步：仅 ok 状态 wrap kind="tool"；命中 injection 段被剔除时
    前缀 `[⚠️ 已清洗]`，与 web / doc 一致让 LLM 感知。

    MCPCallError（未连接 / 超时 / SDK 抛错）统一降级为 `status='error'` 让 ToolCallEngine
    继续引导 LLM 换工具，不向上抛。
    """
    from src.agent.core.mcp_manager import MCPCallError, get_shared_manager
    from src.agent.core.security_filter import scrub_injection, wrap_untrusted

    try:
        text = get_shared_manager().call_tool(name, args)
    except MCPCallError as exc:
        logger.warning("[tool] MCP %s 调用失败：%s", name, exc)
        return ToolResult(status="error", content=f"MCP 工具调用失败: {exc}")
    except Exception as exc:
        logger.warning("[tool] MCP %s 未预期异常：%s", name, exc)
        return ToolResult(status="error", content=f"MCP 工具异常: {exc}")

    cleaned, scrubbed = scrub_injection(text or "")
    flag = "[⚠️ 已清洗] " if scrubbed else ""
    wrapped = wrap_untrusted(f"{flag}{cleaned}", kind="tool")
    return ToolResult(status="ok", content=wrapped)


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
        citation_builder: 引用编排器；仅 search_knowledge 路径透传，
                          其它工具无引用语义直接忽略。
        messages: 当前轮已累计的 messages（含本次 assistant tool_calls）；仅 plan
                  tools（update_step / abort_plan）用于 reconstruct plan 状态。
                  非 plan 工具忽略此参数。

    Returns:
        ToolResult：status 为 "ok"/"empty"/"error"，content 为工具输出内容。
        任何异常（含未知工具名）均被捕获，以 status="error" 返回，不向上抛出。
    """
    logger.info("执行工具: %s，参数: %s", name, json.dumps(args, ensure_ascii=False))

    # 双层保险：get_tools 已按名单门过滤掉被屏蔽 tool，但 LLM 可能因
    # context 缓存 / 历史 messages 含旧 tool name 仍发起调用；此处再 double-check
    # 命中即拒绝（status=error 让 tool_call_engine 引导 LLM 换工具），防绕过。
    from src.agent.core.security_filter import is_tool_allowed
    if not is_tool_allowed(name):
        return ToolResult(
            status="error",
            content=f"工具 {name!r} 当前被名单门拒绝（SECURITY_MODE / TOOL_BLOCKLIST / TOOL_ALLOWLIST）。请改用其它工具或如实告知用户当前无法获取该信息。",
        )

    # namespaced tool 走 MCP 转发（强制 "<server>.<tool>"）
    if "." in name:
        return _execute_mcp_tool(name, args)

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
            case "create_quiz":
                return _tool_create_quiz(
                    questions=args.get("questions"),
                    topic=args.get("topic"),
                    plan_id=args.get("plan_id"),
                    stage_idx=args.get("stage_idx"),
                )
            case "grade_quiz":
                return _tool_grade_quiz(
                    quiz_set_id=args.get("quiz_set_id"),
                    user_answers=args.get("user_answers"),
                )
            case "query_quiz_history":
                return _tool_query_quiz_history(
                    quiz_set_id=args.get("quiz_set_id"),
                    plan_id=args.get("plan_id"),
                    limit=args.get("limit"),
                    detail=args.get("detail", False),
                )
            case "add_to_srs":
                return _tool_add_to_srs(
                    source_type=args.get("source_type"),
                    question_ids=args.get("question_ids"),
                    front=args.get("front"),
                    back=args.get("back"),
                    note=args.get("note"),
                )
            case "query_srs_due":
                return _tool_query_srs_due(
                    limit=args.get("limit"),
                    detail=args.get("detail", False),
                )
            case "review_srs_card":
                return _tool_review_srs_card(
                    card_id=args.get("card_id"),
                    rating=args.get("rating"),
                )
            case "query_srs_stats":
                return _tool_query_srs_stats()
            case _:
                return ToolResult(
                    status="error",
                    content=f"未知工具: '{name}'，支持的工具: {[t['function']['name'] for t in get_tools()]}",
                )
    except Exception as e:
        logger.warning("[tool] execute_tool 异常兜底: %s", e)
        return ToolResult(status="error", content=f"工具执行异常: {e}")
