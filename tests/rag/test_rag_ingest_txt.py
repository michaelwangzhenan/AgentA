from pathlib import Path
from typing import Any

import pytest

import src.config as config
import src.rag.ingest as ingest
from src.services.ingest_telemetry import IngestProbe


class _FakeCollection:
    def __init__(self) -> None:
        self.batches: list[tuple[list[str], list[str], list[dict[str, Any]]]] = []
        self._deleted: list[str] = []

    def get(self, **_: Any) -> dict[str, list[Any]]:
        return {"ids": [], "metadatas": []}

    def delete(self, ids: list[str] | None = None, **_: Any) -> None:
        if ids:
            self._deleted.extend(ids)

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.batches.append((ids, documents, metadatas))


def _large_structured_text(sections: int) -> str:
    return "\n\n".join(f"# Section {i}\n\nBody paragraph {i}" for i in range(sections))


def test_txt_ingest_writes_embedding_sized_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "notes.txt"
    source.write_text(_large_structured_text(40), encoding="utf-8")

    monkeypatch.setattr(ingest, "parse_file", lambda _: source.read_text(encoding="utf-8"))
    monkeypatch.setattr(config, "BM25_ENABLED", False)
    monkeypatch.setattr(config, "CHUNK_SIZE", 100)
    monkeypatch.setattr(config, "CHUNK_OVERLAP", 10)
    monkeypatch.setattr(config, "INGEST_MAX_CHUNKS_PER_DOC", 5000)

    collection = _FakeCollection()
    probe = IngestProbe(file_path=source, rel_path="notes.txt")

    result = ingest._ingest_one_file(
        source,
        tmp_path,
        collection,
        "kb_test",
        None,
        probe,
    )

    assert result == {"doc_id": ingest._doc_id_from_relpath("notes.txt"), "chunks": 40, "status": "ingested"}
    assert max(len(documents) for _, documents, _ in collection.batches) <= 16
    metadata = [md for _, _, batch in collection.batches for md in batch]
    assert [md["chunk_index"] for md in metadata] == list(range(40))
    assert {md["chunk_total"] for md in metadata} == {40}


def test_txt_ingest_truncates_at_max_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = tmp_path / "huge.txt"
    source.write_text(_large_structured_text(30), encoding="utf-8")

    monkeypatch.setattr(ingest, "parse_file", lambda _: source.read_text(encoding="utf-8"))
    monkeypatch.setattr(config, "BM25_ENABLED", False)
    monkeypatch.setattr(config, "CHUNK_SIZE", 100)
    monkeypatch.setattr(config, "CHUNK_OVERLAP", 10)
    monkeypatch.setattr(config, "INGEST_MAX_CHUNKS_PER_DOC", 5)

    collection = _FakeCollection()
    probe = IngestProbe(file_path=source, rel_path="huge.txt")

    with caplog.at_level("WARNING"):
        result = ingest._ingest_one_file(
            source,
            tmp_path,
            collection,
            "kb_test",
            None,
            probe,
        )

    assert result["status"] == "ingested"
    assert result["chunks"] == 5
    assert any("截断" in rec.message for rec in caplog.records)
