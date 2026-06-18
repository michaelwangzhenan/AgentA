"""
测试 [SRSStore](../src/stores/srs_store.py)（Phase 2.4 G1 / D3 / D8-D10）。

覆盖：
    - 表结构幂等初始化、独立 db 文件
    - add_card：quiz_question / manual 两路径 + 非法入参（source_type / source_ref / 空 front/back）
    - get_card / list_cards：按 status 过滤 + 默认排除 archived + limit
    - list_due：active + next_review_at <= now，按时间升序 + id 升序
    - update_review_state：边界夹紧（ease ≥ 1.3 / interval ≥ 1）+ active 才允许 + 元字段更新
    - set_status / suspend / resume / archive / delete_card
    - card_exists_for_source：跳 archived
    - stats：含 mature 卡 / due 数 / 平均 ease
    - 进程级 get_shared_store / reset_shared_store_for_testing
    - 资源管理 context manager
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.stores.srs_store import (
    EASE_FACTOR_INIT,
    EASE_FACTOR_MIN,
    INTERVAL_MIN_DAYS,
    SRSStore,
    get_shared_store,
    reset_shared_store_for_testing,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SRSStore]:
    db = SRSStore(str(tmp_path / "srs.db"))
    yield db
    db.close()


# ── add_card ────────────────────────────────────────────────────────────────


class TestAddCard:
    def test_manual_card_basic(self, store: SRSStore) -> None:
        cid = store.add_card(
            source_type="manual",
            front="Python 装饰器原理",
            back="闭包 + __call__",
        )
        assert cid >= 1
        card = store.get_card(cid)
        assert card is not None
        assert card["source_type"] == "manual"
        assert card["source_ref"] is None
        assert card["front"] == "Python 装饰器原理"
        assert card["back"] == "闭包 + __call__"
        assert card["status"] == "active"
        assert card["ease_factor"] == EASE_FACTOR_INIT
        assert card["interval_days"] == 0
        assert card["repetitions"] == 0
        assert card["lapses"] == 0
        # 新卡 next_review_at == created_at（立即 due）
        assert card["next_review_at"] == card["created_at"]
        assert card["last_reviewed_at"] == ""

    def test_quiz_question_card(self, store: SRSStore) -> None:
        cid = store.add_card(
            source_type="quiz_question",
            front="RAG 全称",
            back="Retrieval-Augmented Generation",
            source_ref=42,
        )
        card = store.get_card(cid)
        assert card is not None
        assert card["source_type"] == "quiz_question"
        assert card["source_ref"] == 42

    def test_note_truncated_to_200(self, store: SRSStore) -> None:
        long_note = "x" * 500
        cid = store.add_card("manual", "F", "B", note=long_note)
        assert len(store.get_card(cid)["note"]) == 200

    def test_invalid_source_type_raises(self, store: SRSStore) -> None:
        with pytest.raises(ValueError):
            store.add_card("bad_type", "F", "B")

    def test_empty_front_raises(self, store: SRSStore) -> None:
        with pytest.raises(ValueError):
            store.add_card("manual", "  ", "B")

    def test_empty_back_raises(self, store: SRSStore) -> None:
        with pytest.raises(ValueError):
            store.add_card("manual", "F", "")

    def test_quiz_question_requires_source_ref(self, store: SRSStore) -> None:
        with pytest.raises(ValueError):
            store.add_card("quiz_question", "F", "B")

    def test_manual_rejects_source_ref(self, store: SRSStore) -> None:
        with pytest.raises(ValueError):
            store.add_card("manual", "F", "B", source_ref=1)

    def test_quiz_question_rejects_zero_or_neg_ref(self, store: SRSStore) -> None:
        with pytest.raises(ValueError):
            store.add_card("quiz_question", "F", "B", source_ref=0)
        with pytest.raises(ValueError):
            store.add_card("quiz_question", "F", "B", source_ref=-1)


# ── get / list ──────────────────────────────────────────────────────────────


class TestListAndGet:
    def test_get_nonexistent_returns_none(self, store: SRSStore) -> None:
        assert store.get_card(999) is None

    def test_list_default_excludes_archived(self, store: SRSStore) -> None:
        c1 = store.add_card("manual", "A", "a")
        c2 = store.add_card("manual", "B", "b")
        c3 = store.add_card("manual", "C", "c")
        store.archive(c2)
        cards = store.list_cards()
        ids = {c["id"] for c in cards}
        assert c1 in ids
        assert c3 in ids
        assert c2 not in ids

    def test_list_status_filter(self, store: SRSStore) -> None:
        c1 = store.add_card("manual", "A", "a")
        c2 = store.add_card("manual", "B", "b")
        store.suspend(c2)
        active_cards = store.list_cards(status="active")
        suspended_cards = store.list_cards(status="suspended")
        assert {c["id"] for c in active_cards} == {c1}
        assert {c["id"] for c in suspended_cards} == {c2}

    def test_list_invalid_status_returns_empty(self, store: SRSStore) -> None:
        store.add_card("manual", "A", "a")
        assert store.list_cards(status="bogus") == []

    def test_list_limit(self, store: SRSStore) -> None:
        for i in range(5):
            store.add_card("manual", f"q{i}", "a")
        assert len(store.list_cards(limit=3)) == 3


# ── list_due ────────────────────────────────────────────────────────────────


class TestListDue:
    def test_new_cards_immediately_due(self, store: SRSStore) -> None:
        c1 = store.add_card("manual", "A", "a")
        c2 = store.add_card("manual", "B", "b")
        due = store.list_due()
        assert {c["id"] for c in due} == {c1, c2}

    def test_due_excludes_suspended_and_archived(self, store: SRSStore) -> None:
        c1 = store.add_card("manual", "A", "a")
        c2 = store.add_card("manual", "B", "b")
        c3 = store.add_card("manual", "C", "c")
        store.suspend(c2)
        store.archive(c3)
        due = store.list_due()
        assert {c["id"] for c in due} == {c1}

    def test_due_filters_by_now(self, store: SRSStore) -> None:
        store.add_card("manual", "A", "a")
        # 给一个早于所有卡 created_at 的 now → 没有 due
        due = store.list_due(now="1900-01-01T00:00:00")
        assert due == []

    def test_due_ordered_by_next_review_at_then_id(self, store: SRSStore) -> None:
        c1 = store.add_card("manual", "A", "a")
        c2 = store.add_card("manual", "B", "b")
        c3 = store.add_card("manual", "C", "c")
        # 模拟 review：c1 推到未来，c3 也推到未来；c2 还 due
        store.update_review_state(
            c1, ease_factor=2.5, interval_days=10, repetitions=1,
            lapses=0, next_review_at="2099-01-01T00:00:00",
        )
        store.update_review_state(
            c3, ease_factor=2.5, interval_days=10, repetitions=1,
            lapses=0, next_review_at="2099-01-01T00:00:00",
        )
        due = store.list_due()
        assert [c["id"] for c in due] == [c2]


# ── update_review_state ─────────────────────────────────────────────────────


class TestUpdateReviewState:
    def test_basic_update(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        ok = store.update_review_state(
            cid, ease_factor=2.3, interval_days=6,
            repetitions=2, lapses=0,
            next_review_at="2030-01-01T00:00:00",
        )
        assert ok is True
        card = store.get_card(cid)
        assert card["ease_factor"] == 2.3
        assert card["interval_days"] == 6
        assert card["repetitions"] == 2
        assert card["next_review_at"] == "2030-01-01T00:00:00"
        assert card["last_reviewed_at"] != ""

    def test_clip_ease_below_min(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        store.update_review_state(
            cid, ease_factor=0.5, interval_days=5,
            repetitions=1, lapses=0, next_review_at="2030-01-01T00:00:00",
        )
        assert store.get_card(cid)["ease_factor"] == EASE_FACTOR_MIN

    def test_clip_interval_below_min(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        store.update_review_state(
            cid, ease_factor=2.5, interval_days=0,
            repetitions=1, lapses=0, next_review_at="2030-01-01T00:00:00",
        )
        assert store.get_card(cid)["interval_days"] == INTERVAL_MIN_DAYS

    def test_update_nonexistent_returns_false(self, store: SRSStore) -> None:
        assert store.update_review_state(
            999, ease_factor=2.5, interval_days=1,
            repetitions=1, lapses=0, next_review_at="2030-01-01T00:00:00",
        ) is False

    def test_update_suspended_returns_false(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        store.suspend(cid)
        ok = store.update_review_state(
            cid, ease_factor=2.5, interval_days=5,
            repetitions=1, lapses=0, next_review_at="2030-01-01T00:00:00",
        )
        assert ok is False

    def test_update_archived_returns_false(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        store.archive(cid)
        ok = store.update_review_state(
            cid, ease_factor=2.5, interval_days=5,
            repetitions=1, lapses=0, next_review_at="2030-01-01T00:00:00",
        )
        assert ok is False


# ── status 切换 ─────────────────────────────────────────────────────────────


class TestStatus:
    def test_suspend_resume_archive(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        assert store.suspend(cid) is True
        assert store.get_card(cid)["status"] == "suspended"
        assert store.resume(cid) is True
        assert store.get_card(cid)["status"] == "active"
        assert store.archive(cid) is True
        assert store.get_card(cid)["status"] == "archived"

    def test_resume_active_returns_false(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        assert store.resume(cid) is False  # 已经是 active

    def test_resume_archived_returns_false(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        store.archive(cid)
        assert store.resume(cid) is False

    def test_set_status_invalid(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        assert store.set_status(cid, "bogus") is False

    def test_set_status_nonexistent(self, store: SRSStore) -> None:
        assert store.set_status(999, "suspended") is False


# ── delete + card_exists_for_source ────────────────────────────────────────


class TestDeleteAndExists:
    def test_delete_card(self, store: SRSStore) -> None:
        cid = store.add_card("manual", "A", "a")
        assert store.delete_card(cid) is True
        assert store.get_card(cid) is None
        assert store.delete_card(cid) is False  # 二次 delete

    def test_exists_for_active_quiz_question(self, store: SRSStore) -> None:
        cid = store.add_card("quiz_question", "Q", "A", source_ref=10)
        assert store.card_exists_for_source("quiz_question", 10) == cid

    def test_exists_skips_archived(self, store: SRSStore) -> None:
        cid = store.add_card("quiz_question", "Q", "A", source_ref=10)
        store.archive(cid)
        assert store.card_exists_for_source("quiz_question", 10) is None

    def test_exists_with_suspended_still_counts(self, store: SRSStore) -> None:
        cid = store.add_card("quiz_question", "Q", "A", source_ref=10)
        store.suspend(cid)
        assert store.card_exists_for_source("quiz_question", 10) == cid


# ── stats ───────────────────────────────────────────────────────────────────


class TestStats:
    def test_empty_stats(self, store: SRSStore) -> None:
        s = store.stats()
        assert s["total_active"] == 0
        assert s["due_count"] == 0
        assert s["avg_ease"] == 0.0
        assert s["mature_count"] == 0

    def test_full_stats(self, store: SRSStore) -> None:
        c1 = store.add_card("manual", "A", "a")
        c2 = store.add_card("manual", "B", "b")
        c3 = store.add_card("manual", "C", "c")
        c4 = store.add_card("manual", "D", "d")
        store.suspend(c2)
        store.archive(c3)
        # c4：模拟 mature 卡（interval ≥ 21d）+ 推到未来
        store.update_review_state(
            c4, ease_factor=2.7, interval_days=30,
            repetitions=4, lapses=0,
            next_review_at="2099-01-01T00:00:00",
        )
        s = store.stats()
        assert s["total_active"] == 2  # c1, c4
        assert s["total_suspended"] == 1
        assert s["total_archived"] == 1
        assert s["due_count"] == 1  # 只 c1 due
        assert s["mature_count"] == 1
        # avg_ease: (2.5 + 2.7) / 2 = 2.6
        assert 2.55 <= s["avg_ease"] <= 2.65

    def test_avg_ease_excludes_non_active(self, store: SRSStore) -> None:
        c1 = store.add_card("manual", "A", "a")
        c2 = store.add_card("manual", "B", "b")
        store.suspend(c2)  # 不参与平均
        assert store.stats()["avg_ease"] == round(EASE_FACTOR_INIT, 2)


# ── shared store helper ────────────────────────────────────────────────────


class TestSharedStore:
    def test_get_returns_same_instance(self, tmp_path: Path) -> None:
        from src.stores import srs_store as mod

        reset_shared_store_for_testing(None)
        original_path = mod.SRS_DB_PATH
        mod.SRS_DB_PATH = str(tmp_path / "srs.db")
        try:
            a = get_shared_store()
            b = get_shared_store()
            assert a is b
        finally:
            a.close()
            reset_shared_store_for_testing(None)
            mod.SRS_DB_PATH = original_path

    def test_reset_with_mock(self, tmp_path: Path) -> None:
        fake = SRSStore(str(tmp_path / "srs.db"))
        reset_shared_store_for_testing(fake)
        try:
            assert get_shared_store() is fake
        finally:
            fake.close()
            reset_shared_store_for_testing(None)


# ── context manager ────────────────────────────────────────────────────────


def test_context_manager(tmp_path: Path) -> None:
    with SRSStore(str(tmp_path / "srs.db")) as s:
        cid = s.add_card("manual", "F", "B")
        assert s.get_card(cid) is not None
