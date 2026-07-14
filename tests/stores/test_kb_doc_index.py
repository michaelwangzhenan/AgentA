"""KB 文档级索引 store UT —— 万级分页与筛选。"""

from __future__ import annotations

import time

import pytest

from src.stores.kb_doc_index import KBDocIndexStore


@pytest.fixture
def store(tmp_path) -> KBDocIndexStore:
    db = tmp_path / "kb_doc_index.db"
    return KBDocIndexStore(str(db))


def _seed(store: KBDocIndexStore, collection: str, n: int) -> None:
    for i in range(n):
        store.upsert(
            collection,
            doc_id=f"doc-{i:05d}",
            filename=f"file-{i:05d}.md",
            source=f"dir/file-{i:05d}.md",
            ext=".md",
            lang="zh" if i % 2 == 0 else "en",
            mtime=float(1_700_000_000 + i),
            ingested_at=float(1_700_100_000 + i),
            chunks=3,
            total_chars=100 + i,
        )


def test_list_page_basic(store: KBDocIndexStore) -> None:
    coll = "kb_zh"
    _seed(store, coll, 25)
    docs, total = store.list_page(coll, page=1, page_size=10)
    assert total == 25
    assert len(docs) == 10
    assert docs[0]["doc_id"] == "doc-00024"  # ingested_at desc


def test_list_page_filter_and_sort(store: KBDocIndexStore) -> None:
    coll = "kb_m3"
    _seed(store, coll, 50)
    docs, total = store.list_page(
        coll,
        page=1,
        page_size=20,
        sort_by="filename",
        desc=False,
        lang="zh",
    )
    assert total == 25
    assert len(docs) == 20
    assert all(d["lang"] == "zh" for d in docs)
    assert docs[0]["filename"] <= docs[-1]["filename"]


def test_collection_stats_and_delete(store: KBDocIndexStore) -> None:
    coll = "kb_en"
    _seed(store, coll, 3)
    docs, chunks = store.collection_stats(coll)
    assert docs == 3
    assert chunks == 9
    assert store.delete_doc(coll, "doc-00001")
    assert store.collection_stats(coll)[0] == 2
    assert store.clear_collection(coll) == 2
    assert store.collection_stats(coll) == (0, 0)


def test_list_page_10k_performance(store: KBDocIndexStore) -> None:
    """万级 doc_id 分页：查询应在亚秒级完成且只返回一页。"""
    coll = "kb_perf"
    n = 10_000
    rows = [
        {
            "doc_id": f"doc-{i:05d}",
            "filename": f"file-{i:05d}.md",
            "source": f"dir/file-{i:05d}.md",
            "ext": ".md",
            "lang": "zh",
            "mtime": float(1_700_000_000 + i),
            "ingested_at": float(1_700_100_000 + i),
            "chunks": 3,
            "total_chars": 100 + i,
        }
        for i in range(n)
    ]
    store.replace_collection(coll, rows)

    t0 = time.perf_counter()
    docs, total = store.list_page(coll, page=1, page_size=100, sort_by="ingested_at", desc=True)
    elapsed = time.perf_counter() - t0

    assert total == n
    assert len(docs) == 100
    assert docs[0]["doc_id"] == "doc-09999"
    assert elapsed < 2.0, f"分页过慢: {elapsed:.3f}s"
