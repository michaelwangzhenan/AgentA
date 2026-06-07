"""
BM25 倒排索引（自实现，零外部依赖）

为什么自己写：
    - rank_bm25 是个轻量纯 Python 库，但额外加一个依赖本工程并不划算；
    - BM25Okapi 公式 ~30 行就能实现；
    - 需要一些工程化扩展（按 doc_id 批量删除、与 ChromaDB 的 chunk id 对齐、
      pickle 持久化），自己写更直接。

工作机制：
    - 与 ChromaDB collection 一一对应，索引文件 bm25_<collection>.pkl 存于 chroma_db 旁；
    - 入库（ingest）时与 chroma 共享 ids/documents/metadatas，方便后续 RRF 按 id 对齐；
    - 分词器：英文走 whitespace + lowercase；中文走 bigram（连续 2 字符）；混合自动并行。

不做什么：
    - 不做拼写纠错 / 同义词扩展；这一层留给上游 query 改写。
"""

from __future__ import annotations

import logging
import math
import pickle
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

import src.config as config

logger = logging.getLogger(__name__)


# ── 分词 ──────────────────────────────────────────────────────────────────────

# 英文/数字 token：连续字母数字下划线
_ALNUM_RE = re.compile(r"[A-Za-z0-9_]+")
# 中文连续段（CJK 统一汉字）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

# 极简英文停用词（避免高频虚词膨胀 idf 噪声）；不做激进过滤以免误伤
_EN_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "this", "that", "these", "those", "it", "as", "at",
    "by", "with", "from", "but", "if", "then", "than", "so", "such", "not",
})


def tokenize(text: str) -> list[str]:
    """
    混合分词：英文/数字按空白切并 lowercase；中文按 bigram。

    设计取舍：
      - bigram 在中文 BM25 上召回率优于 unigram、且无需 jieba 词典依赖；
      - 对单字符的中文短串（如 "我"）也保留 unigram 兜底，避免 1 字 query 完全无 token；
      - 英文走停用词过滤（轻量），仅过滤常见的 functional words。
    """
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    for m in _ALNUM_RE.finditer(text):
        t = m.group()
        if t not in _EN_STOPWORDS:
            tokens.append(t)
    for m in _CJK_RE.finditer(text):
        run = m.group()
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run) - 1):
                tokens.append(run[i : i + 2])
    return tokens


# ── 索引数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class BM25Doc:
    """BM25 索引中的单条文档记录。"""

    id: str
    document: str
    metadata: dict
    tokens: list[str] = field(default_factory=list)


def _match_where(metadata: dict, where: dict | None) -> bool:
    """
    简易 where 子句匹配，支持 ChromaDB 的常用算子子集。

    支持：
      - 直接等值：{"lang": "zh"}
      - $eq / $ne / $in：{"ext": {"$in": [".pdf", ".docx"]}}
    其他算子（$gt 等）暂不支持，遇到时返回 False（保守拒绝）。
    """
    if not where:
        return True
    for key, val in where.items():
        if isinstance(val, dict):
            if "$eq" in val:
                if metadata.get(key) != val["$eq"]:
                    return False
            elif "$ne" in val:
                if metadata.get(key) == val["$ne"]:
                    return False
            elif "$in" in val:
                if metadata.get(key) not in val["$in"]:
                    return False
            else:
                return False
        else:
            if metadata.get(key) != val:
                return False
    return True


# ── 主索引类 ──────────────────────────────────────────────────────────────────


class BM25Index:
    """单 collection 维度的 BM25 索引。线程安全；脏标志触发懒重算。"""

    def __init__(
        self,
        collection_name: str,
        k1: float | None = None,
        b: float | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.k1 = k1 if k1 is not None else config.BM25_K1
        self.b = b if b is not None else config.BM25_B
        self.docs: dict[str, BM25Doc] = {}
        self._dirty: bool = True
        self._idf: dict[str, float] = {}
        self._avg_dl: float = 0.0
        self._doc_len: dict[str, int] = {}
        self._tf: dict[str, Counter] = {}
        self._lock = RLock()

    # —— 写入接口 ——

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        with self._lock:
            for doc_id, doc, md in zip(ids, documents, metadatas):
                self.docs[doc_id] = BM25Doc(
                    id=doc_id,
                    document=doc,
                    metadata=dict(md or {}),
                    tokens=tokenize(doc),
                )
            self._dirty = True

    def delete_ids(self, ids: list[str]) -> int:
        with self._lock:
            removed = 0
            for i in ids:
                if self.docs.pop(i, None) is not None:
                    removed += 1
            if removed:
                self._dirty = True
            return removed

    def delete_by_doc_id(self, doc_id: str) -> int:
        """按 metadata.doc_id 批量删除（ingest 文件级 upsert 用）。"""
        with self._lock:
            target = [
                cid for cid, doc in self.docs.items()
                if doc.metadata.get("doc_id") == doc_id
            ]
            return self.delete_ids(target)

    # —— 查询接口 ——

    def _recompute(self) -> None:
        n = len(self.docs)
        if n == 0:
            self._idf = {}
            self._avg_dl = 0.0
            self._doc_len = {}
            self._tf = {}
            self._dirty = False
            return
        df: Counter[str] = Counter()
        total_len = 0
        self._tf = {}
        self._doc_len = {}
        for doc_id, doc in self.docs.items():
            tf = Counter(doc.tokens)
            self._tf[doc_id] = tf
            self._doc_len[doc_id] = len(doc.tokens)
            total_len += len(doc.tokens)
            for term in tf:
                df[term] += 1
        self._avg_dl = total_len / max(n, 1)
        self._idf = {
            term: math.log((n - cnt + 0.5) / (cnt + 0.5) + 1)
            for term, cnt in df.items()
        }
        self._dirty = False

    def search(
        self,
        query: str,
        top_k: int = 10,
        where: dict | None = None,
    ) -> list[tuple[BM25Doc, float]]:
        """
        返回 [(BM25Doc, score)] 按 BM25 score 降序，最多 top_k 条。
        无任何匹配 token 时返回空列表（与 dense 召回的 "0 命中" 行为一致）。
        """
        with self._lock:
            if self._dirty:
                self._recompute()
            if not self.docs:
                return []
            q_tokens = tokenize(query)
            if not q_tokens:
                return []

            avg_dl = self._avg_dl or 1.0
            scores: dict[str, float] = {}
            for doc_id, tf in self._tf.items():
                doc = self.docs[doc_id]
                if not _match_where(doc.metadata, where):
                    continue
                dl = self._doc_len[doc_id]
                score = 0.0
                for term in q_tokens:
                    f = tf.get(term, 0)
                    if f == 0:
                        continue
                    idf = self._idf.get(term, 0.0)
                    denom = f + self.k1 * (1 - self.b + self.b * dl / avg_dl)
                    score += idf * (f * (self.k1 + 1)) / max(denom, 1e-9)
                if score > 0:
                    scores[doc_id] = score

            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
            return [(self.docs[did], s) for did, s in ranked]

    # —— 持久化 ——

    @classmethod
    def load_or_new(cls, collection_name: str, path: Path) -> "BM25Index":
        """从 pickle 文件加载；不存在或文件损坏时返回空索引。"""
        if path.exists():
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
                idx = cls(
                    collection_name,
                    k1=data.get("k1"),
                    b=data.get("b"),
                )
                idx.docs = data.get("docs") or {}
                idx._dirty = True
                return idx
            except Exception as e:
                logger.warning("[BM25] 加载索引失败，已重建空索引: %s — %s", path, e)
        return cls(collection_name)


def get_index_path(collection_name: str) -> Path:
    """
    BM25 索引文件路径，命名 bm25_<collection>.pkl。
    BM25_INDEX_DIR 配置为空时与 CHROMA_DB_PATH 同级目录。
    """
    base_str = config.BM25_INDEX_DIR or config.CHROMA_DB_PATH
    base = Path(base_str).resolve()
    return base / f"bm25_{collection_name}.pkl"


def save_index(idx: BM25Index, path: Path) -> None:
    """安全持久化：先写临时文件再 rename，避免半写入导致索引损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with idx._lock:  # noqa: SLF001 — 内部状态保护
        if idx._dirty:
            idx._recompute()
        with open(tmp, "wb") as f:
            pickle.dump(
                {
                    "collection_name": idx.collection_name,
                    "k1": idx.k1,
                    "b": idx.b,
                    "docs": idx.docs,
                },
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
    tmp.replace(path)


# ── 进程级缓存 ────────────────────────────────────────────────────────────────

_index_cache: dict[str, BM25Index] = {}
_cache_lock = RLock()


def get_index(collection_name: str) -> BM25Index:
    """获取（或加载）指定 collection 的 BM25 索引；进程内单实例。"""
    with _cache_lock:
        idx = _index_cache.get(collection_name)
        if idx is None:
            idx = BM25Index.load_or_new(collection_name, get_index_path(collection_name))
            _index_cache[collection_name] = idx
        return idx


def drop_index(collection_name: str) -> None:
    """从进程缓存移除指定 collection 的索引。

    底层 pkl 被整体删除 / 重建后调用，下次 `get_index` 会按磁盘最新状态重新加载，
    避免检索端继续持有已失效的索引实例。
    """
    with _cache_lock:
        _index_cache.pop(collection_name, None)
