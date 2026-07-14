"""BM25 索引：增量统计、紧凑持久化、Chroma 重建。"""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from src.rag.bm25_index import (
    BM25Index,
    commit_index,
    drop_index,
    get_index_path,
    rebuild_bm25_from_chroma,
    save_index,
    tokenize,
)


def test_tokenize_mixed_cn_en() -> None:
    tokens = tokenize("5G NR handover 切换")
    assert "5g" in tokens
    assert any("\u4e00" <= ch <= "\u9fff" for t in tokens for ch in t)


def test_incremental_upsert_and_delete() -> None:
    idx = BM25Index("kb_test")
    idx.upsert(
        ["c1", "c2"],
        ["alpha beta gamma", "alpha delta"],
        [{"doc_id": "d1"}, {"doc_id": "d2"}],
    )
    hits = idx.search("alpha", top_k=5)
    assert len(hits) == 2
    assert idx.delete_by_doc_id("d1") == 1
    hits = idx.search("gamma", top_k=5)
    assert len(hits) == 0


def test_save_load_v2_without_document_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.rag.bm25_index.get_index_path",
        lambda coll: tmp_path / f"bm25_{coll}.pkl",
    )
    idx = BM25Index("kb_test")
    long_text = "keyword " * 200
    idx.upsert(["x1"], [long_text], [{"doc_id": "doc"}])
    save_index(idx, tmp_path / "bm25_kb_test.pkl")

    with open(tmp_path / "bm25_kb_test.pkl", "rb") as f:
        raw = pickle.load(f)
    assert raw["version"] == 2
    assert "document" not in str(raw["entries"]["x1"])

    loaded = BM25Index.load_or_new("kb_test", tmp_path / "bm25_kb_test.pkl")
    assert loaded.docs["x1"].document == ""
    assert loaded.docs["x1"].doc_len > 0
    assert loaded.search("keyword", top_k=1)


def test_commit_index_writes_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bm25_kb_test.pkl"
    monkeypatch.setattr("src.rag.bm25_index.get_index_path", lambda coll: path)
    drop_index("kb_test")
    idx = BM25Index("kb_test")
    idx.upsert(["a"], ["hello world"], [{}])
    monkeypatch.setattr("src.rag.bm25_index._index_cache", {"kb_test": idx})
    commit_index("kb_test")
    assert path.exists()


def test_commit_index_release_drops_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bm25_kb_test.pkl"
    monkeypatch.setattr("src.rag.bm25_index.get_index_path", lambda coll: path)
    drop_index("kb_test")
    idx = BM25Index("kb_test")
    idx.upsert(["a"], ["hello world"], [{}])
    monkeypatch.setattr("src.rag.bm25_index._index_cache", {"kb_test": idx})
    commit_index("kb_test", release=True)
    from src.rag.bm25_index import _index_cache

    assert path.exists()
    assert "kb_test" not in _index_cache


def test_rebuild_bm25_from_chroma(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeCollection:
        def count(self) -> int:
            return 2

        def get(self, *, limit: int, offset: int, include: list[str]):
            if offset == 0:
                return {
                    "ids": ["c1", "c2"],
                    "documents": ["foo bar", "bar baz"],
                    "metadatas": [{"doc_id": "d1"}, {"doc_id": "d2"}],
                }
            return {"ids": [], "documents": [], "metadatas": []}

    class FakeClient:
        def get_collection(self, name: str):
            return FakeCollection()

    monkeypatch.setattr("src.rag.bm25_index.chromadb.PersistentClient", lambda path: FakeClient())
    monkeypatch.setattr(
        "src.rag.bm25_index.get_index_path",
        lambda coll: tmp_path / f"bm25_{coll}.pkl",
    )
    drop_index("kb_m3")
    n = rebuild_bm25_from_chroma("kb_m3")
    assert n == 2
    loaded = BM25Index.load_or_new("kb_m3", tmp_path / "bm25_kb_m3.pkl")
    assert len(loaded.docs) == 2
