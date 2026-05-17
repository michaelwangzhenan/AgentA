"""
向量检索模块（dense + BM25 混合）

对用户问题进行多路召回：
    1. Dense：在所有已入库的 ChromaDB collection（en/zh 等）并行做向量检索；
    2. Sparse：可选地在每个 collection 对应的 BM25 索引中做关键词检索；
    3. 按 collection 维度用 Reciprocal Rank Fusion（RRF）融合 dense+bm25 排名；
    4. 跨 collection 用 round-robin 合并；
    5. dense 阈值过滤（仅对未被 BM25 加持的纯 dense hit 生效）；
    6. Cross-Encoder 精排（可选）+ 精排阈值过滤；
    7. 按 source 文件去重（同一文件最多保留 K_PER_SOURCE 条），避免单文档霸屏。

设计要点：
  - dense 与 bm25 分数尺度不可比，使用 RRF（rank-based）天然解决；
  - Hit.score 始终保持"越大越好"语义，便于下游展示与阈值过滤；
  - search(where=...) 透传到 chroma 与 BM25，支持按 lang/ext 等 metadata 精筛。
"""

# 必须在 huggingface/transformers 相关库 import 之前设置环境变量
import os
from dotenv import load_dotenv
load_dotenv(override=True)
for _key in ("HF_ENDPOINT", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    _val = os.getenv(_key)
    if _val:
        os.environ[_key] = _val

import logging
from dataclasses import dataclass, field
from typing import Any

import chromadb
from chromadb.errors import NotFoundError
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

import src.config as config

logger = logging.getLogger(__name__)


# ── BGE 等模型的非对称检索 query 前缀 ─────────────────────────────────────────
_BGE_ZH_QUERY_PREFIX: str = "为这个句子生成表示以用于检索相关文章："
_BGE_EN_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "


def _query_prefix_for(model_name: str) -> str:
    """根据 embedding 模型名返回非对称检索的 query 前缀；不需要时返回空字符串。"""
    name = model_name.lower()
    if "bge-m3" in name or "bge-large-en-v1.5" in name or "bge-base-en-v1.5" in name \
            or "bge-small-en-v1.5" in name or "bge-large-zh-v1.5" in name \
            or "bge-base-zh-v1.5" in name or "bge-small-zh-v1.5" in name:
        return ""
    if "bge" in name and "zh" in name:
        return _BGE_ZH_QUERY_PREFIX
    if "bge" in name:
        return _BGE_EN_QUERY_PREFIX
    return ""


# ── Embedding function 进程级缓存 ────────────────────────────────────────────
_embedding_fn_cache: dict[str, SentenceTransformerEmbeddingFunction] = {}


def _get_embedding_fn(model_name: str) -> SentenceTransformerEmbeddingFunction:
    fn = _embedding_fn_cache.get(model_name)
    if fn is None:
        fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
        _embedding_fn_cache[model_name] = fn
    return fn


@dataclass
class Hit:
    """
    单条检索命中结果，跨 collection 排序与下游展示统一使用。

    Attributes:
        source:     文档相对路径（含子目录），由 ingest 写入 metadata。
        document:   chunk 正文（已含 heading 面包屑前缀，由 splitter 注入）。
        distance:   原始向量距离，cosine 空间下 ∈ [0, 2]；BM25-only 命中时为 0.0。
        collection: 来源 collection 名。
        score:      归一化相关性分（越大越好），不同阶段含义不同：
                      · 召回阶段填 RRF 融合分（数值很小，仅作排序用）；
                      · 精排后由 reranker 覆盖为 cross-encoder sigmoid 概率（[0, 1]）。
        id:         chunk 唯一 ID（与 chroma 的 ids 对齐），用于跨检索器融合去重。
        retrievers: 该 Hit 被哪些检索器召回，如 ["dense", "bm25"]，便于排查与阈值策略。
        metadata:   完整 metadata 字典（含 doc_id / lang / page_no / heading_path 等）。
    """
    source: str
    document: str
    distance: float
    collection: str
    score: float | None = None
    id: str = ""
    retrievers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] | None = None


# ── Dense 召回 ───────────────────────────────────────────────────────────────


def _query_collection(
    client: Any,
    model_name: str,
    collection_name: str,
    query: str,
    top_k: int,
    where: dict | None = None,
) -> list[Hit]:
    """
    查询单个 collection（dense / 向量），返回命中列表。

    若 collection 不存在或为空，静默返回空列表。
    where 透传给 chroma 的 where 子句，支持 metadata 等值/$in/$ne 等过滤。
    """
    embedding_fn = _get_embedding_fn(model_name)
    try:
        col = client.get_collection(
            name=collection_name,
            embedding_function=embedding_fn,  # type: ignore[arg-type]
        )
    except NotFoundError:
        logger.warning(
            "collection '%s' 不存在，跳过。请先运行: python -m rag.ingest -m %s",
            collection_name,
            collection_name.removeprefix("kb_"),
        )
        return []

    count = col.count()
    if count == 0:
        return []

    prefix = _query_prefix_for(model_name)
    effective_query = (prefix + query) if prefix else query

    query_kwargs: dict[str, Any] = {
        "query_texts": [effective_query],
        "n_results": min(top_k, count),
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    results = col.query(**query_kwargs)

    hits: list[Hit] = []
    docs = results["documents"][0] if results["documents"] else []       # type: ignore[index]
    metas = results["metadatas"][0] if results["metadatas"] else []      # type: ignore[index]
    dists = results["distances"][0] if results["distances"] else []      # type: ignore[index]
    ids_list = (results.get("ids") or [[]])[0]
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
        meta = meta or {}
        dense_score = 1.0 - float(dist)
        source = str(meta.get("source") or meta.get("filename") or "unknown")
        chunk_id = ids_list[i] if i < len(ids_list) else ""
        hits.append(Hit(
            source=source,
            document=doc,
            distance=float(dist),
            collection=collection_name,
            score=dense_score,
            id=str(chunk_id) if chunk_id else "",
            retrievers=["dense"],
            metadata=dict(meta),
        ))
    return hits


# ── BM25 召回 ────────────────────────────────────────────────────────────────


def _query_bm25(
    collection_name: str,
    query: str,
    top_k: int,
    where: dict | None = None,
) -> list[Hit]:
    """对指定 collection 的 BM25 索引做关键词召回；索引缺失时静默返回空列表。"""
    if not config.BM25_ENABLED:
        return []
    try:
        from src.rag.bm25_index import get_index, get_index_path

        path = get_index_path(collection_name)
        if not path.exists():
            return []
        idx = get_index(collection_name)
    except Exception as e:
        logger.warning("[BM25] 加载 %s 失败: %s", collection_name, e)
        return []

    raw = idx.search(query, top_k=top_k, where=where)
    if not raw:
        return []

    hits: list[Hit] = []
    for doc, score in raw:
        meta = doc.metadata or {}
        source = str(meta.get("source") or meta.get("filename") or "unknown")
        hits.append(Hit(
            source=source,
            document=doc.document,
            distance=0.0,           # BM25 无向量距离概念，留 0 保持类型一致
            collection=collection_name,
            score=float(score),     # 临时存 BM25 raw score，融合后会被 RRF 分覆盖
            id=doc.id,
            retrievers=["bm25"],
            metadata=dict(meta),
        ))
    return hits


# ── RRF 融合 ─────────────────────────────────────────────────────────────────


def _rrf_fuse(rankings: list[list[Hit]], k: int) -> list[Hit]:
    """
    Reciprocal Rank Fusion：对多个排序结果做 rank-based 融合。

    score(d) = Σ 1 / (k + rank_i(d))，其中 rank_i 是 d 在第 i 个排序中的 1-based 名次。
    同一 chunk 在不同检索器中出现时，retrievers 字段做并集；其余字段以 dense 为优先源
    （dense hit 的 metadata 与 distance 信息更完整）。
    """
    fused: dict[str, Hit] = {}
    fused_score: dict[str, float] = {}

    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            key = hit.id or f"{hit.collection}::{hit.source}::{hash(hit.document)}"
            inc = 1.0 / (k + rank)
            fused_score[key] = fused_score.get(key, 0.0) + inc

            if key not in fused:
                fused[key] = Hit(
                    source=hit.source,
                    document=hit.document,
                    distance=hit.distance,
                    collection=hit.collection,
                    score=None,                    # 末尾统一填 RRF 分
                    id=hit.id,
                    retrievers=list(hit.retrievers),
                    metadata=dict(hit.metadata) if hit.metadata else None,
                )
            else:
                # 同一 chunk 被多检索器召回：合并 retrievers，其它字段保留先到者
                exist = fused[key]
                for r in hit.retrievers:
                    if r not in exist.retrievers:
                        exist.retrievers.append(r)
                # dense 命中带 distance，把它补进去
                if "dense" in hit.retrievers and exist.distance == 0.0 and hit.distance > 0:
                    exist.distance = hit.distance

    out: list[Hit] = []
    for key, hit in fused.items():
        hit.score = fused_score[key]
        out.append(hit)
    out.sort(key=lambda h: (h.score or 0.0), reverse=True)
    return out


# ── 公共入口 ─────────────────────────────────────────────────────────────────


def _dedupe_by_source(hits: list[Hit], k_per_source: int) -> list[Hit]:
    """
    保留 hits 已有顺序，按 source 限流：每个 source 最多保留 k_per_source 条。
    k_per_source <= 0 表示不去重，原样返回。
    """
    if k_per_source <= 0:
        return hits
    counter: dict[str, int] = {}
    out: list[Hit] = []
    for h in hits:
        n = counter.get(h.source, 0)
        if n >= k_per_source:
            continue
        counter[h.source] = n + 1
        out.append(h)
    return out


def search(
    query: str,
    top_k: int = config.RAG_TOP_K,
    where: dict | None = None,
) -> list[Hit]:
    """
    在所有已入库的 collection 中检索最相关的 Top-K 文档片段。

    Args:
        query:  用户的自然语言问题。
        top_k:  最终返回上限，默认读 config.RAG_TOP_K。
        where:  可选 metadata 过滤子句（透传给 chroma 与 BM25），如
                {"lang": "zh"} 或 {"ext": {"$in": [".pdf", ".md"]}}。

    Returns:
        Hit 列表，按融合后/精排后 score 降序，长度 ≤ top_k；空列表表示无命中。
    """
    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)

    # 召回窗口：开启精排时扩大候选；BM25 与 dense 各取 recall_k 条
    recall_k = (
        top_k * config.RERANKER_RECALL_MULTIPLIER
        if config.RERANKER_ENABLED
        else top_k
    )

    # 每个 collection 内部做 dense + BM25 → RRF 融合，再跨 collection round-robin
    per_collection_fused: list[list[Hit]] = []
    for alias, (model_name, collection_name) in config.EMBEDDING_MODELS.items():
        dense_hits = _query_collection(
            client, model_name, collection_name, query, recall_k, where=where,
        )
        if dense_hits:
            dense_hits.sort(key=lambda h: h.distance)

        bm25_hits = _query_bm25(collection_name, query, recall_k, where=where)
        # bm25_hits 已按 BM25 score 降序

        if not dense_hits and not bm25_hits:
            continue

        if dense_hits and bm25_hits:
            fused = _rrf_fuse([dense_hits, bm25_hits], k=config.RRF_K)
            logger.info(
                "  [%s] %s: dense=%d, bm25=%d → fused=%d",
                alias, collection_name, len(dense_hits), len(bm25_hits), len(fused),
            )
        elif dense_hits:
            fused = dense_hits
            logger.info("  [%s] %s: dense=%d (无 BM25 索引)", alias, collection_name, len(dense_hits))
        else:
            fused = bm25_hits
            logger.info("  [%s] %s: bm25=%d (dense 无命中)", alias, collection_name, len(bm25_hits))

        per_collection_fused.append(fused)

    if not per_collection_fused:
        return []

    # Round-robin 跨 collection 合并（避免某模型距离空间挤压另一模型）
    candidates: list[Hit] = []
    iterators = [iter(bucket) for bucket in per_collection_fused]
    while len(candidates) < recall_k and iterators:
        exhausted: list[int] = []
        for idx, it in enumerate(iterators):
            if len(candidates) >= recall_k:
                break
            try:
                candidates.append(next(it))
            except StopIteration:
                exhausted.append(idx)
        for idx in reversed(exhausted):
            iterators.pop(idx)
        if not iterators:
            break

    # Dense 阈值过滤：仅对"纯 dense 命中"应用，BM25 加持的 chunk 不被 dense 阈值剔除。
    # score 缺失（如外部 mock）时回退到 1 - distance，保持向后兼容。
    min_dense = config.RAG_DENSE_MIN_SCORE
    if min_dense > 0:
        before = len(candidates)

        def _keep(h: Hit) -> bool:
            if "bm25" in h.retrievers:
                return True
            dense_like_score = (1.0 - h.distance) if h.distance else (h.score or 0.0)
            return dense_like_score >= min_dense

        candidates = [h for h in candidates if _keep(h)]
        if before != len(candidates):
            logger.info(
                "Dense 阈值过滤：%d → %d（min_score=%.3f，BM25 命中豁免）",
                before, len(candidates), min_dense,
            )

    if not candidates:
        return []

    # 精排（可选）
    if config.RERANKER_ENABLED:
        from src.rag.reranker import rerank
        top_hits = rerank(query=query, hits=candidates, top_k=max(top_k * 2, top_k))
        logger.info("Reranker 精排后保留 %d 条", len(top_hits))

        min_rerank = config.RAG_RERANK_MIN_SCORE
        if min_rerank > 0 and len(candidates) > top_k:
            before = len(top_hits)
            top_hits = [h for h in top_hits if (h.score or 0.0) >= min_rerank]
            if before != len(top_hits):
                logger.info(
                    "Reranker 阈值过滤：%d → %d（min_score=%.3f）",
                    before, len(top_hits), min_rerank,
                )
    else:
        top_hits = candidates

    # 按 source 文件去重，避免一个长文档把 top_k 全部占满
    deduped = _dedupe_by_source(top_hits, config.RAG_K_PER_SOURCE)
    if len(deduped) != len(top_hits):
        logger.info(
            "Per-source 去重：%d → %d（k_per_source=%d）",
            len(top_hits), len(deduped), config.RAG_K_PER_SOURCE,
        )

    return deduped[:top_k]


def format_search_results(hits: list[Hit]) -> str:
    """
    将检索命中列表格式化为 LLM 可消费的字符串。

    新增展示信息：retrievers（哪些检索器召回）、heading_path 与 page_no（如有），
    便于 LLM 自我评估命中质量并在回答中给出精准引用。

    Args:
        hits: search() 返回的命中列表，为空时返回提示文本。

    Returns:
        格式化文本，每条独立一段，段间用 "---" 分隔。
    """
    if not hits:
        return (
            "知识库为空或尚未初始化。\n"
            "请运行以下命令完成文档入库：\n"
            "  python -m rag.ingest -m en   # 英文文档\n"
            "  python -m rag.ingest -m zh   # 中文文档"
        )

    parts: list[str] = []
    for i, hit in enumerate(hits, start=1):
        if hit.score is not None:
            score_str = f"{hit.score:.4f}"
        else:
            score_str = f"{round(1 - hit.distance, 4)}"

        meta = hit.metadata or {}
        loc_bits: list[str] = []
        if hit.retrievers:
            loc_bits.append(f"召回={'+'.join(hit.retrievers)}")
        if meta.get("heading_path"):
            loc_bits.append(f"章节={meta['heading_path']}")
        if meta.get("page_no"):
            loc_bits.append(f"页={meta['page_no']}")
        loc_str = ("，" + "，".join(loc_bits)) if loc_bits else ""

        parts.append(
            f"[{i}] 来源: {hit.source}（相关性: {score_str}，库: {hit.collection}{loc_str}）\n"
            f"{hit.document}"
        )
    return "\n\n---\n\n".join(parts)
