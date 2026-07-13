"""list_kb_documents 聚合逻辑 UT。"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.rag.ingest import (
    _char_count_from_metadata,
    _iter_chunk_metadatas,
    _merge_doc_row,
)


def test_char_count_from_metadata() -> None:
    assert _char_count_from_metadata({"char_count": 120}) == 120
    assert _char_count_from_metadata({"char_count": "99"}) == 99
    assert _char_count_from_metadata({}) == 0
    assert _char_count_from_metadata({"char_count": "bad"}) == 0


def test_merge_doc_row_aggregates_chunks() -> None:
    grouped: dict[str, dict] = {}
    _merge_doc_row(grouped, {
        "doc_id": "d1",
        "source": "a.md",
        "filename": "a.md",
        "char_count": 10,
        "ingested_at": 100.0,
    })
    _merge_doc_row(grouped, {
        "doc_id": "d1",
        "char_count": 5,
        "ingested_at": 200.0,
    })
    assert grouped["d1"]["chunks"] == 2
    assert grouped["d1"]["total_chars"] == 15
    assert grouped["d1"]["ingested_at"] == 200.0


def test_iter_chunk_metadatas_batches() -> None:
    col = MagicMock()
    col.count.return_value = 3
    col.get.side_effect = [
        {"metadatas": [{"doc_id": "a"}, {"doc_id": "b"}]},
        {"metadatas": [{"doc_id": "c"}]},
    ]
    out = list(_iter_chunk_metadatas(col))
    assert len(out) == 3
    assert col.get.call_count == 2
    col.get.assert_any_call(limit=256, offset=0, include=["metadatas"])
    col.get.assert_any_call(limit=256, offset=2, include=["metadatas"])
