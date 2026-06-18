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
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.errors import NotFoundError
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

import src.config as config

logger = logging.getLogger(__name__)


# ── BGE 等模型的非对称检索 query 前缀 ─────────────────────────────────────────
_BGE_ZH_QUERY_PREFIX: str = "为这个句子生成表示以用于检索相关文章："
_BGE_EN_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "


# bge-m3 与 bge-*-v1.5 系列检索时不需要 query 前缀（命中任一子串即跳过）
_BGE_NO_PREFIX_MARKERS: tuple[str, ...] = ("bge-m3", "v1.5")


def _query_prefix_for(model_name: str) -> str:
    """根据 embedding 模型名返回非对称检索的 query 前缀；不需要时返回空字符串。"""
    name = model_name.lower()
    if any(marker in name for marker in _BGE_NO_PREFIX_MARKERS):
        return ""
    if "bge" in name and "zh" in name:
        return _BGE_ZH_QUERY_PREFIX
    if "bge" in name:
        return _BGE_EN_QUERY_PREFIX
    return ""


# ── Embedding function 进程级缓存 ────────────────────────────────────────────
_embedding_fn_cache: dict[str, SentenceTransformerEmbeddingFunction] = {}
_embedding_fn_lock = threading.RLock()


def _get_embedding_fn(model_name: str) -> SentenceTransformerEmbeddingFunction:
    """懒加载 embedding function，多次调用复用同一实例；线程安全。"""
    fn = _embedding_fn_cache.get(model_name)
    if fn is not None:
        return fn
    with _embedding_fn_lock:
        fn = _embedding_fn_cache.get(model_name)
        if fn is None:
            fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
            _embedding_fn_cache[model_name] = fn
    return fn


@lru_cache(maxsize=512)
def _embed_query_cached(model_name: str, text: str) -> tuple[float, ...]:
    """编码单条 query 为向量并按 (model, text) 缓存。

    同一 query 重复检索（含多路改写命中相同文本）时零编码开销 —— query 编码是
    CPU 密集的耗时项。返回 tuple 以满足 lru_cache 不可变要求。
    """
    fn = _get_embedding_fn(model_name)
    vec = fn([text])[0]
    return tuple(float(x) for x in vec)


# ── ChromaDB 客户端进程级缓存 ─────────────────────────────────────────────────
_chroma_client: Any = None
_chroma_client_lock = threading.Lock()


def _get_chroma_client() -> Any:
    """懒加载并复用进程级 `PersistentClient`，避免每次 `search` 都重建（双检锁）。"""
    global _chroma_client
    if _chroma_client is None:
        with _chroma_client_lock:
            if _chroma_client is None:
                _chroma_client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    return _chroma_client


def warm_up() -> None:
    """
    主动触发所有已配置 embedding 模型 + reranker 的加载，避免首次检索时延抖动。

    使用场景：CLI 启动时由 main._warm_up_rag_models() 调用；其它入口也可显式调用。
    RERANKER_ENABLED=false 时不会加载 CrossEncoder（内层 reranker.warm_up 直接 return）。
    任一模型加载失败仅记录 warning，不抛异常。
    """
    for alias, model_name, _coll in config.iter_active_embeddings():
        try:
            _get_embedding_fn(model_name)
            logger.info("[Retriever] embedding 模型已预热 [%s]: %s", alias, model_name)
        except Exception as e:
            logger.warning("[Retriever] embedding 预热失败 [%s] %s: %s", alias, model_name, e)
    if config.RERANKER_ENABLED:
        try:
            from src.rag.reranker import warm_up as _rerank_warm_up
            _rerank_warm_up()
        except Exception as e:
            logger.warning("[Retriever] reranker 预热失败: %s", e)


@dataclass
class Hit:
    """
    Hit = 「从哪来（source/collection）+ 是什么（document）+ 多相关（distance/score）+ 怎么召回到的（id/retrievers）+ 附加信息（metadata）」打包成的一条检索结果。

    Attributes:
        source:     这条 chunk 来自哪个文件（相对路径，含子目录）。
                    例如：pursue/a3_RAG/1_AgentA.md、resume/resume.md
                    由 ingest 写入 metadata，用于去重。
        document:   chunk 正文（已含 heading 路径前缀，由 splitter 注入）。
        distance:   原始向量距离，cosine 空间下 ∈ [0, 2]，越小越相似; BM25-only 命中时为 0.0。
        collection: 来自哪个 ChromaDB 库,例如：kb_m3、kb_en、kb_zh
        score:      归一化相关性分（越大越好），不同阶段含义不同：
                      · 召回阶段填 RRF 融合分（数值很小，仅作排序用）；
                      · 精排后由 reranker 覆盖为 cross-encoder sigmoid 概率（[0, 1]）。
        id:         chunk 唯一 ID（与 chroma 的 ids 对齐），用于跨检索器融合去重。
        retrievers: 该 Hit 被哪些检索器召回，如 ["dense", "bm25"]，便于排查与阈值策略。
        metadata:   完整 metadata 字典（含 doc_id / lang / page_no / heading_path 等）。
        reranked:   是否经过 cross-encoder 精排打分（True 时 score 才是精排分）；
                    未精排（候选≤1 / 模型降级）的 hit 不应被精排阈值过滤。
    """
    source: str
    document: str
    distance: float
    collection: str
    score: float | None = None
    id: str = ""
    retrievers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] | None = None
    reranked: bool = False


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
            "collection '%s' 不存在，跳过。请先运行: python -m src.rag.ingest -m %s",
            collection_name,
            collection_name.removeprefix("kb_"),
        )
        return []

    count = col.count()
    if count == 0:
        return []

    prefix = _query_prefix_for(model_name)
    effective_query = (prefix + query) if prefix else query

    # 自己编码 query（带缓存）并以 query_embeddings 传入，避免 chroma 每次重复编码
    query_kwargs: dict[str, Any] = {
        "query_embeddings": [list(_embed_query_cached(model_name, effective_query))],
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


# ── 公共入口 ─────────────────────────────────────────────────────────────────

def search(
    query: str,
    top_k: int = config.RAG_TOP_K,
    where: dict | None = None,
    queries: list[str] | None = None,
    rerank: bool | None = None,
) -> list[Hit]:
    """
    在所有已入库的 collection 中检索最相关的 Top-K 文档片段。

    流水线：多 query 召回（dense + BM25）→ 同库 RRF → 跨库 round-robin
    → dense 阈值过滤 → 可选 rerank → per-source 去重 → 截断 top_k。

    Args:
        query:    用户原始问题；召回兜底（queries 为空时）、精排打分、日志均用此串。
        top_k:    最终返回上限，默认读 config.RAG_TOP_K。
        where:    可选 metadata 过滤子句（透传给 chroma 与 BM25）。
        queries:  可选多 query 列表，只影响召回阶段，不改变 rerank 使用的 query。
        rerank:   是否启用 cross-encoder 精排：
                    · None  → 沿用 config.RERANKER_ENABLED；
                    · True  → 强制开启；
                    · False → 强制关闭（评估 ablation 用 search(rerank=False)）。

    Returns:
        Hit 列表，按融合后/精排后 score 降序，长度 ≤ top_k；空列表表示无命中。
    """
    # 复用进程级 chromadb 客户端（不再每次 search 重建）
    client = _get_chroma_client()

    # 是否启用 rerank：参数 > 全局 config
    use_rerank = config.RERANKER_ENABLED if rerank is None else bool(rerank)

    # 召回窗口：开启精排时扩大候选，BM25 与 dense 各取 recall_k 条
    recall_k = top_k * config.RERANKER_RECALL_MULTIPLIER if use_rerank else top_k

    # 多 query 时每条 query 单独召回的窗口要适当收缩，保持总候选量 ~ recall_k
    effective_queries: list[str] = []
    if queries:
        for q in queries:
            qs = (q or "").strip()
            if qs and qs not in effective_queries:
                effective_queries.append(qs)
    if not effective_queries:
        effective_queries = [query]
    n_q = max(len(effective_queries), 1)
    per_query_k = max(top_k, recall_k // n_q)


    logger.info(
        "[search] q=%r n_q=%d rerank=%s top_k=%d recall_k=%d per_query_k=%d",
        query[:60], n_q, use_rerank, top_k, recall_k, per_query_k,
    )

    # 逐 collection 进行 dense + BM25 召回，同 collection 内 RRF 融合
    per_collection_fused: list[list[Hit]] = []
    for alias, model_name, collection_name in config.iter_active_embeddings():
        rankings: list[list[Hit]] = []
        dense_total = 0
        bm25_total = 0
        for q in effective_queries:
            dense_hits = _query_collection(
                client, model_name, collection_name, q, per_query_k, where=where,
            )
            if dense_hits:
                dense_hits.sort(key=lambda h: h.distance)
                rankings.append(dense_hits)
                dense_total += len(dense_hits)
            bm25_hits = _query_bm25(collection_name, q, per_query_k, where=where)
            if bm25_hits:
                rankings.append(bm25_hits)
                bm25_total += len(bm25_hits)

        if not rankings:
            continue

        if len(rankings) == 1:
            fused = rankings[0]
            logger.info(
                "  [%s] %s: 单 ranking %d 条（n_queries=%d）",
                alias, collection_name, len(fused), n_q,
            )
        else:
            fused = _rrf_fuse(rankings, k=config.RRF_K)
            logger.info(
                "  [%s] %s: rankings=%d (dense=%d, bm25=%d, n_queries=%d) → fused=%d",
                alias, collection_name, len(rankings),
                dense_total, bm25_total, n_q, len(fused),
            )

        per_collection_fused.append(fused)

    if not per_collection_fused:
        logger.info("[search] no candidates from any collection → return []")
        return []

    # 跨 collection round-robin 合并（避免某模型距离空间挤压另一模型）
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
    logger.info(
        "[search] round-robin merged: %d collections → %d cands (cap=%d)",
        len(per_collection_fused), len(candidates), recall_k,
    )

    # Dense 阈值过滤， 每个模型自己不同的阈值
    # BM25 命中的 chunk 不过滤
    before = len(candidates)
    any_filter_active = config.RAG_DENSE_MIN_SCORE > 0 or any(
        v > 0 for v in config.RAG_DENSE_MIN_SCORE_PER_MODEL.values()
    )
    if any_filter_active:
        def _keep(h: Hit) -> bool:
            if "bm25" in h.retrievers:
                return True
            threshold = config.min_dense_score_for_collection(h.collection)
            if threshold <= 0:
                return True
            if "dense" in h.retrievers:
                dense_like_score = 1.0 - h.distance
            else:
                dense_like_score = h.score or 0.0
            return dense_like_score >= threshold

        candidates = [h for h in candidates if _keep(h)]
        logger.info(
            "[search] dense filter: %d → %d (BM25 命中豁免)",
            before, len(candidates),
        )
    else:
        logger.info("[search] dense filter: skipped (all thresholds <= 0)")

    if not candidates:
        logger.info("[search] no candidates left after dense filter → return []")
        return []

    # Cross-Encoder re-rank
    if use_rerank:
        # 局部导入并用别名 _do_rerank，避免与本函数 rerank 参数同名遮蔽
        from src.rag.reranker import rerank as _do_rerank
        cands_in = len(candidates)
        top_hits = _do_rerank(query=query, hits=candidates, top_k=max(top_k * 2, top_k))
        logger.info(
            "[search] rerank: %d → %d (cross-encoder ON)",
            cands_in, len(top_hits),
        )

        # 精排后 min_score 阈值过滤：只过滤真正经过精排打分（reranked=True）的 hit。
        # 未精排的 hit（候选≤1 / 模型降级）其 score 仍是召回阶段 RRF 小分，
        # 用精排阈值过滤会把它们全删（曾导致 multiplier=2 时召回归零），故放行。
        min_rerank = config.RAG_RERANK_MIN_SCORE
        if min_rerank > 0:
            before = len(top_hits)
            top_hits = [
                h for h in top_hits
                if (not h.reranked) or (h.score or 0.0) >= min_rerank
            ]
            logger.info(
                "[search] rerank min_score filter: %d → %d (min=%.3f)",
                before, len(top_hits), min_rerank,
            )
        else:
            logger.info(
                "[search] rerank min_score filter: skipped (min=%.3f)",
                min_rerank,
            )
    else:
        top_hits = candidates
        logger.info(
            "[search] rerank: skipped (use_rerank=False, %d cands kept as-is)",
            len(top_hits),
        )

    # 按 source 文件去重，避免一个长文档把 top_k 全部占满
    before_dedupe = len(top_hits)
    deduped = _dedupe_by_source(top_hits, config.RAG_K_PER_SOURCE)
    logger.info(
        "[search] dedupe per_source=%d: %d → %d",
        config.RAG_K_PER_SOURCE, before_dedupe, len(deduped),
    )

    final = deduped[:top_k]
    logger.info("[search] truncate top_k=%d: %d → %d", top_k, len(deduped), len(final))
    return final


def format_search_results(
    hits: list[Hit],
    citation_nums: list[int] | None = None,
) -> str:
    """
    将检索命中列表格式化为 LLM 可消费的字符串。

    展示信息：retrievers（哪些检索器召回）、heading_path 与 page_no（如有），
    便于 LLM 自我评估命中质量并在回答中给出精准引用。

    Args:
        hits:          search() 返回的命中列表，为空时返回提示文本。
        citation_nums: 可选编号列表，长度与 `hits` 等长；传入时第 i 段前缀
                       使用 `[citation_nums[i]]`（用于跨 tool_call 的全局编号）；
                       不传则退化为 1..N 局部 enumerate。

    Returns:
        格式化文本，每条独立一段，段间用 "---" 分隔。
    """
    if not hits:
        return (
            "知识库为空或尚未初始化。\n"
            "请运行以下命令完成文档入库：\n"
            "  python -m src.rag.ingest -m en   # 英文文档\n"
            "  python -m src.rag.ingest -m zh   # 中文文档"
        )

    # RAG 召回内容是"非用户主控"外部数据，进 LLM context 前过 security_filter：
    # ① 每条 hit.document 走 scrub_injection 段级删除已知注入模板；
    # ② 命中 injection 时段头追加 "[⚠️ 已清洗]" 提示给 LLM；
    # ③ 整个返回值用 wrap_untrusted(kind="doc") 包装，配合 SYSTEM_PROMPT 数据隔离原则段
    # 让 LLM 把标签内的"指令"识别为数据。
    from src.agent.core.security_filter import scrub_injection, wrap_untrusted

    parts: list[str] = []
    any_scrubbed = False
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

        # 传入 citation_nums 时改用 builder 分配的全局编号，
        # 让 LLM 的引用编号与 Agent.run() 末尾渲染的 sources 块对齐
        n = citation_nums[i - 1] if citation_nums is not None else i

        cleaned_doc, scrubbed = scrub_injection(hit.document)
        any_scrubbed = any_scrubbed or scrubbed
        flag = " [⚠️ 已清洗]" if scrubbed else ""
        parts.append(
            f"[{n}] 来源: {hit.source}（相关性: {score_str}，库: {hit.collection}{loc_str}）{flag}\n"
            f"{cleaned_doc}"
        )
    if any_scrubbed:
        from src.stores.security_event_store import EVENT_SCRUB, record_security_event
        record_security_event(EVENT_SCRUB, "知识库检索")
    return wrap_untrusted("\n\n---\n\n".join(parts), kind="doc")
