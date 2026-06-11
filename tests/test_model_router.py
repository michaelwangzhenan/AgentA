"""模型路由 UT（iter_14）：难度判定 / 向下约束 / auto 基准 / 候选池持久化 / 软失败。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.config as config
from src.llm import model_router


@pytest.fixture
def fake_catalog(monkeypatch):
    """三档假模型：cheap(min) < mid(medium) < exp(max)，价格递增。"""
    models = {
        "cheap": SimpleNamespace(tier="min", provider="p", label="Cheap"),
        "mid": SimpleNamespace(tier="medium", provider="p", label="Mid"),
        "exp": SimpleNamespace(tier="max", provider="p", label="Exp"),
    }
    pricing = {"cheap": (1.0, 1.0), "mid": (5.0, 5.0), "exp": (20.0, 20.0)}
    monkeypatch.setattr(config, "MODEL_CONFIGS", models)
    monkeypatch.setattr(config, "MODEL_PRICING_DEFAULTS", pricing)
    monkeypatch.setattr(model_router, "effective_pool", lambda: ["cheap", "mid", "exp"])
    return models


# ── 难度判定 ──────────────────────────────────────────────────────────────────


def test_rule_difficulty_easy():
    assert model_router._rule_difficulty("什么是向量数据库") == "easy"


def test_rule_difficulty_hard_by_keyword():
    assert model_router._rule_difficulty("请分析并比较两种架构的权衡") == "hard"


def test_rule_difficulty_hard_by_length():
    assert model_router._rule_difficulty("说明一下。" * 40) == "hard"


def test_rule_difficulty_medium():
    assert model_router._rule_difficulty("帮我整理一下今天的会议要点") == "medium"


# ── 路由决策：只向下 ──────────────────────────────────────────────────────────


def test_easy_query_downgrades_to_cheapest(fake_catalog):
    # auto 档才路由：简单问题从池内最高档降到最便宜
    d = model_router.route("什么是 RAG", model_router.AUTO_MODEL, enabled=True, mode="rule")
    assert d.model_id == "cheap"
    assert d.baseline == "exp"
    assert d.downgraded is True


def test_hard_query_keeps_baseline(fake_catalog):
    d = model_router.route(
        "请分析并推导这个算法的复杂度权衡" * 5, model_router.AUTO_MODEL, enabled=True, mode="rule"
    )
    assert d.model_id == "exp"
    assert d.downgraded is False


def test_never_route_above_baseline(fake_catalog):
    # 候选池仅最便宜档可达时不会向上升级（auto 基准=exp，难题目标=exp 但池里也有 cheap）
    d = model_router.route(
        "请分析并比较架构权衡" * 5, model_router.AUTO_MODEL, enabled=True, mode="rule"
    )
    assert model_router._tier_rank(d.model_id) <= model_router._tier_rank(d.baseline)


def test_selected_concrete_model_locked(fake_catalog):
    # 手选具体模型 = 精确锁定，简单问题也不降级
    d = model_router.route("什么是 RAG", "mid", enabled=True, mode="rule")
    assert d.model_id == "mid"
    assert d.baseline == "mid"
    assert d.downgraded is False


def test_selected_top_model_locked_on_easy(fake_catalog):
    # 手选最高档遇简单问题仍锁定，不降到 cheap
    d = model_router.route("什么是 RAG", "exp", enabled=True, mode="rule")
    assert d.model_id == "exp"
    assert d.downgraded is False


def test_routing_disabled_returns_baseline(fake_catalog):
    d = model_router.route("什么是 RAG", "exp", enabled=False)
    assert d.model_id == "exp"
    assert d.downgraded is False


def test_auto_baseline_is_pool_top(fake_catalog):
    # auto：基准取池内最高档 exp；简单问题降到 cheap
    d = model_router.route("什么是 RAG", model_router.AUTO_MODEL, enabled=True, mode="rule")
    assert d.baseline == "exp"
    assert d.model_id == "cheap"


def test_classifier_empty_model_falls_back_to_rule(fake_catalog):
    d = model_router.route(
        "什么是 RAG", model_router.AUTO_MODEL, enabled=True, mode="classifier", classifier_model=""
    )
    assert d.model_id == "cheap"
    assert "rule" in d.mode


# ── 候选池持久化 ──────────────────────────────────────────────────────────────


def test_pool_config_roundtrip(monkeypatch, tmp_path, fake_catalog):
    monkeypatch.setattr(model_router, "_POOL_PATH", tmp_path / "routing_pool.json")
    saved = model_router.set_pool_config(["cheap", "mid", "unknown_id", "cheap"])
    assert saved == ["cheap", "mid"]  # 去重 + 过滤未知
    assert model_router.get_pool_config() == ["cheap", "mid"]


def test_pool_config_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(model_router, "_POOL_PATH", tmp_path / "nope.json")
    assert model_router.get_pool_config() == []
