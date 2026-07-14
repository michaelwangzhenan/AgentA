"""入库取消：协作式中止 + 半成品回滚（保留重入库前旧块）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.rag.ingest import (
    IngestCancelled,
    _ingest_one_file,
    _make_chunk_id,
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


def test_rollback_only_ids_keeps_unlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = MagicMock()
    monkeypatch.setattr("src.config.BM25_ENABLED", False)
    _rollback_doc_chunks(
        collection, "kb_en", "doc123", bm25_save=False, only_ids=["new1"],
    )
    collection.delete.assert_called_once_with(ids=["new1"])
    collection.get.assert_not_called()


def test_ingest_one_file_cancel_during_embed_rolls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """嵌入批间检测到取消 → 抛 IngestCancelled 并清掉已写 chunks。"""
    fp = tmp_path / "doc.md"
    fp.write_text("# hello\n\nworld", encoding="utf-8")
    docs_path = tmp_path

    monkeypatch.setattr("src.rag.ingest.parse_file", lambda _p: "x" * 100)

    def fake_iter(_source, *_a, **_k):
        for i in range(32):
            yield Chunk(text=f"chunk {i}", line_start=i, line_end=i + 1)

    monkeypatch.setattr("src.rag.ingest.iter_structured_lines", fake_iter)
    monkeypatch.setattr("src.config.BM25_ENABLED", False)

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
    deleted_ids = collection.delete.call_args.kwargs.get("ids")
    assert deleted_ids
    assert set(deleted_ids) <= set(written_ids)


def test_reingest_cancel_preserves_old_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """已有文档重入库中途取消：旧块保留，只回滚新写入。"""
    fp = tmp_path / "doc.md"
    fp.write_text("# v2\n\nbody", encoding="utf-8")
    docs_path = tmp_path
    doc_id = "abc123deadbeef01"
    old_ids = [_make_chunk_id(doc_id, 0), _make_chunk_id(doc_id, 1)]

    monkeypatch.setattr("src.rag.ingest.parse_file", lambda _p: "y" * 100)

    def fake_iter(_source, *_a, **_k):
        for i in range(32):
            yield Chunk(text=f"new {i}", line_start=i, line_end=i + 1)

    monkeypatch.setattr("src.rag.ingest.iter_structured_lines", fake_iter)
    monkeypatch.setattr("src.rag.ingest._doc_id_from_relpath", lambda _p: doc_id)
    monkeypatch.setattr("src.config.BM25_ENABLED", False)

    collection = MagicMock()
    written_ids: list[str] = []
    upsert_calls = 0
    cancelled = False

    def fake_get(where=None, include=None) -> dict:
        if not written_ids:
            return {
                "ids": list(old_ids),
                "metadatas": [{"content_sha1": "oldhash"}] * len(old_ids),
            }
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

    deleted_ids = collection.delete.call_args.kwargs.get("ids")
    assert deleted_ids is not None
    assert set(deleted_ids).isdisjoint(set(old_ids))
    assert set(deleted_ids) <= set(written_ids)
    for call in collection.delete.call_args_list:
        ids = call.kwargs.get("ids") or []
        assert not set(ids) & set(old_ids)
