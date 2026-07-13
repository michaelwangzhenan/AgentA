"""GoldenStore CRUD / 审核状态 / 评估取数 / 导入 幂等 UT（iter_14）。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.stores.golden_store import (
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


def test_expected_source_and_type_roundtrip(store: GoldenStore) -> None:
    gid = store.create("q", ["k"], expected_source="resume.md", golden_type="baseline")
    item = store.get(gid)
    assert item["expected_source"] == "resume.md"
    assert item["type"] == "baseline"
    # 局部更新两个新字段
    assert store.update(gid, expected_source="cv.md", type="hyde") is True
    item2 = store.get(gid)
    assert item2["expected_source"] == "cv.md"
    assert item2["type"] == "hyde"


def test_list_for_eval_includes_new_fields(store: GoldenStore) -> None:
    store.create("q", ["a"], "src.md", expected_source="exact.md", golden_type="baseline")
    item = store.list_for_eval()[0]
    assert item["expected_source"] == "exact.md"
    assert item["type"] == "baseline"
    assert item["expected_source_contains"] == "src.md"


def test_import_items_maps_new_fields(store: GoldenStore) -> None:
    added = store.import_items([
        {"query": "q1", "expected_source": "a.md", "type": "hyde"},
    ])
    assert added == 1
    rows, _ = store.list()
    assert rows[0]["expected_source"] == "a.md"
    assert rows[0]["type"] == "hyde"


def test_doc_counts_and_delete_pending_by_doc(store: GoldenStore) -> None:
    store.create("a1", doc_id="d1", source=SOURCE_AI, status=STATUS_PENDING)
    store.create("a2", doc_id="d1", source=SOURCE_AI, status=STATUS_APPROVED)
    store.create("b1", doc_id="d2", source=SOURCE_AI, status=STATUS_PENDING)
    store.create("no-doc")  # 无 doc_id 不计入
    dc = store.doc_counts()
    assert dc["d1"] == {"total": 2, "pending": 1}
    assert dc["d2"] == {"total": 1, "pending": 1}
    assert "" not in dc
    # 重生成清旧 pending：d1 的 pending(1) 删掉，approved 保留
    removed = store.delete_pending_by_doc("d1")
    assert removed == 1
    dc2 = store.doc_counts()
    assert dc2["d1"] == {"total": 1, "pending": 0}


def test_delete_by_doc_removes_all_statuses(store: GoldenStore) -> None:
    store.create("p", doc_id="d1", status=STATUS_PENDING)
    store.create("a", doc_id="d1", status=STATUS_APPROVED)
    store.create("r", doc_id="d1", status=STATUS_REJECTED)
    store.create("other", doc_id="d2", status=STATUS_APPROVED)
    store.create("no-doc")
    assert store.delete_by_doc("d1") == 3
    assert store.counts()["total"] == 2
    rows, _ = store.list(doc_id="d1")
    assert rows == []
    assert store.delete_by_doc("") == 0
    assert store.delete_by_doc("missing") == 0


def test_list_doc_id_filter_and_export_all(store: GoldenStore) -> None:
    store.create("q-a", doc_id="da")
    store.create("q-b", doc_id="db")
    rows, total = store.list(doc_id="da")
    assert total == 1 and rows[0]["query"] == "q-a"
    exported = store.export_all()
    assert len(exported) == 2
    assert {r["query"] for r in exported} == {"q-a", "q-b"}


def test_migration_adds_columns_to_old_db(tmp_path: Path) -> None:
    """旧库（没有 expected_source / type 列）打开后应被 _migrate 自动补列。"""
    import sqlite3

    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE rag_golden (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            expected_keywords TEXT NOT NULL DEFAULT '[]',
            expected_source_contains TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'approved',
            doc_id TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        INSERT INTO rag_golden(query, created_at, updated_at) VALUES ('old-q', 1, 1);
        """
    )
    conn.commit()
    conn.close()

    s = GoldenStore(db)
    try:
        item = s.get(1)
        assert item is not None
        assert item["query"] == "old-q"
        assert item["expected_source"] == ""  # 补列默认空，旧数据不丢
        assert item["type"] == ""
    finally:
        s.close()
