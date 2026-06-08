"""
Query 改写模块 —— RAG 召回前的语义扩展

对外主入口为 expand_queries(query)，返回去重后的 query 列表（第 0 项恒为原 query），
供 retriever 多路召回；RRF 融合在 retriever 内完成，不在本模块。

三轴扩展（各轴独立开关，未开启则不追加）：
    1. multi_query（RAG_QUERY_REWRITE_ENABLED）：LLM 生成 N 条同义/术语化改写；
    2. hyde_query（RAG_HYDE_ENABLED）：LLM 生成 1~2 句假设性答案，作额外检索 query；
    3. translate_query（RAG_TRANSLATE_QUERY_ENABLED）：按 query 语种追加中/英翻译版。

设计要点：
    - 失败静默降级：单轴 LLM 失败只影响该轴，expand_queries 至少保留原 query；
    - 进程级 LRU 缓存：multi / hyde / translate 各自缓存，同 query 重复调用零 token；
    - 不依赖 chat_history：指代消解由 Agent 在调用 search 前自行补全 query。
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

_TRANSLATE_PROMPT = (
    "把下面这条信息检索 query 准确翻译为 {target_lang_label}。\n"
    "要求：\n"
    "1. 严格保留专有名词、缩写、版本号、命令名（如 gNB、3GPP TS 38.211、PRACH）；\n"
    "2. 不要加任何前缀、后缀、引号、括注、解释；\n"
    "3. 只输出一行翻译结果。\n\n"
    "原 query：{query}\n\n"
    "翻译："
)


# ── 按需触发判定 ─────────────────────────────────────────────────────────────


def _is_short_query(query: str) -> bool:
    """query 太短（多为精确术语/缩写）时跳过 multi-query/HyDE，省 LLM 调用与延迟。

    阈值由 config.RAG_REWRITE_MIN_QUERY_LEN 控制，设 0 表示从不跳过。
    """
    threshold = config.RAG_REWRITE_MIN_QUERY_LEN
    if threshold <= 0:
        return False
    return len(query.strip()) < threshold


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
    if _is_short_query(query):
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
    if _is_short_query(query):
        return ""
    return _cached_hyde(query.strip())


# ── 跨语言翻译轴（Iter-5） ──────────────────────────────────────────────────


def detect_query_lang(text: str) -> str:
    """
    简单启发：CJK 字符占比 > 30% 视为 'zh'，否则 'en'。
    返回值用于决定翻译目标方向（zh→en 或 en→zh）。
    """
    if not text:
        return "en"
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return "zh" if cjk / max(len(text), 1) > 0.3 else "en"


_LANG_LABELS: dict[str, str] = {"en": "English（英文）", "zh": "中文"}


@lru_cache(maxsize=256)
def _cached_translate(query: str, target_lang: str) -> str:
    label = _LANG_LABELS.get(target_lang, target_lang)
    text = _call_llm(_TRANSLATE_PROMPT.format(target_lang_label=label, query=query))
    if not text:
        return ""
    # 翻译应为单行；LLM 偶尔会多打一行解释，取首行即可
    first = text.splitlines()[0].strip()
    return first


def translate_query(query: str, target_lang: str) -> str:
    """
    把 query 翻译成 target_lang（'zh' 或 'en'）。

    禁用 / 输入为空 / 目标语种非 zh|en / LLM 失败时返回空字符串。
    """
    if not config.RAG_TRANSLATE_QUERY_ENABLED:
        return ""
    if target_lang not in ("zh", "en"):
        return ""
    if not query or not query.strip():
        return ""
    return _cached_translate(query.strip(), target_lang)


# ── 主入口 ──────────────────────────────────────────────────────────────


def expand_queries(query: str) -> list[str]:
    """
    生成「原 query + multi-query 改写 + HyDE 假设性答案 + 中/英翻译」的去重列表。

    第 0 项恒为原 query（经 strip）；各轴失败互不影响，全部失败时仅含原 query。
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

    if config.RAG_TRANSLATE_QUERY_ENABLED:
        src_lang = detect_query_lang(query)
        target_lang = "en" if src_lang == "zh" else "zh"
        translated = translate_query(query, target_lang)
        if translated:
            _add(translated)
            logger.info(
                "[QueryRewriter] 已追加翻译版 [%s→%s]: %s",
                src_lang, target_lang, translated[:60],
            )

    return expanded


def clear_cache() -> None:
    """便于测试 / 调优时清空 LRU 缓存。"""
    _cached_multi_query.cache_clear()
    _cached_hyde.cache_clear()
    _cached_translate.cache_clear()
