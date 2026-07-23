"""本地敏感词过滤器：词库加载、文本标准化、白名单与 Aho-Corasick 匹配。

进程内单例，应用启动时加载一次；加载失败时标记不可用，聊天接口返回 503。
"""

from __future__ import annotations

import json
import logging
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ahocorasick

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_WORD_DIR = _PROJECT_ROOT / "resources" / "sensitive_words"

# 零宽字符
_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\ufeff\u2060")
# 常见分隔符（简单拆词绕过）
_SEPARATORS = frozenset(" \t\n\r.-_*/\\|·•・")


@dataclass(frozen=True)
class FilterResult:
    """敏感词检查结果。"""

    hit: bool
    word: str | None = None
    category: str | None = None
    word_list_version: str | None = None


class SensitiveWordFilter:
    """敏感词过滤器：标准化输入后做白名单优先的 Aho-Corasick 匹配。"""

    def __init__(self) -> None:
        self._ready = False
        self._version = ""
        self._metadata: dict[str, Any] = {}
        self._trad_to_simp: dict[str, str] = {}
        self._allow_terms: list[str] = []
        self._automaton: ahocorasick.Automaton | None = None
        self._load_error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def version(self) -> str:
        return self._version

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def load(self, base_dir: Path | str | None = None) -> None:
        """从词库目录加载；失败时标记不可用并记录错误，不抛异常。"""
        self._ready = False
        self._automaton = None
        self._load_error = None
        root = Path(base_dir) if base_dir is not None else _DEFAULT_WORD_DIR
        try:
            self._metadata = self._read_metadata(root / "metadata.json")
            self._version = str(self._metadata.get("version") or "unknown")
            self._trad_to_simp = self._read_trad_simp(root / "trad_simp.tsv")
            self._allow_terms = self._read_allow(root / "allow.txt")
            deny_entries = self._read_deny(root / "deny.tsv")
            self._automaton = self._build_automaton(deny_entries)
            self._ready = True
            logger.info(
                "[sensitive_word_filter] 词库加载完成 version=%s words=%d allow=%d dir=%s",
                self._version,
                len(deny_entries),
                len(self._allow_terms),
                root,
            )
        except Exception as exc:  # noqa: BLE001 — 启动失败不能拖垮整个服务
            self._load_error = str(exc)
            logger.error("[sensitive_word_filter] 词库加载失败：%s", exc, exc_info=True)

    def normalize(self, text: str) -> str:
        """Unicode NFKC、小写、去零宽、繁简映射、去常见分隔符。"""
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKC", text).lower()
        chars: list[str] = []
        for ch in normalized:
            if ch in _ZERO_WIDTH:
                continue
            ch = self._trad_to_simp.get(ch, ch)
            if ch in _SEPARATORS:
                continue
            chars.append(ch)
        return "".join(chars)

    def check(self, text: str) -> FilterResult:
        """检查文本；未就绪时视为不可用（调用方应返回 503）。"""
        if not self._ready or self._automaton is None:
            return FilterResult(hit=False, word_list_version=self._version or None)

        norm_text = self.normalize(text)
        if not norm_text:
            return FilterResult(hit=False, word_list_version=self._version)

        whitelist_ranges = self._whitelist_ranges(norm_text)
        for end_index, payload in self._automaton.iter(norm_text):
            word, category, norm_len = payload
            start_index = end_index - norm_len + 1
            if self._is_fully_whitelisted(start_index, end_index + 1, whitelist_ranges):
                continue
            return FilterResult(
                hit=True,
                word=word,
                category=category,
                word_list_version=self._version,
            )
        return FilterResult(hit=False, word_list_version=self._version)

    def _whitelist_ranges(self, norm_text: str) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        for term in self._allow_terms:
            norm_term = self.normalize(term)
            if not norm_term:
                continue
            start = 0
            while True:
                idx = norm_text.find(norm_term, start)
                if idx < 0:
                    break
                ranges.append((idx, idx + len(norm_term)))
                start = idx + 1
        return self._merge_ranges(ranges)

    @staticmethod
    def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if not ranges:
            return []
        ranges.sort()
        merged = [ranges[0]]
        for start, end in ranges[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _is_fully_whitelisted(
        start: int, end: int, whitelist_ranges: list[tuple[int, int]]
    ) -> bool:
        for ws, we in whitelist_ranges:
            if ws <= start and end <= we:
                return True
        return False

    def _build_automaton(
        self, entries: list[tuple[str, str]]
    ) -> ahocorasick.Automaton:
        automaton = ahocorasick.Automaton()
        added = 0
        for word, category in entries:
            norm_word = self.normalize(word)
            if not norm_word:
                continue
            automaton.add_word(norm_word, (word, category, len(norm_word)))
            added += 1
        if added == 0:
            raise ValueError("deny.tsv 无有效词条")
        automaton.make_automaton()
        return automaton

    @staticmethod
    def _read_metadata(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("metadata.json 格式错误")
        return data

    def _read_trad_simp(self, path: Path) -> dict[str, str]:
        mapping: dict[str, str] = {}
        if not path.is_file():
            return mapping
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            trad, simp = parts[0].strip(), parts[1].strip()
            if trad and simp:
                mapping[trad] = simp
        return mapping

    @staticmethod
    def _read_allow(path: Path) -> list[str]:
        if not path.is_file():
            return []
        terms: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
        return terms

    @staticmethod
    def _read_deny(path: Path) -> list[tuple[str, str]]:
        if not path.is_file():
            raise FileNotFoundError(f"缺少词库文件：{path}")
        entries: list[tuple[str, str]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            word, category = parts[0].strip(), parts[1].strip()
            if word and category:
                entries.append((word, category))
        if not entries:
            raise ValueError("deny.tsv 无有效词条")
        return entries


# ── 进程内单例 ───────────────────────────────────────────────────────────────

_shared_filter: SensitiveWordFilter | None = None
_shared_lock = threading.Lock()
_bootstrap_attempted = False


def get_shared_filter() -> SensitiveWordFilter:
    """获取进程级共享过滤器；首次调用懒加载（双检锁）。"""
    global _shared_filter
    if _shared_filter is None:
        with _shared_lock:
            if _shared_filter is None:
                _shared_filter = SensitiveWordFilter()
    return _shared_filter


def bootstrap_filter(base_dir: Path | str | None = None) -> SensitiveWordFilter:
    """应用启动时加载词库；可重复调用，仅首次真正加载。"""
    global _bootstrap_attempted
    filt = get_shared_filter()
    with _shared_lock:
        if not _bootstrap_attempted:
            filt.load(base_dir)
            _bootstrap_attempted = True
    return filt


def reset_shared_filter_for_testing(
    filt: SensitiveWordFilter | None = None, *, reload: bool = False
) -> None:
    """UT 专用：注入 mock / 重置单例。生产代码不要调用。"""
    global _shared_filter, _bootstrap_attempted
    with _shared_lock:
        _shared_filter = filt
        _bootstrap_attempted = reload or filt is not None


def ensure_loaded_for_testing(base_dir: Path | str | None = None) -> SensitiveWordFilter:
    """UT 专用：确保过滤器已加载指定词库目录。"""
    reset_shared_filter_for_testing(SensitiveWordFilter(), reload=True)
    filt = get_shared_filter()
    filt.load(base_dir)
    return filt
