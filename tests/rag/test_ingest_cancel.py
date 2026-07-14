"""入库取消：协作式中止 + 半成品回滚。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.rag.ingest import (
    IngestCancelled,
    _ingest_one_file,
    _raise_if_cancelled,
    _rollback_doc_chunks,
)
from src.rag.splitter import Chunk


def test_raise_if_cancelled() -> None:
    with pytest.raises(IngestCancelled):
        _raise_if_cancelled(lambda: True)
    _raise_if_cancelled(lambda: False)
    _raise_if_cancelled(None)


def test_rollback_doc_chunks_deletes_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = MagicMock()
    collection.get.return_value = {"ids": ["c1", "c2"]}
    monkeypatch.setattr("src.config.BM25_ENABLED", False)
    _rollback_doc_chunks(collection, "kb_en", "doc123", bm25_save=False)
    collection.delete.assert_called_once_with(ids=["c1", "c2"])


def test_ingest_one_file_cancel_during_embed_rolls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """嵌入批间检测到取消 → 抛 IngestCancelled 并清掉已写 chunks。"""
    fp = tmp_path / "doc.md"
    fp.write_text("# hello\n\nworld", encoding="utf-8")
    docs_path = tmp_path

    chunks = [
        Chunk(text=f"chunk {i}", line_start=i, line_end=i + 1)
        for i in range(32)
    ]
    monkeypatch.setattr("src.rag.ingest.parse_file", lambda _p: "x" * 100)
    monkeypatch.setattr("src.rag.ingest.split_structured", lambda *_a, **_k: chunks)

    collection = MagicMock()
    written_ids: list[str] = []
    upsert_calls = 0
    cancelled = False

    def fake_get(where=None, include=None) -> dict:
        return {"ids": list(written_ids), "metadatas": []}

    def fake_upsert(ids=None, **_kwargs) -> None:
        nonlocal upsert_calls, cancelled
        written_ids.extend(ids or [])
        upsert_calls += 1
        if upsert_calls >= 1:
            cancelled = True

    collection.get.side_effect = fake_get
    collection.upsert.side_effect = fake_upsert

    with pytest.raises(IngestCancelled):
        _ingest_one_file(
            fp,
            docs_path,
            collection,
            "kb_en",
            cancel_cb=lambda: cancelled,
            bm25_save=False,
        )

    assert upsert_calls >= 1
    collection.delete.assert_called()
