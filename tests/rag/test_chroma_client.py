"""Chroma PersistentClient 单例 UT。"""

from __future__ import annotations

import chromadb
import pytest

from src.rag.chroma_client import (
    chroma_db_path,
    close_chroma_client,
    get_chroma_client,
)


@pytest.fixture(autouse=True)
def _reset_chroma_client() -> None:
    close_chroma_client()
    yield
    close_chroma_client()


def test_get_chroma_client_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []

    class FakeClient:
        pass

    def fake_persistent(path: str) -> FakeClient:
        created.append(path)
        return FakeClient()

    monkeypatch.setattr(chromadb, "PersistentClient", fake_persistent)
    a = get_chroma_client()
    b = get_chroma_client()
    assert a is b
    assert len(created) == 1
    assert created[0] == chroma_db_path()


def test_close_chroma_client_recreates(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []

    class FakeClient:
        pass

    monkeypatch.setattr(
        chromadb,
        "PersistentClient",
        lambda path: created.append(path) or FakeClient(),
    )
    get_chroma_client()
    close_chroma_client()
    get_chroma_client()
    assert len(created) == 2
