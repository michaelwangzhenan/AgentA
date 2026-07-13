"""golden_options 与 golden_gen 配置 UT。"""

from __future__ import annotations

import pytest

import src.config as config
from src.rag import golden_options as go


def test_normalize_golden_llm_aliases() -> None:
    assert go.normalize_golden_llm(None) == go.GOLDEN_LLM_NONE
    assert go.normalize_golden_llm("kimi2.5") == go.GOLDEN_LLM_KIMI
    assert go.normalize_golden_llm("deepseek-v4-flash") == go.GOLDEN_LLM_DEEPSEEK
    assert go.normalize_golden_llm("bogus") == go.GOLDEN_LLM_NONE


def test_effective_golden_llm_request_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EVAL_GOLDEN_LLM", "none")
    assert go.effective_golden_llm("kimi-k2.5") == go.GOLDEN_LLM_KIMI
    assert go.effective_golden_llm(None) == go.GOLDEN_LLM_NONE


def test_clamp_golden_max_q(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EVAL_GOLDEN_MAX_Q", 3)
    assert go.clamp_golden_max_q(None) == 3
    assert go.clamp_golden_max_q(0) == go.GOLDEN_MAX_Q_MIN
    assert go.clamp_golden_max_q(99) == go.GOLDEN_MAX_Q_MAX


def test_resolve_llm_for_manual_generate_defaults_kimi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EVAL_GOLDEN_LLM", "none")
    assert go.resolve_llm_for_manual_generate(None) == go.GOLDEN_LLM_KIMI
