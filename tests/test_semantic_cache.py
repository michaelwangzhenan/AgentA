"""语义缓存 UT（iter_14）：命中 / 阈值 / 过期 / 用户隔离 / 失效 / 软失败。

不依赖真实 ChromaDB：注入 FakeCollection + 替换 _embed，验证存取与判定逻辑。
"""

from __future__ import annotations

import time

import pytest

import src.config as config
from src.memory import semantic_cache
from src.memory.semantic_cache import SemanticCacheStore


class FakeCollection:
    """最小 chroma collection 替身：按 user_id 过滤，返回可控 distance。"""

    def __init__(self) -> None:
        self.entries: list[dict] = []  # {id, meta, doc}
        self.distance = 0.0            # 下次 query 返回的距离
        self.deleted_ids: list[str] = []

    def add(self, ids, embeddings, documents, metadatas):
        self.entries.append({"id": ids[0], "meta": metadatas[0], "doc": documents[0]})

    def query(self, query_embeddings, n_results, where, include):
        uid = where["user_id"]
        matches = [e for e in self.entries if e["meta"]["user_id"] == uid]
        if not matches:
            return {"ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]]}
        e = matches[-1]
        return {
            "ids": [[e["id"]]],
            "distances": [[self.distance]],
            "metadatas": [[e["meta"]]],
            "documents": [[e["doc"]]],
        }

    def delete(self, ids=None, where=None):
        if ids:
            self.deleted_ids.extend(ids)
            self.entries = [e for e in self.entries if e["id"] not in ids]
        if where and "user_id" in where:
            self.entries = [e for e in self.entries if e["meta"]["user_id"] != where["user_id"]]

    def count(self):
        return len(self.entries)


@pytest.fixture
def store(monkeypatch):
    s = SemanticCacheStore(collection_name="test_cache")
    s._collection = FakeCollection()
    monkeypatch.setattr(s, "_embed", lambda q: [0.0, 0.0, 0.0])
    return s


def test_put_then_hit(store):
    store.put("什么是RAG", "RAG 是检索增强生成", user_id=1, model_id="m")
    store._collection.distance = 0.01  # sim 0.99 ≥ 0.95
    assert store.lookup("什么是RAG", user_id=1) == "RAG 是检索增强生成"


def test_miss_below_threshold(store):
    store.put("什么是RAG", "答案", user_id=1)
    store._collection.distance = 0.2  # sim 0.8 < 0.95
    assert store.lookup("什么是RAG", user_id=1) is None


def test_user_isolation(store):
    store.put("q", "user1 的答案", user_id=1)
    store._collection.distance = 0.0
    assert store.lookup("q", user_id=2) is None  # 别的用户查不到


def test_expired_entry_is_skipped_and_deleted(store):
    store.put("q", "旧答案", user_id=1, ttl_days=7)
    # 手动把过期时间改到过去
    store._collection.entries[-1]["meta"]["expires_at"] = int(time.time()) - 10
    store._collection.distance = 0.0
    assert store.lookup("q", user_id=1) is None
    assert store._collection.deleted_ids  # 命中过期条目被顺手删掉


def test_delete_for_user(store):
    store.put("q", "a", user_id=1)
    store.put("q", "b", user_id=2)
    store.delete_for_user(1)
    assert store._collection.count() == 1


# ── 软失败入口 ────────────────────────────────────────────────────────────────


def test_lookup_cached_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_CACHE_ENABLED", False)
    assert semantic_cache.lookup_cached("q", 1) is None


def test_lookup_cached_swallows_errors(monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_CACHE_ENABLED", True)

    class Boom(SemanticCacheStore):
        def lookup(self, *a, **k):
            raise RuntimeError("boom")

    semantic_cache.reset_shared_store_for_testing(Boom())
    try:
        assert semantic_cache.lookup_cached("q", 1) is None  # 不抛
    finally:
        semantic_cache.reset_shared_store_for_testing(None)
