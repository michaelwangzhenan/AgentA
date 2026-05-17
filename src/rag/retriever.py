"""
向量检索模块

对用户问题进行向量化，在 ChromaDB 中执行相似度检索，返回 Top-K 相关文档片段。

支持多 embedding 模型：对所有已有 collection（en/zh 等）并行检索，
结果合并后按相似度全局排序，返回最相关的 Top-K 条。
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
from dataclasses import dataclass
from typing import Any

import chromadb
from chromadb.errors import NotFoundError
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

import src.config as config

logger = logging.getLogger(__name__)


# ── BGE 等模型的非对称检索 query 前缀 ─────────────────────────────────────────
# BGE v1 系列（如 bge-small-zh / bge-base-zh）训练时使用了 query/passage 不同前缀，
# 推理时只给 query 加前缀、passage 不加，召回率显著优于不加前缀。
# 参考：https://huggingface.co/BAAI/bge-small-zh
#
# bge-v1.5 系列、bge-m3、e5-multilingual 不再需要前缀；其他通用模型（MiniLM/mpnet）
# 也不需要前缀。本模块按模型名做精确白名单匹配，避免误伤。
_BGE_ZH_QUERY_PREFIX: str = "为这个句子生成表示以用于检索相关文章："
_BGE_EN_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "


def _query_prefix_for(model_name: str) -> str:
    """根据 embedding 模型名返回非对称检索的 query 前缀；不需要时返回空字符串。"""
    name = model_name.lower()
    # bge-v1.5 / bge-m3 不需要前缀；显式排除避免被下面的兜底误伤
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
# 避免每次 _query_collection 都新建一份 SentenceTransformer，多 collection 时首查抖动明显。
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
    单条检索命中结果，用于跨 collection 排序与下游展示。

    Attributes:
        source:     文档相对路径（含子目录），由 ingest 写入 metadata。
        document:   chunk 正文。
        distance:   原始向量距离，cosine 空间下 ∈ [0, 2]。
        collection: 来源 collection 名。
        score:      归一化相关性分（越大越好）：
                      - 召回阶段填 dense_score = 1 - distance（cosine 相似度，[-1, 1]）；
                      - 精排后由 reranker 覆盖为 cross-encoder 输出（不同模型尺度不同）。
                    向后兼容：未设置时为 None，旧消费方按 distance 排序仍可工作。
        metadata:   入库时写入的完整 metadata 字典（lang / page_no / heading_path 等），
                    None 表示老数据没有这些字段。
    """
    source: str
    document: str
    distance: float
    collection: str
    score: float | None = None
    metadata: dict[str, Any] | None = None


def _query_collection(
    client: Any,
    model_name: str,
    collection_name: str,
    query: str,
    top_k: int,
) -> list[Hit]:
    """
    查询单个 collection，返回命中列表。

    若 collection 不存在或为空，静默返回空列表。
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
            collection_name.removeprefix("kb_"),  # en / zh
        )
        return []

    count = col.count()
    if count == 0:
        return []

    # BGE v1 等非对称模型：query 前加专用前缀，document 不变
    prefix = _query_prefix_for(model_name)
    effective_query = (prefix + query) if prefix else query

    results = col.query(
        query_texts=[effective_query],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )

    hits: list[Hit] = []
    docs = results["documents"][0] if results["documents"] else []       # type: ignore[index]
    metas = results["metadatas"][0] if results["metadatas"] else []      # type: ignore[index]
    dists = results["distances"][0] if results["distances"] else []      # type: ignore[index]
    for doc, meta, dist in zip(docs, metas, dists):
        meta = meta or {}
        # cosine 空间下，相似度 = 1 - distance；其他空间回退为同样公式（仅作排序用）
        dense_score = 1.0 - float(dist)
        # 优先用 metadata 里完整 source（rel_path），兜底 filename
        source = str(meta.get("source") or meta.get("filename") or "unknown")
        hits.append(Hit(
            source=source,
            document=doc,
            distance=float(dist),
            collection=collection_name,
            score=dense_score,
            metadata=dict(meta),
        ))
    return hits


def search(query: str, top_k: int = config.RAG_TOP_K) -> list[Hit]:
    """
    在所有已入库的 collection 中检索最相关的 Top-K 文档片段。

    两阶段流程：
      阶段一（召回）：遍历所有 (model, collection)，每个 collection 内部按自身
        距离升序各取 top_k × RERANKER_RECALL_MULTIPLIER 条；因不同 embedding
        模型距离空间不可比，采用 round-robin 交错合并得到候选集。
        对每条 hit 计算 dense_score = 1 - distance（cosine 相似度）作为初始分。
      阶段二（精排）：RERANKER_ENABLED=true 时，用 Cross-Encoder 对候选集重新
        打分并降序排列，截取 top_k 条；否则直接截取 round-robin 前 top_k 条。
      阶段三（阈值过滤）：dense 阶段按 RAG_DENSE_MIN_SCORE 过滤；
        若开启精排，再按 RAG_RERANK_MIN_SCORE 过滤一次。

    Args:
        query: 用户的自然语言问题。
        top_k: 全局返回的最大片段数，默认读取 config.RAG_TOP_K。

    Returns:
        Hit 列表，已按 score 降序、且过滤掉低于阈值的低质量片段。
        所有 collection 均为空或全被阈值过滤掉时返回空列表。
    """
    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)

    # 召回窗口：开启精排时扩大取回范围，关闭时与 top_k 相同
    recall_k = (
        top_k * config.RERANKER_RECALL_MULTIPLIER
        if config.RERANKER_ENABLED
        else top_k
    )

    # 每个 collection 各自检索 recall_k 条，结果按内部距离升序
    per_collection: list[list[Hit]] = []
    for alias, (model_name, collection_name) in config.EMBEDDING_MODELS.items():
        hits = _query_collection(client, model_name, collection_name, query, recall_k)
        if hits:
            hits.sort(key=lambda h: h.distance)
            per_collection.append(hits)
            logger.info("  [%s] %s: %d 条命中", alias, collection_name, len(hits))

    if not per_collection:
        return []

    # Round-robin 交错合并：依次取每个 collection 的第 1、2、... 名
    # 避免跨模型距离不可比导致某个库被整体压制
    candidates: list[Hit] = []
    iterators = [iter(bucket) for bucket in per_collection]
    while len(candidates) < recall_k and iterators:
        exhausted: list[int] = []
        for idx, it in enumerate(iterators):
            if len(candidates) >= recall_k:
                break
            try:
                candidates.append(next(it))
            except StopIteration:
                exhausted.append(idx)
        # 移除已耗尽的迭代器（倒序避免索引错位）
        for idx in reversed(exhausted):
            iterators.pop(idx)
        if not iterators:
            break

    # 阶段一阈值：按 dense score 过滤明显不相关的候选，避免送入 reranker / LLM 浪费。
    # score 缺失（如外部 mock）时回退到 1 - distance，保持向后兼容。
    min_dense = config.RAG_DENSE_MIN_SCORE
    if min_dense > 0:
        before = len(candidates)
        candidates = [
            h for h in candidates
            if (h.score if h.score is not None else 1.0 - h.distance) >= min_dense
        ]
        if before != len(candidates):
            logger.info(
                "Dense 阈值过滤：%d → %d（min_score=%.3f）",
                before, len(candidates), min_dense,
            )

    if not candidates:
        return []

    # 阶段二：精排（如已开启）
    if config.RERANKER_ENABLED:
        from src.rag.reranker import rerank
        top_hits = rerank(query=query, hits=candidates, top_k=top_k)
        logger.info("Reranker 精排后保留 %d 条", len(top_hits))

        # 阶段三：reranker score 阈值过滤（仅当 reranker 真正打分时生效；
        # 若 candidates ≤ top_k，rerank() 直接 passthrough，score 仍是 dense_score，
        # 此时跳过此过滤避免误伤）
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
        top_hits = candidates[:top_k]

    return top_hits


def format_search_results(hits: list[Hit]) -> str:
    """
    将检索命中列表格式化为 LLM 可消费的字符串。

    Args:
        hits: search() 返回的命中列表，为空时返回提示文本。

    Returns:
        格式化文本，每条包含序号、来源文件名、相似度和文档片段。
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
        # 优先展示 reranker / dense 归一化分（更直观），fallback 到 1 - distance
        if hit.score is not None:
            score_str = f"{hit.score:.4f}"
        else:
            score_str = f"{round(1 - hit.distance, 4)}"
        parts.append(
            f"[{i}] 来源: {hit.source}（相关性: {score_str}，库: {hit.collection}）\n"
            f"{hit.document}"
        )
    return "\n\n---\n\n".join(parts)
