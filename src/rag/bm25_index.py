"""
BM25 倒排索引（自实现，零外部依赖）

为什么自己写：
    - rank_bm25 是个轻量纯 Python 库，但额外加一个依赖本工程并不划算；
    - BM25Okapi 公式 ~30 行就能实现；
    - 需要一些工程化扩展（按 doc_id 批量删除、与 ChromaDB 的 chunk id 对齐、
      pickle 持久化），自己写更直接。

工作机制：
    - 与 ChromaDB collection 一一对应，索引文件 bm25_<collection>.pkl 默认存于 BM25_INDEX_DIR；
    - 入库（ingest）时与 chroma 共享 ids/metadatas，正文只用于分词后丢弃；
    - 检索命中后由 retriever 按 chunk id 回 Chroma 取正文；
    - 分词器：英文走 whitespace + lowercase；中文走 bigram（连续 2 字符）；混合自动并行。

不做什么：
    - 不做拼写纠错 / 同义词扩展；这一层留给上游 query 改写。
"""

from __future__ import annotations

import contextlib
import gc
import json
import logging
import math
import os
import pickle
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

import src.config as config
from src.rag.chroma_client import get_chroma_client

logger = logging.getLogger(__name__)

_INDEX_VERSION = 2
_SCAN_BATCH = 256

# ── 分词 ──────────────────────────────────────────────────────────────────────

_ALNUM_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

_EN_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "this", "that", "these", "those", "it", "as", "at",
    "by", "with", "from", "but", "if", "then", "than", "so", "such", "not",
})


def tokenize(text: str) -> list[str]:
    """混合分词：英文/数字按空白切并 lowercase；中文按 bigram。"""
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
    """BM25 索引中的单条文档记录（不持久化正文）。"""

    id: str
    metadata: dict
    tf: Counter[str] = field(default_factory=Counter)
    doc_len: int = 0
    # 兼容旧版 / 管理端读取；正文存于 Chroma，检索时回表补齐。
    document: str = field(default="", repr=False)
    tokens: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.tokens and not self.tf:
            self.tf = Counter(self.tokens)
            self.doc_len = len(self.tokens)
        elif self.tf and not self.doc_len:
            self.doc_len = sum(self.tf.values())


def _match_where(metadata: dict, where: dict | None) -> bool:
    """简易 where 子句匹配，支持 ChromaDB 的常用算子子集。"""
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
    """单 collection 维度的 BM25 索引。线程安全；统计量增量维护。"""

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
        self._tf: dict[str, Counter[str]] = {}
        self._doc_len: dict[str, int] = {}
        self._df: Counter[str] = Counter()
        self._total_token_len: int = 0
        self._avg_dl: float = 0.0
        self._idf: dict[str, float] = {}
        self._stats_dirty: bool = True
        self._lock = RLock()

    def _refresh_idf(self) -> None:
        n = len(self.docs)
        if n == 0:
            self._idf = {}
            self._avg_dl = 0.0
            self._stats_dirty = False
            return
        self._avg_dl = self._total_token_len / n
        self._idf = {
            term: math.log((n - cnt + 0.5) / (cnt + 0.5) + 1)
            for term, cnt in self._df.items()
        }
        self._stats_dirty = False

    def _remove_entry(self, doc_id: str) -> None:
        doc = self.docs.pop(doc_id, None)
        tf = self._tf.pop(doc_id, None)
        self._doc_len.pop(doc_id, None)
        if not doc or not tf:
            return
        self._total_token_len -= doc.doc_len
        for term in tf:
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]
        self._stats_dirty = True

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        with self._lock:
            for chunk_id, doc_text, md in zip(ids, documents, metadatas):
                if chunk_id in self.docs:
                    self._remove_entry(chunk_id)
                tokens = tokenize(doc_text)
                tf = Counter(tokens)
                entry = BM25Doc(
                    id=chunk_id,
                    metadata=dict(md or {}),
                    tf=tf,
                    doc_len=len(tokens),
                )
                self.docs[chunk_id] = entry
                self._tf[chunk_id] = tf
                self._doc_len[chunk_id] = entry.doc_len
                self._total_token_len += entry.doc_len
                for term in tf:
                    self._df[term] += 1
                self._stats_dirty = True

    def delete_ids(self, ids: list[str]) -> int:
        with self._lock:
            removed = 0
            for chunk_id in ids:
                if chunk_id in self.docs:
                    self._remove_entry(chunk_id)
                    removed += 1
            return removed

    def delete_by_doc_id(self, doc_id: str) -> int:
        """按 metadata.doc_id 批量删除（ingest 文件级 upsert 用）。"""
        with self._lock:
            target = [
                cid for cid, doc in self.docs.items()
                if doc.metadata.get("doc_id") == doc_id
            ]
            return self.delete_ids(target)

    def search(
        self,
        query: str,
        top_k: int = 10,
        where: dict | None = None,
    ) -> list[tuple[BM25Doc, float]]:
        """返回 [(BM25Doc, score)] 按 BM25 score 降序，最多 top_k 条。"""
        with self._lock:
            if self._stats_dirty:
                self._refresh_idf()
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

    @classmethod
    def load_or_new(cls, collection_name: str, path: Path) -> "BM25Index":
        """从 pickle 文件加载；不存在或文件损坏时返回空索引。"""
        if path.exists():
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
                return cls._from_pickle_data(collection_name, data)
            except Exception as e:
                logger.warning("[BM25] 加载索引失败，已重建空索引: %s — %s", path, e)
        return cls(collection_name)

    @classmethod
    def _from_pickle_data(cls, collection_name: str, data: Any) -> "BM25Index":
        if isinstance(data, dict) and data.get("version") == _INDEX_VERSION:
            idx = cls(
                data.get("collection_name") or collection_name,
                k1=data.get("k1"),
                b=data.get("b"),
            )
            for chunk_id, row in (data.get("entries") or {}).items():
                tf = Counter(row.get("tf") or {})
                doc_len = int(row.get("doc_len") or sum(tf.values()))
                idx.docs[chunk_id] = BM25Doc(
                    id=chunk_id,
                    metadata=dict(row.get("metadata") or {}),
                    tf=tf,
                    doc_len=doc_len,
                )
                idx._tf[chunk_id] = tf
                idx._doc_len[chunk_id] = doc_len
            idx._df = Counter(data.get("df") or {})
            idx._total_token_len = int(data.get("total_token_len") or 0)
            idx._stats_dirty = True
            return idx

        # v1：整库 BM25Doc（含正文 + tokens）
        idx = cls(
            collection_name,
            k1=data.get("k1") if isinstance(data, dict) else None,
            b=data.get("b") if isinstance(data, dict) else None,
        )
        legacy_docs = (data.get("docs") if isinstance(data, dict) else None) or {}
        for chunk_id, doc in legacy_docs.items():
            if isinstance(doc, BM25Doc):
                tokens = doc.tokens or tokenize(doc.document)
            else:
                tokens = tokenize(getattr(doc, "document", "") or "")
            tf = Counter(tokens)
            entry = BM25Doc(
                id=chunk_id,
                metadata=dict(getattr(doc, "metadata", None) or {}),
                tf=tf,
                doc_len=len(tokens),
            )
            idx.docs[chunk_id] = entry
            idx._tf[chunk_id] = tf
            idx._doc_len[chunk_id] = entry.doc_len
            idx._total_token_len += entry.doc_len
            for term in tf:
                idx._df[term] += 1
        idx._stats_dirty = True
        return idx

    def to_pickle_data(self) -> dict:
        with self._lock:
            return {
                "version": _INDEX_VERSION,
                "collection_name": self.collection_name,
                "k1": self.k1,
                "b": self.b,
                "entries": {
                    cid: {
                        "metadata": doc.metadata,
                        "tf": dict(doc.tf),
                        "doc_len": doc.doc_len,
                    }
                    for cid, doc in self.docs.items()
                },
                "df": dict(self._df),
                "total_token_len": self._total_token_len,
            }


def get_index_path(collection_name: str) -> Path:
    """BM25 索引文件路径，命名 bm25_<collection>.pkl。"""
    base_str = config.BM25_INDEX_DIR or config.CHROMA_DB_PATH
    base = Path(base_str).resolve()
    return base / f"bm25_{collection_name}.pkl"


def get_manifest_path(collection_name: str) -> Path:
    """BM25 轻量 manifest（L1 列表 / L2 元数据，不含 tf/idf）。"""
    pkl = get_index_path(collection_name)
    return pkl.with_name(f"{pkl.stem}.manifest.json")


def get_chunks_list_path(collection_name: str) -> Path:
    """BM25 分块清单 jsonl（id + metadata + tokens），供管理端 L2 列表。"""
    pkl = get_index_path(collection_name)
    return pkl.with_name(f"{pkl.stem}.chunks.jsonl")


_SIDECAR_LOCK_WAIT_SEC = 30.0
_SIDECAR_LOCK_POLL_SEC = 0.1


def _sidecar_lock_path(pkl_path: Path) -> Path:
    return pkl_path.with_suffix(pkl_path.suffix + ".sidecar.lock")


@contextlib.contextmanager
def _sidecar_write_lock(pkl_path: Path):
    """跨进程互斥：避免多进程/多 worker 同时写同一 chunks.jsonl.tmp。"""
    lock_path = _sidecar_lock_path(pkl_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    deadline = time.monotonic() + _SIDECAR_LOCK_WAIT_SEC
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"BM25 sidecar 写入锁超时: {lock_path}") from None
            time.sleep(_SIDECAR_LOCK_POLL_SEC)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        lock_path.unlink(missing_ok=True)


def _write_index_sidecars_unlocked(idx: BM25Index, pkl_path: Path) -> None:
    """写 manifest + chunks.jsonl（调用方负责加锁）。"""
    chunks_path = get_chunks_list_path(idx.collection_name)
    manifest_path = get_manifest_path(idx.collection_name)
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    chunks_tmp = chunks_path.with_suffix(chunks_path.suffix + ".tmp")
    with open(chunks_tmp, "w", encoding="utf-8") as f:
        for chunk_id, doc in sorted(idx.docs.items()):
            row = {
                "id": chunk_id,
                "metadata": dict(doc.metadata or {}),
                "tokens": doc.doc_len,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    chunks_tmp.replace(chunks_path)

    stat = pkl_path.stat()
    manifest = {
        "version": 1,
        "collection": idx.collection_name,
        "docs": len(idx.docs),
        "k1": idx.k1,
        "b": idx.b,
        "pkl_mtime": stat.st_mtime,
        "pkl_bytes": stat.st_size,
        "chunks_file": chunks_path.name,
    }
    manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    with open(manifest_tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    manifest_tmp.replace(manifest_path)


def _write_index_sidecars(idx: BM25Index, pkl_path: Path) -> None:
    """写盘 pkl 后同步 manifest + chunks.jsonl，管理巡检无需反序列化全索引。"""
    with _sidecar_write_lock(pkl_path):
        _write_index_sidecars_unlocked(idx, pkl_path)


def rewrite_index_sidecars(collection_name: str) -> dict:
    """从 pkl 重建 manifest + chunks.jsonl，不改动 pkl 本体。"""
    pkl_path = get_index_path(collection_name)
    if not pkl_path.is_file():
        raise ValueError(f"BM25 pkl 不存在: {collection_name}")
    drop_index(collection_name)
    idx = BM25Index.load_or_new(collection_name, pkl_path)
    if not idx.docs:
        raise ValueError(f"BM25 pkl 为空或无法加载: {collection_name}")
    with _sidecar_write_lock(pkl_path):
        _write_index_sidecars_unlocked(idx, pkl_path)
    return {"collection": collection_name, "docs": len(idx.docs), "ok": True}


def save_index(idx: BM25Index, path: Path) -> None:
    """安全持久化：先写临时文件再 rename，避免半写入导致索引损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = idx.to_pickle_data()
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    _write_index_sidecars(idx, path)


_index_cache: dict[str, BM25Index] = {}
_cache_lock = RLock()
_pin_refs: dict[str, int] = {}
_idle_timers: dict[str, threading.Timer] = {}


def _cancel_idle_timer(collection_name: str) -> None:
    timer = _idle_timers.pop(collection_name, None)
    if timer is not None:
        timer.cancel()


def _drop_from_cache(collection_name: str, *, reason: str) -> None:
    with _cache_lock:
        if collection_name not in _index_cache:
            return
        _cancel_idle_timer(collection_name)
        _pin_refs.pop(collection_name, None)
        _index_cache.pop(collection_name, None)
    logger.info("[BM25] 释放进程缓存 collection=%s (%s)", collection_name, reason)
    gc.collect()


def _evict_lru(*, keep: str) -> None:
    max_n = max(1, int(config.BM25_INDEX_CACHE_MAX))
    while len(_index_cache) >= max_n:
        evicted = False
        for name in list(_index_cache):
            if name == keep:
                continue
            if _pin_refs.get(name, 0) > 0:
                continue
            _drop_from_cache(name, reason="LRU 淘汰")
            evicted = True
            break
        if not evicted:
            break


def _schedule_idle_release(collection_name: str) -> None:
    idle_sec = int(config.BM25_INDEX_IDLE_RELEASE_SEC)
    if idle_sec <= 0:
        _drop_from_cache(collection_name, reason="检索结束立即释放")
        return
    _cancel_idle_timer(collection_name)

    def _fire() -> None:
        _idle_timers.pop(collection_name, None)
        with _cache_lock:
            if _pin_refs.get(collection_name, 0) > 0:
                return
            if collection_name not in _index_cache:
                return
        _drop_from_cache(collection_name, reason=f"空闲 {idle_sec}s")

    timer = threading.Timer(idle_sec, _fire)
    timer.daemon = True
    _idle_timers[collection_name] = timer
    timer.start()


def pin_index(collection_name: str) -> None:
    """检索临界区开始：阻止空闲释放 / LRU 淘汰。"""
    with _cache_lock:
        _cancel_idle_timer(collection_name)
        _pin_refs[collection_name] = _pin_refs.get(collection_name, 0) + 1


def unpin_index(collection_name: str) -> None:
    """检索临界区结束：引用归零后按空闲策略释放。"""
    with _cache_lock:
        refs = _pin_refs.get(collection_name, 0)
        if refs <= 1:
            _pin_refs.pop(collection_name, None)
            should_release = collection_name in _index_cache
        else:
            _pin_refs[collection_name] = refs - 1
            should_release = False
    if should_release:
        _schedule_idle_release(collection_name)


def get_index(collection_name: str) -> BM25Index:
    """获取（或加载）指定 collection 的 BM25 索引；入库路径用，检索请 pin/unpin。"""
    with _cache_lock:
        _evict_lru(keep=collection_name)
        idx = _index_cache.get(collection_name)
        if idx is None:
            idx = BM25Index.load_or_new(collection_name, get_index_path(collection_name))
            _index_cache[collection_name] = idx
        return idx


def drop_index(collection_name: str) -> None:
    """从进程缓存移除指定 collection 的索引。"""
    _drop_from_cache(collection_name, reason="显式 drop")


def commit_index(collection_name: str, *, release: bool = False) -> None:
    """把进程内缓存的 BM25 索引写入磁盘；release 为真时写盘后移出进程缓存。"""
    with _cache_lock:
        idx = _index_cache.get(collection_name)
    if idx is None:
        return
    save_index(idx, get_index_path(collection_name))
    if release:
        drop_index(collection_name)


def rebuild_bm25_from_chroma(
    collection_name: str,
    *,
    batch_size: int = _SCAN_BATCH,
) -> int:
    """从 Chroma collection 全量重建 BM25 索引，返回写入的 chunk 数。"""
    client = get_chroma_client()
    try:
        collection = client.get_collection(name=collection_name)
    except Exception as exc:
        raise ValueError(f"Chroma collection 不存在: {collection_name}") from exc

    idx = BM25Index(collection_name)
    total = collection.count()
    offset = 0
    written = 0
    while offset < total:
        got = collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = got.get("ids") or []
        documents = got.get("documents") or []
        metadatas = got.get("metadatas") or []
        if not ids:
            break
        idx.upsert(ids, documents, metadatas)
        written += len(ids)
        offset += len(ids)

    path = get_index_path(collection_name)
    save_index(idx, path)
    with _cache_lock:
        _evict_lru(keep=collection_name)
        _index_cache[collection_name] = idx
    logger.info(
        "[BM25] 已从 Chroma 重建 %s → %d 块，写入 %s",
        collection_name,
        written,
        path,
    )
    return written
