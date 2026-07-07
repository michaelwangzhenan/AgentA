"""语义答案缓存：相近 query 命中历史答案，跳过整次检索 + 生成。

与 ``query_rewriter`` / ``retriever`` 里的进程内 LRU 是两层（详 docs/iter_14_enh.md §2.1.7）：
LRU 缓存子步骤（改写 / 编码），本模块缓存**最终答案**并做向量命中、持久化、按用户隔离。

存储：独立 ChromaDB collection（与 KB collection 分开，同在 ``CHROMA_DB_PATH``），
余弦相似度空间。一条 = query 向量 + 答案(document) + 元数据(user_id / 过期时间 / 模型)。

红线：读 / 写 / 失效任一出错只记 log、回落正常流程，绝不阻断主对话。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

_HNSW_SPACE = "cosine"


class SemanticCacheStore:
    """语义答案缓存的存取 + 失效（依赖层）。线程安全。"""

    def __init__(self, collection_name: str | None = None) -> None:
        self._collection_name = collection_name or config.SEMANTIC_CACHE_COLLECTION
        self._lock = threading.RLock()
        self._collection: Any = None

    # ── collection 句柄（懒加载，复用 retriever 的进程级 client） ──────────────

    def _get_collection(self) -> Any:
        with self._lock:
            if self._collection is None:
                from src.rag.retriever import _get_chroma_client

                client = _get_chroma_client()
                self._collection = client.get_or_create_collection(
                    name=self._collection_name,
                    metadata={"hnsw:space": _HNSW_SPACE},
                )
            return self._collection

    @staticmethod
    def _embed(query: str) -> list[float]:
        """用 RAG 默认 embedding 模型编码 query；复用 retriever 的 LRU，跟随 backend。"""
        from src.rag import online_api
        from src.rag.retriever import _embed_query_cached

        model_name, _ = config.resolve_embedding(config.DEFAULT_EMBEDDING_ALIAS)
        backend = online_api.embedding_backend_for(model_name)
        return list(_embed_query_cached(model_name, query, backend))

    # ── 查询 ────────────────────────────────────────────────────────────────

    def lookup(
        self, query: str, user_id: int, threshold: float | None = None
    ) -> str | None:
        """按 user_id 隔离 + 向量相似度找最相近的未过期缓存答案；命中返回答案文本。"""
        q = (query or "").strip()
        if not q:
            return None
        threshold = config.SEMANTIC_CACHE_THRESHOLD if threshold is None else threshold
        col = self._get_collection()
        vec = self._embed(q)
        res = col.query(
            query_embeddings=[vec],
            n_results=1,
            where={"user_id": int(user_id)},
            include=["documents", "metadatas", "distances"],
        )
        ids = (res.get("ids") or [[]])[0]
        if not ids:
            logger.info("[cache] 未命中（库内该用户无条目）query=%r", q[:60])
            return None
        dist = (res.get("distances") or [[1.0]])[0][0]
        similarity = 1.0 - float(dist)
        meta = (res.get("metadatas") or [[{}]])[0][0] or {}
        answer = (res.get("documents") or [[""]])[0][0]
        expires_at = int(meta.get("expires_at") or 0)
        now = int(time.time())

        if expires_at and now >= expires_at:
            # 命中但已过期：顺手删掉，按未命中处理
            try:
                col.delete(ids=[ids[0]])
            except Exception:
                pass
            logger.info("[cache] 命中但已过期 sim=%.3f query=%r", similarity, q[:60])
            return None

        if similarity >= threshold:
            logger.info("[cache] 命中 sim=%.3f (阈值 %.3f) query=%r", similarity, threshold, q[:60])
            return answer
        logger.info(
            "[cache] 未命中 最相似 sim=%.3f < 阈值 %.3f query=%r", similarity, threshold, q[:60]
        )
        return None

    # ── 写入 ────────────────────────────────────────────────────────────────

    def put(
        self, query: str, answer: str, user_id: int, model_id: str = "",
        ttl_days: int | None = None,
    ) -> None:
        """写入一条缓存（query 向量 + 答案 + 元数据）。"""
        q = (query or "").strip()
        if not q or not (answer or "").strip():
            return
        ttl_days = config.SEMANTIC_CACHE_TTL_DAYS if ttl_days is None else ttl_days
        now = int(time.time())
        expires_at = now + max(0, int(ttl_days)) * 86400
        col = self._get_collection()
        vec = self._embed(q)
        entry_id = uuid.uuid4().hex
        col.add(
            ids=[entry_id],
            embeddings=[vec],
            documents=[answer],
            metadatas=[{
                "user_id": int(user_id),
                "query": q[:500],
                "created_at": now,
                "expires_at": expires_at,
                "model_id": model_id or "",
            }],
        )
        logger.info("[cache] 写入 id=%s user=%d 过期=%d query=%r", entry_id, user_id, expires_at, q[:60])

    # ── 失效 ────────────────────────────────────────────────────────────────

    def invalidate_all(self) -> None:
        """清空整个缓存 collection（KB 变更时调用：答案依赖 KB，全量作废最稳）。"""
        with self._lock:
            from src.rag.retriever import _get_chroma_client

            client = _get_chroma_client()
            try:
                client.delete_collection(name=self._collection_name)
            except Exception:
                pass
            self._collection = None  # 下次访问懒重建
        logger.info("[cache] 已全量作废（collection=%s）", self._collection_name)

    def delete_for_user(self, user_id: int) -> None:
        """删除某用户的全部缓存（注销 / admin 删号级联调用）。"""
        col = self._get_collection()
        col.delete(where={"user_id": int(user_id)})
        logger.info("[cache] 已清除用户缓存 user=%d", user_id)

    def count(self) -> int:
        try:
            return int(self._get_collection().count())
        except Exception:
            return 0


# ── 进程内单例 + 软失败入口 ──────────────────────────────────────────────────

_shared_store: SemanticCacheStore | None = None
_shared_lock = threading.Lock()


def get_shared_store() -> SemanticCacheStore:
    global _shared_store
    if _shared_store is None:
        with _shared_lock:
            if _shared_store is None:
                _shared_store = SemanticCacheStore()
    return _shared_store


def reset_shared_store_for_testing(store: SemanticCacheStore | None = None) -> None:
    """UT 专用：注入 mock / 重置。生产代码不要调用。"""
    global _shared_store
    _shared_store = store


def lookup_cached(query: str, user_id: int) -> str | None:
    """软失败查询：未启用 / 出错一律返回 None（回落正常流程）。"""
    if not config.SEMANTIC_CACHE_ENABLED:
        return None
    try:
        return get_shared_store().lookup(query, user_id)
    except Exception:
        logger.warning("[cache] lookup 失败（已忽略，回落正常流程）", exc_info=True)
        return None


def store_cached(query: str, answer: str, user_id: int, model_id: str = "") -> None:
    """软失败写入：未启用 / 出错一律忽略（不影响已返回的答案）。"""
    if not config.SEMANTIC_CACHE_ENABLED:
        return
    try:
        get_shared_store().put(query, answer, user_id, model_id=model_id)
    except Exception:
        logger.warning("[cache] store 失败（已忽略，不影响对话）", exc_info=True)


def invalidate_all_soft() -> None:
    """软失败全量作废：KB 变更旁路调用，出错只记 log。"""
    if not config.SEMANTIC_CACHE_ENABLED:
        return
    try:
        get_shared_store().invalidate_all()
    except Exception:
        logger.warning("[cache] invalidate_all 失败（已忽略）", exc_info=True)


def delete_for_user_soft(user_id: int) -> None:
    """软失败清用户缓存：账号删除级联调用。"""
    try:
        get_shared_store().delete_for_user(user_id)
    except Exception:
        logger.warning("[cache] delete_for_user 失败（已忽略）", exc_info=True)
