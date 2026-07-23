"""敏感词过滤器单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.sensitive_word_filter import SensitiveWordFilter, ensure_loaded_for_testing


@pytest.fixture
def word_dir(tmp_path: Path) -> Path:
    base = tmp_path / "sensitive_words"
    base.mkdir()
    (base / "metadata.json").write_text(
        json.dumps({"version": "test-1.0", "word_count": 3}),
        encoding="utf-8",
    )
    (base / "deny.tsv").write_text(
        "testblock\ttest\nbadword\tviolence\n违禁测试\ttest\n",
        encoding="utf-8",
    )
    (base / "allow.txt").write_text("testblocksafe\n", encoding="utf-8")
    (base / "trad_simp.tsv").write_text("測\t测\n試\t试\n違\t违\n", encoding="utf-8")
    return base


@pytest.fixture
def filt(word_dir: Path) -> SensitiveWordFilter:
    return ensure_loaded_for_testing(word_dir)


class TestSensitiveWordFilter:
    def test_hit_plain(self, filt: SensitiveWordFilter) -> None:
        r = filt.check("这里有 testblock 词")
        assert r.hit is True
        assert r.word == "testblock"
        assert r.category == "test"

    def test_pass_clean(self, filt: SensitiveWordFilter) -> None:
        assert filt.check("普通问候").hit is False

    def test_whitelist(self, filt: SensitiveWordFilter) -> None:
        assert filt.check("使用 testblocksafe 没问题").hit is False

    def test_case_insensitive(self, filt: SensitiveWordFilter) -> None:
        assert filt.check("TeStBlOcK").hit is True

    def test_fullwidth(self, filt: SensitiveWordFilter) -> None:
        assert filt.check("ｔｅｓｔｂｌｏｃｋ").hit is True

    def test_zero_width(self, filt: SensitiveWordFilter) -> None:
        assert filt.check("te\u200bst\u200cblock").hit is True

    def test_simple_split(self, filt: SensitiveWordFilter) -> None:
        assert filt.check("te.st-blo_ck").hit is True

    def test_traditional_chinese(self, filt: SensitiveWordFilter) -> None:
        assert filt.check("違禁測試").hit is True

    def test_load_failure(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken"
        broken.mkdir()
        filt = SensitiveWordFilter()
        filt.load(broken)
        assert filt.is_ready is False
        assert filt.load_error

    def test_performance_under_10ms(self, filt: SensitiveWordFilter) -> None:
        import time

        text = "普通文本 " * 200 + "testblock"
        start = time.perf_counter()
        for _ in range(100):
            filt.check(text)
        elapsed_ms = (time.perf_counter() - start) * 1000 / 100
        assert elapsed_ms < 10
