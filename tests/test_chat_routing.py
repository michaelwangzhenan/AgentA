"""chat 端点降本编排辅助 UT（iter_14）：瞬时错误判定 / 缓存可写判定 / 单轮判定。"""

from __future__ import annotations

import pytest

from src.api.routes import chat as chat_mod


# ── 瞬时错误判定（决定路由模型失败是否回退基准重试） ──────────────────────────


class _StatusErr(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class RateLimitError(Exception):
    pass


class APITimeoutError(Exception):
    pass


@pytest.mark.parametrize("code,expected", [(429, True), (503, True), (500, True), (400, False), (401, False)])
def test_transient_by_status(code, expected):
    assert chat_mod._is_transient_llm_error(_StatusErr(code)) is expected


def test_transient_by_class_name():
    assert chat_mod._is_transient_llm_error(RateLimitError()) is True
    assert chat_mod._is_transient_llm_error(APITimeoutError()) is True


def test_non_transient_generic():
    assert chat_mod._is_transient_llm_error(ValueError("bad request")) is False


def test_estimate_tokens():
    assert chat_mod._estimate_tokens("") == 1
    assert chat_mod._estimate_tokens("abcd" * 10) == 10


# ── 单轮判定 ──────────────────────────────────────────────────────────────────


class _FakeHistory:
    def __init__(self, msgs):
        self._msgs = msgs

    def load_last_n_messages(self, session_id, n, user_id=None):
        return self._msgs[:n]


def test_is_fresh_true_when_empty():
    assert chat_mod._is_fresh_session(_FakeHistory([]), "s", 1) is True


def test_is_fresh_false_when_has_history():
    assert chat_mod._is_fresh_session(_FakeHistory([{"role": "user"}]), "s", 1) is False


# ── 缓存可写判定 ──────────────────────────────────────────────────────────────


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(chat_mod.semantic_cache, "store_cached",
                        lambda *a, **k: calls.append((a, k)))
    return calls


def _holder(text="答案", used_tools=False, personalized=False):
    return {"text": text, "used_tools": used_tools, "personalized": personalized}


def test_store_when_eligible(captured):
    chat_mod._maybe_store_cache(True, _holder(), "q", 1, "m")
    assert len(captured) == 1


def test_no_store_when_not_fresh(captured):
    chat_mod._maybe_store_cache(False, _holder(), "q", 1, "m")
    assert captured == []


def test_no_store_when_used_tools(captured):
    # 报了 used_tools 但拿不到工具名单（非默认实现）→ 保守不写
    chat_mod._maybe_store_cache(True, _holder(used_tools=True), "q", 1, "m")
    assert captured == []


def test_store_when_only_search_knowledge(captured):
    # 只用了纯检索工具 → 仍可缓存（KB 变更会全量作废兜底）
    h = _holder(used_tools=True)
    h["tool_names"] = {"search_knowledge"}
    chat_mod._maybe_store_cache(True, h, "q", 1, "m")
    assert len(captured) == 1


def test_no_store_when_non_cacheable_tool(captured):
    # 夹带了联网搜索等不可缓存工具 → 不写
    h = _holder(used_tools=True)
    h["tool_names"] = {"search_knowledge", "web_search"}
    chat_mod._maybe_store_cache(True, h, "q", 1, "m")
    assert captured == []


def test_no_store_when_personalized(captured):
    chat_mod._maybe_store_cache(True, _holder(personalized=True), "q", 1, "m")
    assert captured == []


def test_no_store_when_empty_answer(captured):
    chat_mod._maybe_store_cache(True, _holder(text=""), "q", 1, "m")
    assert captured == []
