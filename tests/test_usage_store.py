"""UsageStore + 单价合并 / 成本计算 / record_usage 旁路入口 UT（iter_11）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import NamedTuple

import pytest

import src.config as config
from src.memory.usage_store import (
    UsageStore,
    cost_of,
    merged_pricing,
    record_usage,
    reset_shared_store_for_testing,
)


class _Usage(NamedTuple):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@pytest.fixture
def store(tmp_path: Path):
    s = UsageStore(str(tmp_path / "usage.db"))
    yield s
    s.close()


def _seed(s: UsageStore, **kw) -> None:
    base = dict(user_id=1, model_id="kimi-k2.5", thinking=False,
                prompt_tokens=100, completion_tokens=50)
    base.update(kw)
    s.record(**base)


# ── 写入 + 聚合 ───────────────────────────────────────────────────────────────


def test_record_and_aggregate_by_model(store: UsageStore) -> None:
    _seed(store, model_id="kimi-k2.5", prompt_tokens=100, completion_tokens=50)
    _seed(store, model_id="kimi-k2.5", prompt_tokens=200, completion_tokens=80)
    _seed(store, model_id="gpt-4o", prompt_tokens=10, completion_tokens=5)
    now = int(time.time())
    rows = {r["model_id"]: r for r in store.aggregate_by_model(now - 3600, now + 3600)}
    assert rows["kimi-k2.5"]["prompt_tokens"] == 300
    assert rows["kimi-k2.5"]["completion_tokens"] == 130
    assert rows["kimi-k2.5"]["total_tokens"] == 430
    assert rows["kimi-k2.5"]["count"] == 2
    assert rows["gpt-4o"]["count"] == 1


def test_total_tokens_defaults_to_sum(store: UsageStore) -> None:
    store.record(user_id=1, model_id="gpt-4o", thinking=False,
                 prompt_tokens=7, completion_tokens=3)
    now = int(time.time())
    rows = store.aggregate_by_model(now - 60, now + 60)
    assert rows[0]["total_tokens"] == 10


def test_range_filter_excludes_outside(store: UsageStore) -> None:
    old = int(time.time()) - 10 * 86400
    _seed(store, created_at=old)
    _seed(store)  # now
    now = int(time.time())
    # 仅最近 1 天
    rows = store.aggregate_by_model(now - 86400, now + 60)
    assert sum(r["count"] for r in rows) == 1


def test_aggregate_series_groups_by_day_user_model(store: UsageStore) -> None:
    _seed(store, user_id=1, model_id="kimi-k2.5")
    _seed(store, user_id=2, model_id="gpt-4o")
    now = int(time.time())
    rows = store.aggregate_series(now - 3600, now + 3600)
    keys = {(r["user_id"], r["model_id"]) for r in rows}
    assert (1, "kimi-k2.5") in keys
    assert (2, "gpt-4o") in keys
    assert all("day" in r for r in rows)


# ── 明细分页 ──────────────────────────────────────────────────────────────────


def test_list_events_pagination(store: UsageStore) -> None:
    for i in range(5):
        _seed(store, prompt_tokens=i)
    now = int(time.time())
    page1, total = store.list_events(now - 3600, now + 3600, limit=2, offset=0)
    assert total == 5
    assert len(page1) == 2
    page3, _ = store.list_events(now - 3600, now + 3600, limit=2, offset=4)
    assert len(page3) == 1


def test_list_events_model_filter(store: UsageStore) -> None:
    _seed(store, model_id="kimi-k2.5")
    _seed(store, model_id="gpt-4o")
    now = int(time.time())
    rows, total = store.list_events(now - 3600, now + 3600, model_id="gpt-4o")
    assert total == 1
    assert rows[0]["model_id"] == "gpt-4o"


# ── 用户隔离 / 级联清理 ────────────────────────────────────────────────────────


def test_per_user_filter(store: UsageStore) -> None:
    _seed(store, user_id=1)
    _seed(store, user_id=2)
    now = int(time.time())
    u1 = store.aggregate_by_model(now - 3600, now + 3600, user_id=1)
    assert sum(r["count"] for r in u1) == 1


def test_delete_all_for_user_isolated(store: UsageStore) -> None:
    _seed(store, user_id=1)
    _seed(store, user_id=2)
    deleted = store.delete_all_for_user(1)
    assert deleted == 1
    now = int(time.time())
    assert sum(r["count"] for r in store.aggregate_by_model(now - 3600, now + 3600, user_id=1)) == 0
    assert sum(r["count"] for r in store.aggregate_by_model(now - 3600, now + 3600, user_id=2)) == 1


# ── 缓存命中率采集（cache_lookups） ────────────────────────────────────────────


def test_cache_lookup_aggregate_counts_hits_and_misses(store: UsageStore) -> None:
    store.record_cache_lookup(1, hit=True, saved=0.3)
    store.record_cache_lookup(1, hit=False)
    store.record_cache_lookup(1, hit=True, saved=0.2)
    now = int(time.time())
    agg = store.aggregate_cache_lookups(now - 3600, now + 3600, user_id=1)
    assert agg["lookups"] == 3
    assert agg["hits"] == 2
    assert agg["saved"] == pytest.approx(0.5)


def test_cache_lookup_miss_records_no_saving(store: UsageStore) -> None:
    # 未命中即便传了 saved 也记 0（节省只算命中的）
    store.record_cache_lookup(1, hit=False, saved=9.9)
    now = int(time.time())
    assert store.aggregate_cache_lookups(now - 3600, now + 3600, user_id=1)["saved"] == 0.0


def test_cache_lookup_series_groups_hits_by_day(store: UsageStore) -> None:
    store.record_cache_lookup(1, hit=True, saved=0.3)
    store.record_cache_lookup(1, hit=False)
    now = int(time.time())
    rows = store.cache_lookups_series(now - 3600, now + 3600, user_id=1)
    assert len(rows) == 1
    assert rows[0]["kind"] == "cache"
    assert rows[0]["count"] == 1
    assert rows[0]["saved"] == pytest.approx(0.3)


def test_cache_lookup_per_user_filter(store: UsageStore) -> None:
    store.record_cache_lookup(1, hit=True)
    store.record_cache_lookup(2, hit=False)
    now = int(time.time())
    agg1 = store.aggregate_cache_lookups(now - 3600, now + 3600, user_id=1)
    agg_all = store.aggregate_cache_lookups(now - 3600, now + 3600)
    assert (agg1["lookups"], agg1["hits"]) == (1, 1)
    assert (agg_all["lookups"], agg_all["hits"]) == (2, 1)


def test_cache_lookup_empty_returns_zero(store: UsageStore) -> None:
    now = int(time.time())
    agg = store.aggregate_cache_lookups(now - 3600, now + 3600)
    assert (agg["lookups"], agg["hits"], agg["saved"]) == (0, 0, 0.0)


def test_delete_all_for_user_clears_cache_lookups(store: UsageStore) -> None:
    store.record_cache_lookup(1, hit=True)
    store.record_cache_lookup(2, hit=True)
    store.delete_all_for_user(1)
    now = int(time.time())
    assert store.aggregate_cache_lookups(now - 3600, now + 3600, user_id=1)["lookups"] == 0
    assert store.aggregate_cache_lookups(now - 3600, now + 3600, user_id=2)["lookups"] == 1


# ── 单价覆盖 + 合并 + 成本 ─────────────────────────────────────────────────────


def test_pricing_override_crud(store: UsageStore) -> None:
    store.set_pricing("gpt-4o", 1.0, 2.0)
    assert store.get_pricing_overrides()["gpt-4o"] == (1.0, 2.0)
    store.set_pricing("gpt-4o", 3.0, 4.0)  # upsert
    assert store.get_pricing_overrides()["gpt-4o"] == (3.0, 4.0)
    store.delete_pricing("gpt-4o")
    assert "gpt-4o" not in store.get_pricing_overrides()


def test_set_pricing_bulk(store: UsageStore) -> None:
    store.set_pricing_bulk({"gpt-4o": (1.0, 2.0), "kimi-k2.5": (0.5, 1.5)})
    ov = store.get_pricing_overrides()
    assert ov["gpt-4o"] == (1.0, 2.0)
    assert ov["kimi-k2.5"] == (0.5, 1.5)


def test_merged_pricing_override_wins(store: UsageStore) -> None:
    store.set_pricing("gpt-4o", 99.0, 88.0)
    merged = merged_pricing(store)
    assert merged["gpt-4o"] == (99.0, 88.0)
    # 未覆盖的仍来自默认
    assert merged["kimi-k2.5"] == config.MODEL_PRICING_DEFAULTS["kimi-k2.5"]


def test_cost_of_math() -> None:
    pricing = {"gpt-4o": (2.0, 4.0)}  # $/1M
    # 1M 输入 + 0.5M 输出 = 2.0 + 2.0 = 4.0
    assert cost_of("gpt-4o", 1_000_000, 500_000, pricing) == pytest.approx(4.0)
    # 未知模型成本 0
    assert cost_of("unknown", 1_000_000, 1_000_000, pricing) == 0.0


# ── record_usage 旁路入口 ─────────────────────────────────────────────────────


class TestRecordUsage:
    @pytest.fixture(autouse=True)
    def _shared(self, tmp_path: Path):
        s = UsageStore(str(tmp_path / "shared_usage.db"))
        reset_shared_store_for_testing(s)
        yield s
        reset_shared_store_for_testing(None)
        s.close()

    def test_records_namedtuple_usage(self, _shared: UsageStore) -> None:
        record_usage(5, "gpt-4o", True, _Usage(10, 20, 30), session_id="s1")
        now = int(time.time())
        rows, total = _shared.list_events(now - 60, now + 60, user_id=5)
        assert total == 1
        assert rows[0]["prompt_tokens"] == 10
        assert rows[0]["total_tokens"] == 30
        assert rows[0]["thinking"] is True

    def test_skips_none(self, _shared: UsageStore) -> None:
        record_usage(5, "gpt-4o", False, None)
        now = int(time.time())
        _, total = _shared.list_events(now - 60, now + 60)
        assert total == 0

    def test_skips_empty_usage(self, _shared: UsageStore) -> None:
        record_usage(5, "gpt-4o", False, _Usage(0, 0, 0))
        now = int(time.time())
        _, total = _shared.list_events(now - 60, now + 60)
        assert total == 0

    def test_never_raises_on_bad_usage(self, _shared: UsageStore) -> None:
        # 传入怪对象不应抛（旁路保护）
        record_usage(5, "gpt-4o", False, object())
        # 不抛即通过

    def test_total_zero_falls_back_to_sum(self, _shared: UsageStore) -> None:
        # provider 回 total=0 但有输入输出 → 总数应回落 prompt+completion，不存成 0
        record_usage(5, "gpt-4o", False, _Usage(30, 12, 0))
        now = int(time.time())
        rows, total = _shared.list_events(now - 60, now + 60, user_id=5)
        assert total == 1
        assert rows[0]["total_tokens"] == 42
