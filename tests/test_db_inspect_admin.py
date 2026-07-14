"""管理巡检降峰：Chroma 分批扫描、BM25 manifest 列表（不测真实 Chroma/BM25 落盘）。"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pytest

import src.config as config
import src.services.db_inspect as inspect
from src.rag.bm25_index import BM25Index, save_index


class _FakeChromaCol:
    def __init__(self, total: int) -> None:
        self._total = total
        self.get_calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> dict:
        self.get_calls.append(dict(kwargs))
        offset = int(kwargs.get("offset") or 0)
        limit = int(kwargs.get("limit") or 0)
        include = kwargs.get("include") or []
        end = min(offset + limit, self._total)
        ids = [f"id-{i}" for i in range(offset, end)]
        out: dict[str, Any] = {"ids": ids}
        if "documents" in include:
            out["documents"] = [f"document body {i} " * 30 for i in range(offset, end)]
        if "metadatas" in include:
            out["metadatas"] = [{"filename": f"f{i}.txt", "ingested_at": i} for i in range(offset, end)]
        return out


def test_chroma_scan_rows_batches_and_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CHROMA_SCAN_CAP", 1200)
    monkeypatch.setattr(config, "CHROMA_SCAN_BATCH", 500)
    monkeypatch.setattr(config, "CHROMA_LIST_PREVIEW_MAX", 200)
    inspect.CHROMA_SCAN_CAP = 1200
    inspect.CHROMA_SCAN_BATCH = 500
    inspect.CHROMA_LIST_PREVIEW_MAX = 200

    col = _FakeChromaCol(1200)
    rows, truncated = inspect._chroma_scan_rows(col, where=None, where_document=None)

    assert len(rows) == 1200
    assert truncated is False
    assert len(col.get_calls) >= 3
    assert all(call["limit"] <= 500 for call in col.get_calls if call.get("include"))
    assert all(len(r["preview"]) <= 200 for r in rows)
    assert all("document" not in r for r in rows)


def test_chroma_items_list_preview_max_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CHROMA_LIST_PREVIEW_MAX", 200)
    inspect.CHROMA_LIST_PREVIEW_MAX = 200

    long_doc = "x" * 500
    got = {"ids": ["a"], "documents": [long_doc], "metadatas": [{"filename": "t.txt"}]}
    row = inspect._rows_from_get(got, list_view=True)[0]
    view = inspect._chroma_item_view(row)
    assert len(view["preview"]) == 200
    assert view["preview"].endswith("...")
    assert view["doc_len"] == 500


def _write_bm25_sidecars(
    tmp_path: Path, collection: str, n: int, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    pkl = tmp_path / f"bm25_{collection}.pkl"
    monkeypatch.setattr(
        "src.rag.bm25_index.get_index_path",
        lambda coll: tmp_path / f"bm25_{coll}.pkl",
    )
    idx = BM25Index(collection)
    ids = [f"c{i}" for i in range(n)]
    docs = [f"text {i}" for i in range(n)]
    metas = [
        {"doc_id": f"d{i}", "filename": f"doc-{i:05d}.md", "ingested_at": float(i)}
        for i in range(n)
    ]
    idx.upsert(ids, docs, metas)
    save_index(idx, pkl)
    return pkl


def test_bm25_indexes_reads_manifest_without_pickle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_bm25_sidecars(tmp_path, "kb_test", 3, monkeypatch)
    monkeypatch.setattr(inspect, "bm25_dir", lambda: tmp_path)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("pickle.load should not be called for L1 list")

    monkeypatch.setattr(pickle, "load", _boom)

    data = inspect.bm25_indexes()
    assert len(data["indexes"]) == 1
    row = data["indexes"][0]
    assert row["docs"] == 3
    assert row.get("error") is None


def test_bm25_docs_20k_filter_without_pickle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = 20000
    _write_bm25_sidecars(tmp_path, "kb_big", n, monkeypatch)
    monkeypatch.setattr(inspect, "bm25_dir", lambda: tmp_path)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("pickle.load should not be called for L2 list")

    monkeypatch.setattr(pickle, "load", _boom)

    page = inspect.bm25_docs("kb_big", limit=50, offset=0, filename_q="doc-00199.md")
    assert page is not None
    assert page["total"] == 1
    assert page["items"][0]["id"] == "c199"


def test_save_index_writes_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.rag.bm25_index.get_index_path",
        lambda coll: tmp_path / f"bm25_{coll}.pkl",
    )
    idx = BM25Index("kb_test")
    idx.upsert(["a"], ["hello"], [{"doc_id": "d0", "filename": "a.md"}])
    pkl = tmp_path / "bm25_kb_test.pkl"
    save_index(idx, pkl)

    manifest = json.loads((tmp_path / "bm25_kb_test.manifest.json").read_text(encoding="utf-8"))
    assert manifest["docs"] == 1
    assert manifest["collection"] == "kb_test"
    assert (tmp_path / "bm25_kb_test.chunks.jsonl").is_file()
