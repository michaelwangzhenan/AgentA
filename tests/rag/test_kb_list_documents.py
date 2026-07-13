"""list_kb_documents 聚合逻辑 UT。"""

from __future__ import annotations

from src.rag.ingest import _char_count_from_metadata


def test_char_count_from_metadata() -> None:
    assert _char_count_from_metadata({"char_count": 120}) == 120
    assert _char_count_from_metadata({"char_count": "99"}) == 99
    assert _char_count_from_metadata({}) == 0
    assert _char_count_from_metadata({"char_count": "bad"}) == 0
