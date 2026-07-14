"""文档级索引残缺时自动修复列表与 L1 统计。"""

from __future__ import annotations

from unittest.mock import MagicMock

import src.rag.ingest as ingest


def test_list_page_auto_backfills_when_index_incomplete(monkeypatch) -> None:
    store = MagicMock()
    store.list_page.side_effect = [
        ([{"doc_id": "only"}], 1),
        ([{"doc_id": "a"}, {"doc_id": "b"}], 2),
    ]
    store.collection_stats.return_value = (1, 5)  # index 只有 5 chunk
    monkeypatch.setattr(
        "src.stores.kb_doc_index.get_kb_doc_index", lambda: store,
    )

    coll = MagicMock()
    coll.count.return_value = 500  # chroma 仍有大量块
    client = MagicMock()
    client.get_collection.return_value = coll
    monkeypatch.setattr(ingest, "get_chroma_client", lambda: client)

    backfilled: list[str] = []
    monkeypatch.setattr(
        ingest, "backfill_kb_doc_index",
        lambda model: backfilled.append(model) or 2,
    )

    docs, total = ingest.list_kb_documents_page(model="api-m3", page=1, page_size=20)
    assert backfilled == ["api-m3"]
    assert total == 2
    assert len(docs) == 2


def test_l1_count_repairs_incomplete_index_once(monkeypatch) -> None:
    """L1 检测到存量残缺索引时回填，之后使用修复后的轻量统计。"""
    ingest._KB_STATS_CACHE.clear()
    store = MagicMock()
    store.collection_stats.side_effect = [(1, 18), (5, 14123)]
    monkeypatch.setattr(
        "src.stores.kb_doc_index.get_kb_doc_index", lambda: store,
    )

    coll = MagicMock()
    coll.count.return_value = 14123
    client = MagicMock()
    client.get_collection.return_value = coll
    monkeypatch.setattr(ingest, "get_chroma_client", lambda: client)

    backfilled: list[str] = []
    monkeypatch.setattr(
        ingest, "backfill_kb_doc_index",
        lambda model: backfilled.append(model) or 5,
    )

    assert ingest.count_kb_documents("api-m3", use_cache=False) == (5, 14123)
    assert backfilled == ["api-m3"]


def test_l1_count_uses_healthy_index_without_backfill(monkeypatch) -> None:
    """正常入库后的数量一致，不触发全库回填。"""
    ingest._KB_STATS_CACHE.clear()
    store = MagicMock()
    store.collection_stats.return_value = (6, 14124)
    monkeypatch.setattr(
        "src.stores.kb_doc_index.get_kb_doc_index", lambda: store,
    )

    coll = MagicMock()
    coll.count.return_value = 14124
    client = MagicMock()
    client.get_collection.return_value = coll
    monkeypatch.setattr(ingest, "get_chroma_client", lambda: client)

    backfill = MagicMock()
    monkeypatch.setattr(ingest, "backfill_kb_doc_index", backfill)

    assert ingest.count_kb_documents("api-m3", use_cache=False) == (6, 14124)
    backfill.assert_not_called()
