"""
Reranker 模块 —— Cross-Encoder 二阶段精排

在 Bi-Encoder 召回的候选集上使用 Cross-Encoder 重新打分并降序排列，
显著提升最终返回结果的相关性精度。

工作流程：
    Bi-Encoder（召回） → top_k × RECALL_MULTIPLIER 候选
    Cross-Encoder（精排） → 逐对打分 → 降序重排 → 截取最终 top_k 条

使用方式：
    from src.rag.reranker import rerank
    hits = rerank(query="RAG 技术", hits=candidates, top_k=5)
"""

import logging
from typing import TYPE_CHECKING

import src.config as config

if TYPE_CHECKING:
    from src.rag.retriever import Hit

logger = logging.getLogger(__name__)

# 模块级懒加载缓存，避免每次调用都重新加载模型
_cross_encoder: "CrossEncoder | None" = None  # type: ignore[name-defined]


def _get_cross_encoder() -> "CrossEncoder":  # type: ignore[name-defined]
    """懒加载 CrossEncoder，首次调用时初始化，后续复用同一实例。"""
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder
        logger.info("[Reranker] 加载 CrossEncoder 模型: %s", config.RERANKER_MODEL)
        _cross_encoder = CrossEncoder(config.RERANKER_MODEL)
        logger.info("[Reranker] CrossEncoder 加载完成")
    return _cross_encoder


def rerank(query: str, hits: "list[Hit]", top_k: int) -> "list[Hit]":
    """
    使用 Cross-Encoder 对候选 hits 重新打分并按相关性降序排列。

    若候选数 ≤ top_k，直接透传原列表（无需精排，避免无意义开销）。
    若 RERANKER_ENABLED=false，调用方不应调用此函数（retriever.py 中已判断）。

    Args:
        query:  用户的自然语言问题。
        hits:   Bi-Encoder 召回的候选 _Hit 列表。
        top_k:  最终期望返回的条数。

    Returns:
        经 Cross-Encoder 重新排序后截取的 top_k 条 _Hit 列表（分数越高越靠前）。
    """
    if len(hits) <= top_k:
        logger.info("[Reranker] 候选数 %d ≤ top_k %d，跳过精排直接透传", len(hits), top_k)
        return hits

    model = _get_cross_encoder()

    # 构造 (query, document) 对，批量送入 CrossEncoder
    pairs: list[tuple[str, str]] = [(query, hit.document) for hit in hits]
    raw = model.predict(pairs)
    # 真实 CrossEncoder 返回 numpy ndarray，mock 可能直接返回 list，统一转换
    scores: list[float] = raw.tolist() if hasattr(raw, "tolist") else list(raw)

    # 按分数降序排列，截取 top_k 条
    ranked = sorted(zip(scores, hits), key=lambda x: x[0], reverse=True)
    result = [hit for _, hit in ranked[:top_k]]

    logger.info(
        "[Reranker] 从 %d 候选精排至 %d 条，最高分: %.4f，最低分: %.4f",
        len(hits),
        len(result),
        ranked[0][0],
        ranked[len(result) - 1][0],
    )
    return result
