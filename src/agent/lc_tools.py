import logging
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool, BaseTool
from src.agent.tools import (
    _tool_search_knowledge,
    _tool_web_search,
    _tool_fetch_url,
    _tool_load_skill,
)

logger = logging.getLogger(__name__)
TOOL_EMPTY_HINT = (
    '\n\n[提示] 知识库未找到相关内容，请先调用 web_search 搜索相关关键词，'
    '再根据返回的真实 URL 调用 fetch_url 获取详情，不允许直接回答。'
)


class SearchKnowledgeInput(BaseModel):
    query: str = Field(description='用于检索的自然语言查询句')
    top_k: int = Field(default=5, description='返回文档片段数')


class WebSearchInput(BaseModel):
    query: str = Field(description='搜索关键词，支持中英文')
    num: int = Field(default=5, description='返回结果条数，最多 10')


class FetchUrlInput(BaseModel):
    url: str = Field(description='要抓取的网页 URL，必须来自 web_search 返回的真实链接')
    max_chars: int = Field(default=3000, description='返回最大字符数')


def _search_fn(query: str, top_k: int = 5) -> str:
    result = _tool_search_knowledge(query, top_k)
    c = result.to_llm_str()
    if result.status == 'empty':
        c += TOOL_EMPTY_HINT
    return c


def _web_search_fn(query: str, num: int = 5) -> str:
    result = _tool_web_search(query, num)
    c = result.to_llm_str()
    if result.status == 'error':
        c += '\n\n[提示] 搜索失败，请检查网络或更换关键词重试'
    return c


def _fetch_fn(url: str, max_chars: int = 3000) -> str:
    result = _tool_fetch_url(url, max_chars)
    c = result.to_llm_str()
    if result.status == 'error':
        c += chr(10) * 2 + '[提示] 抓取失败，请换 web_search 返回的其他 URL 重试'
    return c


def _make_load_fn(skill_bodies: dict):
    def _fn(name: str) -> str:
        return _tool_load_skill(name, skill_bodies).to_llm_str()
    return _fn


search_knowledge_tool: BaseTool = StructuredTool(
    name='search_knowledge',
    description='搜索私有知识库，返回最相关文档片段。当问题涉及私域文档时，应首先调用此工具。',
    args_schema=SearchKnowledgeInput,
    func=_search_fn,
)

web_search_tool: BaseTool = StructuredTool(
    name='web_search',
    description=(
        '通过搜索引擎查找互联网信息，返回真实 URL 及摘要列表。'
        '知识库无结果时，应先调用此工具获取真实 URL，再用 fetch_url 抓取详情。'
        '不得凭空猜测 URL，必须先 web_search 确认。'
    ),
    args_schema=WebSearchInput,
    func=_web_search_fn,
)

fetch_url_tool: BaseTool = StructuredTool(
    name='fetch_url',
    description=(
        '抓取指定网页正文文本，SPA 页面自动通过 Jina Reader 渲染兜底。'
        'URL 必须来自 web_search 返回的真实链接，不得凭空构造。'
    ),
    args_schema=FetchUrlInput,
    func=_fetch_fn,
)


def build_lc_tools(skill_bodies: dict = None) -> list:
    res = [search_knowledge_tool, web_search_tool, fetch_url_tool]
    if skill_bodies:
        res.append(_build_load_skill_tool(skill_bodies))
    return res


def _build_load_skill_tool(skill_bodies: dict) -> BaseTool:
    class _In(BaseModel):
        name: str = Field(description='要加载的 Skill 名称')

    names = ', '.join(skill_bodies.keys())
    desc = '加载 Skill 指导内容。可用名称: [' + names + ']。'
    return StructuredTool(
        name='load_skill',
        description=desc,
        args_schema=_In,
        func=_make_load_fn(skill_bodies),
    )
