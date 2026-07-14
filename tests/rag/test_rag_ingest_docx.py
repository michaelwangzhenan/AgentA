from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import src.config as config
import src.rag.ingest as ingest


class _FakeCollection:
    def __init__(self) -> None:
        self.batches: list[tuple[list[str], list[str], list[dict[str, Any]]]] = []

    def get(self, **_: Any) -> dict[str, list[Any]]:
        return {"ids": [], "metadatas": []}

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.batches.append((ids, documents, metadatas))


def test_docx_ingest_writes_embedding_sized_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"docx")
    parsed = tmp_path / "parsed.txt"
    parsed.write_text(
        "\n\n".join(f"# Heading {i}\n\nParagraph {i}" for i in range(40)),
        encoding="utf-8",
    )

    @contextmanager
    def fake_parsed_docx_temp(_: Path):
        yield parsed

    monkeypatch.setattr(ingest, "parsed_docx_temp", fake_parsed_docx_temp)
    monkeypatch.setattr(config, "BM25_ENABLED", False)
    monkeypatch.setattr(config, "CHUNK_SIZE", 100)
    monkeypatch.setattr(config, "CHUNK_OVERLAP", 10)
    collection = _FakeCollection()

    result = ingest._ingest_docx_file(
        source,
        "source.docx",
        "doc-id",
        collection,
        "kb_test",
        None,
    )

    assert result == {"doc_id": "doc-id", "chunks": 40, "status": "ingested"}
    assert max(len(documents) for _, documents, _ in collection.batches) <= 16
    metadata = [md for _, _, batch in collection.batches for md in batch]
    assert [md["chunk_index"] for md in metadata] == list(range(40))
    assert {md["chunk_total"] for md in metadata} == {40}
    assert all(md["heading_path"].startswith("Heading") for md in metadata)
