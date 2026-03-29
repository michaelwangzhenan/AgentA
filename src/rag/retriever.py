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


@dataclass
class Hit:
    """单条检索命中结果，用于跨 collection 排序。"""
    source: str
    document: str
    distance: float
    collection: str


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
    embedding_fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
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

    results = col.query(
        query_texts=[query],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )

    hits: list[Hit] = []
    docs = results["documents"][0] if results["documents"] else []       # type: ignore[index]
    metas = results["metadatas"][0] if results["metadatas"] else []      # type: ignore[index]
    dists = results["distances"][0] if results["distances"] else []      # type: ignore[index]
    for doc, meta, dist in zip(docs, metas, dists):
        hits.append(Hit(
            source=str(meta.get("source", "unknown")),
            document=doc,
            distance=float(dist),
            collection=collection_name,
        ))
    return hits


def search(query: str, top_k: int = config.RAG_TOP_K) -> list[Hit]:
    """
    在所有已入库的 collection 中检索最相关的 Top-K 文档片段。

    两阶段流程：
      阶段一（召回）：遍历所有 (model, collection)，每个 collection 内部按自身
        距离升序各取 top_k × RERANKER_RECALL_MULTIPLIER 条；因不同 embedding
        模型距离空间不可比，采用 round-robin 交错合并得到候选集。
      阶段二（精排）：RERANKER_ENABLED=true 时，用 Cross-Encoder 对候选集重新
        打分并降序排列，截取 top_k 条；否则直接截取 round-robin 前 top_k 条。

    Args:
        query: 用户的自然语言问题。
        top_k: 全局返回的最大片段数，默认读取 config.RAG_TOP_K。

    Returns:
        格式化后的检索结果字符串，含来源文件名、所属 collection 和片段内容。
        若所有 collection 均为空则返回提示字符串。
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

    # 阶段二：精排（如已开启）
    if config.RERANKER_ENABLED:
        from src.rag.reranker import rerank
        top_hits = rerank(query=query, hits=candidates, top_k=top_k)
        logger.info("Reranker 精排后保留 %d 条", len(top_hits))
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
        similarity = round(1 - hit.distance, 4)
        parts.append(
            f"[{i}] 来源: {hit.source}（相似度: {similarity}，库: {hit.collection}）\n"
            f"{hit.document}"
        )
    return "\n\n---\n\n".join(parts)
