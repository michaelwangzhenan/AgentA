"""降本节省记录 UT（iter_14）：record_saving / 汇总 / 趋势 / 删号级联。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.memory.usage_store import UsageStore


@pytest.fixture
def store(tmp_path: Path):
    s = UsageStore(str(tmp_path / "usage.db"))
    yield s
    s.close()


def test_record_and_aggregate_savings(store: UsageStore) -> None:
    store.record_saving(1, "route", "exp", "cheap", 0.5, 100)
    store.record_saving(1, "route", "exp", "mid", 0.3, 80)
    store.record_saving(1, "cache", "cheap", "cache", 0.2, 50)
    now = int(time.time())
    agg = store.aggregate_savings(now - 3600, now + 3600, user_id=1)
    assert agg["route_count"] == 2
    assert agg["route_saved"] == pytest.approx(0.8)
    assert agg["cache_count"] == 1
    assert agg["cache_saved"] == pytest.approx(0.2)


def test_aggregate_filters_by_user(store: UsageStore) -> None:
    store.record_saving(1, "route", "exp", "cheap", 1.0, 10)
    store.record_saving(2, "route", "exp", "cheap", 2.0, 10)
    now = int(time.time())
    assert store.aggregate_savings(now - 3600, now + 3600, user_id=1)["route_saved"] == pytest.approx(1.0)
    assert store.aggregate_savings(now - 3600, now + 3600, user_id=None)["route_saved"] == pytest.approx(3.0)


def test_savings_series_groups_by_day_kind(store: UsageStore) -> None:
    store.record_saving(1, "route", "exp", "cheap", 0.5, 10)
    store.record_saving(1, "cache", "cheap", "cache", 0.2, 10)
    now = int(time.time())
    rows = store.savings_series(now - 3600, now + 3600, user_id=1)
    kinds = {r["kind"] for r in rows}
    assert kinds == {"route", "cache"}


def test_delete_all_for_user_cascades_savings(store: UsageStore) -> None:
    store.record_saving(1, "route", "exp", "cheap", 0.5, 10)
    store.delete_all_for_user(1)
    now = int(time.time())
    assert store.aggregate_savings(now - 3600, now + 3600, user_id=1)["route_count"] == 0
