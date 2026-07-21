"""RAG 本地模型缓存释放 UT。"""

from __future__ import annotations

from src.rag import reranker
from src.rag.retriever import _embed_query_cached, _embedding_fn_cache, clear_model_caches


def test_clear_model_caches_empties_dicts(monkeypatch) -> None:
    _embedding_fn_cache["local:test"] = object()
    reranker._cross_encoder_cache["m"] = object()  # type: ignore[assignment]

    monkeypatch.setattr(
        "src.rag.retriever._get_embedding_fn",
        lambda _name, use_api=None: (lambda texts: [[0.1, 0.2]]),
    )
    _embed_query_cached("m", "q", False)
    assert _embed_query_cached.cache_info().currsize == 1

    clear_model_caches()
    assert not _embedding_fn_cache
    assert not reranker._cross_encoder_cache
    assert _embed_query_cached.cache_info().currsize == 0
