"""
工具层 —— Agent 可调用的工具定义与执行

遵循 OpenAI Function Calling 格式，Agent 通过 LLM 的 tool_calls 决定调用哪个工具。

工具列表：
    - search_knowledge : 搜索私有知识库（ChromaDB 向量检索）
    - fetch_url        : 实时抓取网页内容（用于知识库无法覆盖的问题）
"""

import json
import logging
from typing import Any

import requests
from bs4 import BeautifulSoup

from rag.retriever import search

logger = logging.getLogger(__name__)

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
            "name": "fetch_url",
            "description": (
                "抓取指定网页的正文内容并返回纯文本。"
                "当问题需要实时信息、或知识库中没有相关内容时调用此工具。"
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


def _tool_search_knowledge(query: str, top_k: int = 5) -> str:
    """
    调用 RAG 检索层，返回格式化的文档片段字符串。

    Args:
        query: 检索查询语句。
        top_k: 返回的最大片段数。

    Returns:
        格式化的检索结果，含来源和相似度；若知识库为空则返回提示。
    """
    top_k = min(max(1, top_k), 10)  # 限制在 1~10 之间
    logger.debug(f"[tool] search_knowledge: query={query!r}, top_k={top_k}")
    return search(query, top_k=top_k)


def _tool_fetch_url(url: str, max_chars: int = 3000) -> str:
    """
    抓取网页正文内容，使用 BeautifulSoup 提取纯文本。

    Args:
        url: 目标网页 URL。
        max_chars: 返回内容的最大字符数。

    Returns:
        网页正文纯文本（截断至 max_chars），抓取失败时返回错误说明。
    """
    logger.debug(f"[tool] fetch_url: url={url!r}, max_chars={max_chars}")

    if not url.startswith(("http://", "https://")):
        return f"错误：URL 必须以 http:// 或 https:// 开头，收到：{url!r}"

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "aside", "header"]):
            tag.decompose()

        lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
        # 合并连续空行
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

        return text if text else "页面内容为空或无法提取正文。"

    except requests.exceptions.Timeout:
        return f"错误：请求超时（15s），URL: {url}"
    except requests.exceptions.HTTPError as e:
        return f"错误：HTTP {e.response.status_code}，URL: {url}"
    except requests.exceptions.RequestException as e:
        return f"错误：网络请求失败 — {e}"
    except Exception as e:
        return f"错误：解析页面失败 — {e}"


# ── 统一路由入口 ──────────────────────────────────────────────────────────────


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """
    根据工具名称路由执行对应工具函数。

    Args:
        name: 工具名称，对应 TOOLS 列表中的 function.name。
        args: 工具参数字典，由 LLM 的 tool_calls 解析而来。

    Returns:
        工具执行结果的字符串，将作为 tool role 的 content 追加到 messages。

    Raises:
        ValueError: 工具名称不存在时抛出。
    """
    logger.info(f"执行工具: {name}，参数: {json.dumps(args, ensure_ascii=False)}")

    match name:
        case "search_knowledge":
            return _tool_search_knowledge(
                query=args["query"],
                top_k=args.get("top_k", 5),
            )
        case "fetch_url":
            return _tool_fetch_url(
                url=args["url"],
                max_chars=args.get("max_chars", 3000),
            )
        case _:
            raise ValueError(f"未知工具: '{name}'，支持的工具: {[t['function']['name'] for t in TOOLS]}")
