"""RAG 入库自动生成 golden 候选 UT（iter_14）。LLM 全部 mock，不真发调用。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.config as config
from src.memory import golden_store
from src.memory.golden_store import GoldenStore, STATUS_PENDING, SOURCE_AI
from src.rag import golden_gen


def _fake_resp(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_generate_candidates_parses_json() -> None:
    payload = '[{"query": "什么是 RAG?", "expected_keywords": ["RAG", "检索"]}, ' \
              '{"query": "如何分块?", "expected_keywords": ["chunk"]}]'
    with patch("src.llm.provider.chat", return_value=_fake_resp(payload)):
        out = golden_gen.generate_candidates("一些资料正文", max_q=3)
    assert len(out) == 2
    assert out[0]["query"] == "什么是 RAG?"
    assert out[0]["expected_keywords"] == ["RAG", "检索"]


def test_generate_candidates_respects_max_q() -> None:
    payload = '[{"query":"a"},{"query":"b"},{"query":"c"}]'
    with patch("src.llm.provider.chat", return_value=_fake_resp(payload)):
        out = golden_gen.generate_candidates("正文", max_q=2)
    assert len(out) == 2


def test_generate_candidates_empty_text() -> None:
    assert golden_gen.generate_candidates("   ", max_q=3) == []


def test_generate_candidates_bad_json_soft_fail() -> None:
    with patch("src.llm.provider.chat", return_value=_fake_resp("not json at all")):
        assert golden_gen.generate_candidates("正文", max_q=3) == []


def test_generate_candidates_llm_error_soft_fail() -> None:
    with patch("src.llm.provider.chat", side_effect=RuntimeError("boom")):
        assert golden_gen.generate_candidates("正文", max_q=3) == []


def test_run_generation_writes_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EVAL_AUTO_GOLDEN_ENABLED", True)
    store = GoldenStore(str(tmp_path / "g.db"))
    golden_store.reset_shared_store_for_testing(store)
    try:
        payload = '[{"query": "Q1?", "expected_keywords": ["k1"]}]'
        with patch("src.rag.parser.parse_file", return_value="资料正文"), \
             patch("src.llm.provider.chat", return_value=_fake_resp(payload)):
            n = golden_gen.run_generation_for_file(
                tmp_path / "doc.md", source="doc.md", doc_id="abc"
            )
        assert n == 1
        rows, total = store.list()
        assert total == 1
        item = rows[0]
        assert item["status"] == STATUS_PENDING
        assert item["source"] == SOURCE_AI
        assert item["expected_source_contains"] == "doc.md"
        assert item["doc_id"] == "abc"
    finally:
        golden_store.reset_shared_store_for_testing(None)
        store.close()


def test_run_generation_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EVAL_AUTO_GOLDEN_ENABLED", False)
    with patch("src.rag.parser.parse_file") as pf:
        assert golden_gen.run_generation_for_file(tmp_path / "x.md", "x.md") == 0
        pf.assert_not_called()  # 开关关闭直接返回，不解析


def test_run_generation_parse_error_soft_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EVAL_AUTO_GOLDEN_ENABLED", True)
    with patch("src.rag.parser.parse_file", side_effect=OSError("no file")):
        # 不抛，返回 0
        assert golden_gen.run_generation_for_file(tmp_path / "x.md", "x.md") == 0
