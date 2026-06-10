"""GoldenStore CRUD / 审核状态 / 评估取数 / 导入 幂等 UT（iter_14）。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.memory.golden_store import (
    SOURCE_AI,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    GoldenStore,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[GoldenStore]:
    s = GoldenStore(str(tmp_path / "golden.db"))
    yield s
    s.close()


def test_create_and_get(store: GoldenStore) -> None:
    gid = store.create("怎么用 RAG?", ["RAG", "检索"], "readme.md", "笔记")
    item = store.get(gid)
    assert item is not None
    assert item["query"] == "怎么用 RAG?"
    assert item["expected_keywords"] == ["RAG", "检索"]
    assert item["expected_source_contains"] == "readme.md"
    assert item["status"] == STATUS_APPROVED  # 默认手工录入即通过
    assert item["source"] == "manual"


def test_create_empty_query_raises(store: GoldenStore) -> None:
    with pytest.raises(ValueError):
        store.create("   ")


def test_keywords_accept_comma_string(store: GoldenStore) -> None:
    gid = store.create("q", "a, b ,c", "")
    assert store.get(gid)["expected_keywords"] == ["a", "b", "c"]


def test_update_and_set_status(store: GoldenStore) -> None:
    gid = store.create("q", ["a"], status=STATUS_PENDING)
    assert store.update(gid, query="q2", expected_keywords=["x"]) is True
    item = store.get(gid)
    assert item["query"] == "q2"
    assert item["expected_keywords"] == ["x"]
    assert store.set_status(gid, STATUS_APPROVED) is True
    assert store.get(gid)["status"] == STATUS_APPROVED


def test_update_invalid_status_raises(store: GoldenStore) -> None:
    gid = store.create("q")
    with pytest.raises(ValueError):
        store.update(gid, status="bogus")


def test_update_missing_returns_false(store: GoldenStore) -> None:
    assert store.update(99999, query="x") is False


def test_delete(store: GoldenStore) -> None:
    gid = store.create("q")
    assert store.delete(gid) is True
    assert store.get(gid) is None
    assert store.delete(gid) is False


def test_list_filter_and_counts(store: GoldenStore) -> None:
    store.create("a", status=STATUS_APPROVED)
    store.create("b", status=STATUS_PENDING, source=SOURCE_AI)
    store.create("c", status=STATUS_REJECTED)
    counts = store.counts()
    assert counts["approved"] == 1
    assert counts["pending"] == 1
    assert counts["rejected"] == 1
    assert counts["total"] == 3
    rows, total = store.list(status=STATUS_PENDING)
    assert total == 1 and rows[0]["query"] == "b"
    rows, total = store.list(source=SOURCE_AI)
    assert total == 1 and rows[0]["source"] == SOURCE_AI


def test_list_for_eval_only_approved_by_default(store: GoldenStore) -> None:
    store.create("approved-q", ["k"], status=STATUS_APPROVED)
    store.create("pending-q", ["k"], status=STATUS_PENDING)
    store.create("rejected-q", ["k"], status=STATUS_REJECTED)
    items = store.list_for_eval(use_pending=False)
    assert [i["query"] for i in items] == ["approved-q"]
    items_p = store.list_for_eval(use_pending=True)
    qs = {i["query"] for i in items_p}
    assert qs == {"approved-q", "pending-q"}  # rejected 永不纳入


def test_list_for_eval_field_shape(store: GoldenStore) -> None:
    store.create("q", ["a", "b"], "src.md", "note")
    item = store.list_for_eval()[0]
    assert item == {
        "query": "q",
        "expected_keywords": ["a", "b"],
        "expected_source_contains": "src.md",
        "note": "note",
    }


def test_import_items_idempotent(store: GoldenStore) -> None:
    items = [
        {"query": "q1", "expected_keywords": ["a"]},
        {"query": "q2", "expected_source_contains": "x.md"},
        {"query": ""},  # 空跳过
    ]
    assert store.import_items(items) == 2
    # 再次导入同集合：query 去重，新增 0
    assert store.import_items(items) == 0
    assert store.counts()["total"] == 2
