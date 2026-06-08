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
import math
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

import src.config as config

if TYPE_CHECKING:
    from src.rag.retriever import Hit

logger = logging.getLogger(__name__)

# 模块级懒加载缓存：按 model_name 索引，运行时切换 RERANKER_MODEL 时也能复用旧实例（Iter-4）。
# 使用 RLock 确保多线程首查不会重复加载（防止 SentenceTransformer 内部状态被并发污染）。
_cross_encoder_cache: "dict[str, CrossEncoder]" = {}  # type: ignore[name-defined]
_cross_encoder_lock = threading.RLock()


def _get_cross_encoder() -> "CrossEncoder":  # type: ignore[name-defined]
    """懒加载 CrossEncoder，按当前 config.RERANKER_MODEL 取实例，多次调用复用。"""
    name = config.RERANKER_MODEL
    ce = _cross_encoder_cache.get(name)
    if ce is not None:
        return ce
    with _cross_encoder_lock:
        ce = _cross_encoder_cache.get(name)
        if ce is None:
            from sentence_transformers import CrossEncoder
            logger.info("[Reranker] 加载 CrossEncoder 模型: %s", name)
            ce = CrossEncoder(name)
            _cross_encoder_cache[name] = ce
            logger.info("[Reranker] CrossEncoder 加载完成")
    return ce


def warm_up() -> None:
    """主动触发 CrossEncoder 预加载，避免首次检索时延抖动。失败仅警告。"""
    if not config.RERANKER_ENABLED:
        return
    try:
        _get_cross_encoder()
    except Exception as e:
        logger.warning("[Reranker] 预热失败 %s: %s", config.RERANKER_MODEL, e)


def _normalize_score(raw: float) -> float:
    """
    将 cross-encoder 原始输出归一化到 [0, 1] 概率分，方便阈值过滤与展示。

    - BGE-reranker 系列输出已经接近 sigmoid 概率（且本身经过 sigmoid 训练），
      但在不同实现下 predict() 可能返回 logit；统一过一遍 sigmoid 总是安全的：
      raw 已经在 [0, 1] 时再 sigmoid 一次会进一步压向 [0.5, 0.73]，仍保单调，
      仅影响阈值刻度，不影响排序。
    - ms-marco MiniLM 等输出 raw logit（区间约 [-10, 10]），sigmoid 后落入 [0, 1]。
    """
    try:
        if raw >= 0:
            return 1.0 / (1.0 + math.exp(-raw))
        # 数值稳定写法（避免 raw 极小时 math.exp(-raw) 溢出）
        ex = math.exp(raw)
        return ex / (1.0 + ex)
    except (OverflowError, ValueError):
        return 0.0 if raw < 0 else 1.0


def rerank(query: str, hits: "list[Hit]", top_k: int) -> "list[Hit]":
    """
    使用 Cross-Encoder 对候选 hits 重新打分并按相关性降序排列。
    只有候选 ≤ 1 时才透传（一条无需排序）；候选 ≥ 2 一律精排，避免
    "候选不够多就不打分、score 仍是 RRF 小分"被下游精排阈值误删。

    Args:
        query:  用户的自然语言问题。
        hits:   Dense + BM25 召回的候选 Hit 列表。
        top_k:  精排后截取的条数上限（保留 buffer 供下游去重）。

    Returns:
        经 Cross-Encoder 重新排序并截取的 top_k 条 Hit 列表（分数越高越靠前）。
        每条 Hit 的 .score 被覆盖为归一化精排分、.reranked 置 True。
    """
    if len(hits) <= 1:
        logger.info("[Reranker] 候选数 %d ≤ 1，无需精排直接透传", len(hits))
        return hits

    # 优雅降级：模型加载失败（本地无缓存 + TRANSFORMERS_OFFLINE=1 / 网络不可达 / 模型名错误）
    # 不应让整条检索链崩溃。降级为不精排，直接截取召回前 top_k 条。
    try:
        model = _get_cross_encoder()
    except Exception as e:  # noqa: BLE001 — 模型加载层异常种类繁多，统一兜底
        logger.warning(
            "[Reranker] 加载模型 %r 失败，本次降级为不精排（直接返回召回前 %d 条）。"
            "若需启用精排请检查：1) 模型名拼写；2) 本地已缓存或可联网下载；"
            "3) 关闭 TRANSFORMERS_OFFLINE，或切换 RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2。"
            " 原始错误: %s",
            config.RERANKER_MODEL, top_k, e,
        )
        return hits[:top_k]

    # 构造 (query, document) 对，批量送入 CrossEncoder
    pairs: list[tuple[str, str]] = [(query, hit.document) for hit in hits]
    raw = model.predict(pairs)
    # 真实 CrossEncoder 返回 numpy ndarray，mock 可能直接返回 list，统一转换
    raw_scores: list[float] = [float(x) for x in (raw.tolist() if hasattr(raw, "tolist") else raw)]
    norm_scores: list[float] = [_normalize_score(s) for s in raw_scores]

    # 按归一化分数降序排列，截取 top_k 条；同时把分数写回 Hit.score
    indexed = sorted(
        zip(norm_scores, raw_scores, hits),
        key=lambda x: x[0],
        reverse=True,
    )
    result: list["Hit"] = []
    for norm, _raw, hit in indexed[:top_k]:
        # dataclass replace 保持向后兼容：旧消费方读 distance 仍 OK
        # reranked=True 标记本条已带精排分，下游精排阈值才对它生效
        result.append(replace(hit, score=norm, reranked=True))

    logger.info(
        "[Reranker] 从 %d 候选精排至 %d 条，最高分: %.4f，最低分: %.4f（已归一化到 [0,1]）",
        len(hits),
        len(result),
        indexed[0][0],
        indexed[len(result) - 1][0],
    )
    return result
