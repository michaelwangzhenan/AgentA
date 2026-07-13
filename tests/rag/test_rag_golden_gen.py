"""RAG 入库自动生成 golden 候选 UT（iter_14）。LLM 全部 mock，不真发调用。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.stores import golden_store
from src.stores.golden_store import GoldenStore, STATUS_PENDING, SOURCE_AI
from src.rag import golden_gen


def _fake_resp(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_generate_candidates_parses_json() -> None:
    payload = (
        '[{"query": "什么是 RAG?", "expected_keywords": ["RAG", "检索"], "type": "3gpp-def"}, '
        '{"query": "如何分块?", "expected_keywords": ["chunk"], "type": "project-impl"}]'
    )
    with patch("src.llm.provider.chat", return_value=_fake_resp(payload)):
        out = golden_gen.generate_candidates("一些资料正文", max_q=3, llm_model="kimi-k2.5")
    assert len(out) == 2
    assert out[0]["query"] == "什么是 RAG?"
    assert out[0]["expected_keywords"] == ["RAG", "检索"]
    assert out[0]["type"] == "3gpp-def"


def test_generate_candidates_respects_max_q() -> None:
    payload = '[{"query":"a"},{"query":"b"},{"query":"c"}]'
    with patch("src.llm.provider.chat", return_value=_fake_resp(payload)):
        out = golden_gen.generate_candidates("正文", max_q=2, llm_model="kimi-k2.5")
    assert len(out) == 2


def test_generate_candidates_empty_text() -> None:
    assert golden_gen.generate_candidates("   ", max_q=3) == []


def test_generate_candidates_bad_json_soft_fail() -> None:
    with patch("src.llm.provider.chat", return_value=_fake_resp("not json at all")):
        assert golden_gen.generate_candidates("正文", max_q=3, llm_model="kimi-k2.5") == []


def test_generate_candidates_llm_error_soft_fail() -> None:
    with patch("src.llm.provider.chat", side_effect=RuntimeError("boom")):
        assert golden_gen.generate_candidates("正文", max_q=3, llm_model="kimi-k2.5") == []


def test_run_generation_writes_pending(tmp_path: Path) -> None:
    store = GoldenStore(str(tmp_path / "g.db"))
    golden_store.reset_shared_store_for_testing(store)
    try:
        payload = '[{"query": "Q1?", "expected_keywords": ["k1"], "type": "personal-bio"}]'
        with patch("src.rag.parser.parse_file", return_value="资料正文"), \
             patch("src.llm.provider.chat", return_value=_fake_resp(payload)):
            n = golden_gen.run_generation_for_file(
                tmp_path / "doc.md",
                source="doc.md",
                doc_id="abc",
                llm_model="kimi-k2.5",
                force=True,
            )
        assert n == 1
        rows, total = store.list()
        assert total == 1
        item = rows[0]
        assert item["status"] == STATUS_PENDING
        assert item["source"] == SOURCE_AI
        assert item["expected_source_contains"] == "doc.md"
        assert item["doc_id"] == "abc"
        assert item["type"] == "personal-bio"
    finally:
        golden_store.reset_shared_store_for_testing(None)
        store.close()


def test_generate_candidates_large_file_multiple_llm_calls() -> None:
    big = "x" * 7000
    payload = '[{"query": "Q?", "expected_keywords": ["x"], "type": "3gpp-def"}]'
    with patch("src.llm.provider.chat", return_value=_fake_resp(payload)) as chat_mock:
        out = golden_gen.generate_candidates(big, max_q=3, llm_model="kimi-k2.5")
    assert len(out) >= 1
    assert chat_mock.call_count == 2


def test_generate_candidates_dedupes_queries() -> None:
    payload = (
        '[{"query": "相同问题?", "expected_keywords": ["a"]}, '
        '{"query": "相同问题?", "expected_keywords": ["b"]}, '
        '{"query": "另一题?", "expected_keywords": ["c"]}]'
    )
    with patch("src.llm.provider.chat", return_value=_fake_resp(payload)):
        out = golden_gen.generate_candidates("正文内容", max_q=3, llm_model="kimi-k2.5")
    assert len(out) == 2


def test_run_generation_without_llm_skips(tmp_path: Path) -> None:
    with patch("src.rag.parser.parse_file") as pf:
        assert golden_gen.run_generation_for_file(tmp_path / "x.md", "x.md") == 0
        pf.assert_not_called()


def test_run_generation_parse_error_soft_fail(tmp_path: Path) -> None:
    with patch("src.rag.parser.parse_file", side_effect=OSError("no file")):
        assert golden_gen.run_generation_for_file(
            tmp_path / "x.md", "x.md", llm_model="kimi-k2.5", force=True,
        ) == 0
