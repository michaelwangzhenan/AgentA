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
from typing import Any, Literal

import requests
from bs4 import BeautifulSoup

import src.config as _cfg
from src.rag.retriever import search, format_search_results

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

    若传入 skill_bodies，追加 load_skill 工具定义（name 字段限定为已有名称枚举）。
    """
    tools = list(TOOLS)
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
                "搜索私有知识库，返回与问题最相关的文档片段。"
                "当问题可能在已导入的本地文档中有答案时，优先调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用于检索的自然语言查询语句，尽量与用户问题语义相近。",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回的最大文档片段数，默认为 5，最大不超过 10。",
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


def _tool_search_knowledge(query: str, top_k: int = 5) -> ToolResult:
    """
    调用 RAG 检索层，返回格式化的文档片段字符串。

    Args:
        query: 检索查询语句。
        top_k: 返回的最大片段数。

    Returns:
        ToolResult：有命中结果 → status="ok"；知识库为空/无命中 → status="empty"。
    """
    top_k = min(max(1, top_k), MAX_SEARCH_TOP_K)  # 限制在 1~MAX_SEARCH_TOP_K 之间
    logger.info("[tool] search_knowledge: query=%r, top_k=%d", query, top_k)
    hits = search(query, top_k=top_k)
    if hits:
        return ToolResult(status="ok", content=format_search_results(hits))
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
) -> ToolResult:
    """
    根据工具名称路由执行对应工具函数。

    Args:
        name: 工具名称，对应 TOOLS 列表中的 function.name。
        args: 工具参数字典，由 LLM 的 tool_calls 解析而来。
        skill_bodies: {skill_name: body} 映射，供 load_skill 工具使用。

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
                    top_k=args.get("top_k", 5),
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
            case _:
                return ToolResult(
                    status="error",
                    content=f"未知工具: '{name}'，支持的工具: {[t['function']['name'] for t in get_tools()]}",
                )
    except Exception as e:
        logger.warning("[tool] execute_tool 异常兜底: %s", e)
        return ToolResult(status="error", content=f"工具执行异常: {e}")
