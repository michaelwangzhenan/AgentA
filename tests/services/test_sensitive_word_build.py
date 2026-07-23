"""sensitive_word_build 单元测试。"""

from __future__ import annotations

from pathlib import Path

from src.services.sensitive_word_build import (
    WordEntry,
    normalize_proxy_url,
    parse_extra_tsv,
    parse_tags_lines,
    proxy_port_warning,
    resolve_http_proxy,
    select_entries,
    write_word_pack,
)


def test_parse_tags_and_select_priority(tmp_path: Path) -> None:
    lines = [
        "赌博词 3",
        "政治词 0",
        "违法词 4",
        "色情词 2",
    ]
    upstream = parse_tags_lines(lines, include_tags=None)
    extras = [WordEntry("六四", "politics")]
    selected, stats = select_entries(upstream, extras=extras, max_words=3)

    assert stats.extra_count == 1
    assert len(selected) == 3
    assert selected[0].word == "六四"
    # 政治类优先
    assert any(e.word == "政治词" for e in selected)


def test_parse_extra_tsv(tmp_path: Path) -> None:
    p = tmp_path / "extra.tsv"
    p.write_text("foo\tbar\n# comment\n", encoding="utf-8")
    assert parse_extra_tsv(p) == [WordEntry("foo", "bar")]


def test_write_word_pack(tmp_path: Path) -> None:
    entries = [WordEntry("a", "politics"), WordEntry("b", "porn")]
    write_word_pack(
        tmp_path,
        entries,
        allow_lines=["白名单词"],
        upstream_commit="abc123",
        dry_run=False,
    )
    deny = (tmp_path / "deny.tsv").read_text(encoding="utf-8")
    assert "a\tpolitics" in deny
    assert (tmp_path / "allow.txt").read_text(encoding="utf-8").strip() == "白名单词"
    meta = (tmp_path / "metadata.json").read_text(encoding="utf-8")
    assert "abc123" in meta


def test_resolve_http_proxy_cli_over_env(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://env:8080")
    p = resolve_http_proxy("http://cli:7890")
    assert p == {"http": "http://cli:7890", "https": "http://cli:7890"}


def test_resolve_http_proxy_from_env(monkeypatch) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://env:3128")
    p = resolve_http_proxy()
    assert p == {"http": "http://env:3128", "https": "http://env:3128"}


def test_normalize_proxy_url_adds_scheme_and_warns_port() -> None:
    assert normalize_proxy_url("10.0.0.1:7890") == "http://10.0.0.1:7890"
    assert proxy_port_warning("http://10.0.0.1") is not None
    assert proxy_port_warning("http://10.0.0.1:7890") is None
