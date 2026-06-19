"""
LangChain 工具适配 —— 把 `tools.py` 的全部 OpenAI 风格工具动态包装为 `StructuredTool`

设计（iter_a_LangChain.md §2.2）：
- 不为每个 tool 手写 StructuredTool，而是遍历 `get_tools(skill_bodies)` 的 JSON schema
  动态生成：`parameters` → pydantic 模型，`func` → 统一闭包路由到 `execute_tool`。
- 单一真相源：工具集合（含 plan/study/quiz/srs + MCP 合流 + 名单门过滤 + fetch_url
  屏蔽逻辑）与 Python / AutoGPT 完全一致。
- security_filter / critic 已在 `execute_tool` 内部，自动获得。
- `citation_getter`：per-run `CitationBuilder` 的取值闭包，仅 search_knowledge 路径用到
  （LangChainAgent 每轮 run() 重置；详 §4 引用对齐）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from src.agent.tools import execute_tool, get_tools

logger = logging.getLogger(__name__)

TOOL_EMPTY_HINT = (
    '\n\n[提示] 知识库未找到相关内容，请先调用 web_search 搜索相关关键词，'
    '再根据返回的真实 URL 调用 fetch_url 获取详情，不允许直接回答。'
)

# JSON schema type → python type（用于动态构造 pydantic 模型）
_TYPE_MAP: dict[str, type] = {
    'string': str,
    'integer': int,
    'number': float,
    'boolean': bool,
    'array': list,
    'object': dict,
}


# ── 向后兼容：保留显式 input 模型（历史测试 import）────────────────────────────
class SearchKnowledgeInput(BaseModel):
    query: str = Field(description='用于检索的自然语言查询句')
    top_k: int = Field(default=5, description='返回文档片段数')


class WebSearchInput(BaseModel):
    query: str = Field(description='搜索关键词，支持中英文')
    num: int = Field(default=5, description='返回结果条数，最多 10')


class FetchUrlInput(BaseModel):
    url: str = Field(description='要抓取的网页 URL，必须来自 web_search 返回的真实链接')
    max_chars: int = Field(default=3000, description='返回最大字符数')


# ── 动态包装核心 ──────────────────────────────────────────────────────────────
def _py_type(prop: dict[str, Any]) -> type:
    """JSON schema 单属性 → python 类型；联合类型取首个非 null。"""
    t = prop.get('type', 'string')
    if isinstance(t, list):
        t = next((x for x in t if x != 'null'), 'string')
    return _TYPE_MAP.get(t, str)


def _schema_to_model(tool_name: str, params: dict[str, Any]) -> type[BaseModel]:
    """把工具的 OpenAI `parameters` JSON schema 动态构造为 pydantic 模型。

    - required 字段：必填，无默认值；
    - 可选字段：用 schema 的 default（无则 None），类型放宽为 Optional。
    嵌套 object / array 统一降级为 dict / list，结构校验交给 `execute_tool`。
    """
    props: dict[str, Any] = params.get('properties', {}) or {}
    required = set(params.get('required', []) or [])
    fields: dict[str, Any] = {}
    for pname, prop in props.items():
        pytype = _py_type(prop)
        desc = prop.get('description', '')
        if pname in required:
            fields[pname] = (pytype, Field(description=desc))
        else:
            default = prop.get('default', None)
            if default is None:
                fields[pname] = (Optional[pytype], Field(default=None, description=desc))
            else:
                fields[pname] = (pytype, Field(default=default, description=desc))
    return create_model(f'{tool_name}_Args', **fields)


# 仅这几个内置工具在特定 status 下追加引导提示（与 Python ToolCallEngine 行为对齐）
def _decorate_hint(tool_name: str, result_status: str, content: str) -> str:
    if tool_name == 'search_knowledge' and result_status == 'empty':
        return content + TOOL_EMPTY_HINT
    if tool_name == 'web_search' and result_status == 'error':
        return content + '\n\n[提示] 搜索失败，请检查网络或更换关键词重试'
    if tool_name == 'fetch_url' and result_status == 'error':
        return content + '\n\n[提示] 抓取失败，请换 web_search 返回的其他 URL 重试'
    return content


def _make_router(
    tool_name: str,
    skill_bodies: dict[str, str] | None,
    citation_getter: Callable[[], Any] | None,
    approval_fn: Callable[[list[Any]], None] | None,
) -> Callable[..., str]:
    """构造路由闭包：StructuredTool 调用 → execute_tool(tool_name, kwargs)。

    make_plan 成功后若挂了 `approval_fn`，调它做用户审批（PLAN_PERMISSION_MODE 路径）；
    审批拒绝时由 `approval_fn` 抛 `PlanAbortedByUser`（与 Python tool_call_engine 同源），
    create_agent 的 ToolNode 会把异常转成 tool 错误消息，LangChainAgent 另在 run() 后
    依据 abort flag 给出确定性取消回答。
    """

    def _fn(**kwargs: Any) -> str:
        # 过滤 LLM 偶发传入的 None 可选参数，让 execute_tool 用自身默认值
        args = {k: v for k, v in kwargs.items() if v is not None}
        citation_builder = citation_getter() if citation_getter is not None else None
        result = execute_tool(
            tool_name,
            args,
            skill_bodies=skill_bodies,
            citation_builder=citation_builder,
        )
        if tool_name == 'make_plan' and result.status == 'ok' and approval_fn is not None:
            approval_fn(args.get('steps') or [])  # 拒绝即抛 PlanAbortedByUser
        return _decorate_hint(tool_name, result.status, result.to_llm_str())

    return _fn


def _wrap_one(
    tool_def: dict[str, Any],
    skill_bodies: dict[str, str] | None,
    citation_getter: Callable[[], Any] | None,
    approval_fn: Callable[[list[Any]], None] | None,
) -> BaseTool:
    fn_spec = tool_def.get('function', {})
    name = fn_spec.get('name', '')
    description = fn_spec.get('description', '') or name
    params = fn_spec.get('parameters', {}) or {'type': 'object'}
    args_model = _schema_to_model(name, params)
    return StructuredTool(
        name=name,
        description=description,
        args_schema=args_model,
        func=_make_router(name, skill_bodies, citation_getter, approval_fn),
    )


def build_langchain_tools(
    skill_bodies: dict | None = None,
    *,
    citation_getter: Callable[[], Any] | None = None,
    approval_fn: Callable[[list[Any]], None] | None = None,
) -> list[BaseTool]:
    """把 `get_tools(skill_bodies)` 暴露的全部工具动态包装为 StructuredTool 列表。

    Args:
        skill_bodies: {skill_name: body}；非空时 get_tools 会追加 load_skill 工具。
        citation_getter: per-run CitationBuilder 取值闭包（仅 search_knowledge 用）。
        approval_fn: make_plan 成功后的审批 hook（拒绝抛 PlanAbortedByUser）；None 不审批。

    Returns:
        与 Python / AutoGPT 同源的工具全集（含 plan/study/quiz/srs + MCP 合流）。
    """
    defs = get_tools(skill_bodies if skill_bodies else None)
    tools: list[BaseTool] = []
    for d in defs:
        try:
            tools.append(_wrap_one(d, skill_bodies, citation_getter, approval_fn))
        except Exception as e:  # 单个 schema 异常不拖垮整体
            nm = d.get('function', {}).get('name', '?')
            logger.warning('[langchain_tools] 包装工具 %s 失败，跳过：%s', nm, e)
    return tools
