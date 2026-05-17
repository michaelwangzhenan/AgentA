"""
Query 改写模块 —— RAG 召回前的语义扩展（Iter-3）

提供两类零侵入的 query 扩展手段：
    1. multi_query：让 LLM 为原问题生成 N 条同义/术语化/多角度改写；
       与原 query 一起送入 retriever，所有 ranking 通过 RRF 融合提升召回率。
    2. hyde_query：让 LLM 给出 1~2 句"假设性答案"，把答案也作为 embedding
       检索 query。适合 query 与 doc 词汇分布差异大的场景（口语 → 文档术语）。

设计要点：
    - 失败静默降级：LLM 调用异常时返回空，不打断主检索链路；
    - 进程级 LRU 缓存：同一 query 多次调用零开销，避免重复花 token；
    - 通过 RAG_QUERY_REWRITE_ENABLED / RAG_HYDE_ENABLED 一键开关；
    - 不依赖 chat_history：指代消解由 SYSTEM_PROMPT 引导 Agent 在 query 入参时
      自行解析（避免本模块对会话状态产生耦合）。
"""

from __future__ import annotations

import logging
from functools import lru_cache

import src.config as config

logger = logging.getLogger(__name__)


# ── Prompt 模板 ──────────────────────────────────────────────────────────────

_MULTI_QUERY_PROMPT = (
    "你是一名信息检索专家。请为以下问题生成 {n} 条不同的简短查询，"
    "用于在专业文档库里检索相关内容。\n"
    "要求：\n"
    "1. 优先使用专有名词、缩写、版本号、命令名等高区分度关键词；\n"
    "2. 把口语化表达替换为对应的标准术语（示例：'5G 基站' → 'gNB'，"
    "'4G' → 'LTE'，'随机接入' → 'PRACH random access'）；\n"
    "3. 每条查询用不同的措辞或角度，避免与其他改写重复；\n"
    "4. 每条查询 ≤ 20 字，不带任何解释、不带编号、不带引号；\n"
    "5. 直接输出 {n} 行，每行一条改写，行间不要空行。\n\n"
    "原问题：{query}\n\n"
    "改写："
)

_HYDE_PROMPT = (
    "请用 1~2 句话给出以下问题的假设性答案，仅作为语义检索的参考。\n"
    "不需要保证答案正确，但要使用该领域的专业术语，让答案包含真实文档中可能出现的关键词。\n"
    "直接输出答案文本，不要前缀说明。\n\n"
    "问题：{query}\n\n"
    "假设性答案："
)


# ── LLM 调用封装 ─────────────────────────────────────────────────────────────


def _call_llm(prompt: str) -> str:
    """
    单轮非工具 LLM 调用，使用低温度以提升改写稳定性。

    所有异常被捕获并返回空串：query 改写是"锦上添花"层，绝不能因 LLM 故障导致
    主检索链路失败。
    """
    try:
        from src.llm.provider import chat

        response = chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            temperature=0.2,
        )
        msg = response.choices[0].message
        return (getattr(msg, "content", None) or "").strip()
    except Exception as e:
        logger.warning("[QueryRewriter] LLM 调用失败，已静默降级: %s", e)
        return ""


# ── Multi-Query ──────────────────────────────────────────────────────────────


def _parse_multi_query_lines(text: str, original: str, n: int) -> list[str]:
    """从 LLM 输出文本中抽取每行候选改写，做去噪 / 去重。"""
    candidates: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # 去除常见序号前缀（"1. " / "1）" / "1、" / "- " 等）
        s = s.lstrip("0123456789.、)）-–—* ").strip()
        # 去除首尾包裹的引号（中英）
        for q in ('"', "'", "“", "”", "‘", "’", "「", "」", "『", "』", "《", "》"):
            if s.startswith(q):
                s = s[len(q):]
            if s.endswith(q):
                s = s[: -len(q)]
        s = s.strip()
        if not s or s == original:
            continue
        if s in candidates:
            continue
        candidates.append(s)
        if len(candidates) >= n:
            break
    return candidates


@lru_cache(maxsize=256)
def _cached_multi_query(query: str, n: int) -> tuple[str, ...]:
    """LRU 缓存层：返回 tuple 以满足 lru_cache 不可变要求。"""
    text = _call_llm(_MULTI_QUERY_PROMPT.format(n=n, query=query))
    if not text:
        return ()
    return tuple(_parse_multi_query_lines(text, query, n))


def multi_query(query: str, n: int | None = None) -> list[str]:
    """
    返回原 query 的 N 条同义改写（不含原 query）。

    Args:
        query: 用户原始问题。
        n: 改写条数；None 表示读 config.RAG_REWRITE_MAX_QUERIES。

    Returns:
        改写列表（最多 n 条）；功能未启用 / 输入为空 / LLM 失败时返回空列表。
    """
    if not config.RAG_QUERY_REWRITE_ENABLED:
        return []
    if not query or not query.strip():
        return []
    n_final = n if n is not None else config.RAG_REWRITE_MAX_QUERIES
    n_final = max(1, min(int(n_final), 5))
    return list(_cached_multi_query(query.strip(), n_final))


# ── HyDE（Hypothetical Document Embeddings） ────────────────────────────────


@lru_cache(maxsize=256)
def _cached_hyde(query: str) -> str:
    text = _call_llm(_HYDE_PROMPT.format(query=query))
    return text or ""


def hyde_query(query: str) -> str:
    """
    返回 LLM 生成的"假设性答案"字符串，作为额外检索 query。

    禁用 / 输入为空 / LLM 失败时返回空字符串（调用方应检查并跳过）。
    """
    if not config.RAG_HYDE_ENABLED:
        return ""
    if not query or not query.strip():
        return ""
    return _cached_hyde(query.strip())


# ── 一站式入口 ───────────────────────────────────────────────────────────────


def expand_queries(query: str) -> list[str]:
    """
    生成"原 query + multi-query 改写 + 可选 HyDE 答案"的去重列表。

    返回的列表第 0 个永远是原 query，便于调用方在主链路中保留原意。
    Multi-query 与 HyDE 任一失败都不影响其他来源；全部失败时退化为只有原 query。
    """
    seen: set[str] = set()
    expanded: list[str] = []

    def _add(q: str) -> None:
        s = (q or "").strip()
        if not s or s in seen:
            return
        seen.add(s)
        expanded.append(s)

    _add(query)

    for q in multi_query(query):
        _add(q)

    hyde = hyde_query(query)
    if hyde:
        _add(hyde)

    return expanded


def clear_cache() -> None:
    """便于测试 / 调优时清空 LRU 缓存。"""
    _cached_multi_query.cache_clear()
    _cached_hyde.cache_clear()
