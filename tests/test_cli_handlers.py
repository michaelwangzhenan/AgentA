"""CLI handlers 辅助函数单测。"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cli import handlers
from src.cli.handlers import (
    _conversation_messages,
    _format_relative_time,
    _is_visible_assistant_message,
    _sanitize_cli_text,
)
from src.memory.chat_history import ChatHistoryStore


def test_sanitize_cli_text_strips_carriage_returns() -> None:
    assert _sanitize_cli_text("根据\r\n知识库") == "根据\n知识库"
    assert _sanitize_cli_text("Agent:\r开头") == "Agent:\n开头"


def test_is_visible_assistant_skips_tool_only() -> None:
    assert not _is_visible_assistant_message(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}
    )
    assert _is_visible_assistant_message(
        {"role": "assistant", "content": "最终回答", "tool_calls": [{"id": "1"}]}
    )


def test_conversation_messages_filters_tool_only_assistant() -> None:
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "assistant", "content": "最终回答"},
    ]
    out = _conversation_messages(msgs)
    assert len(out) == 2
    assert out[1]["content"] == "最终回答"


# ── _format_relative_time ────────────────────────────────────────────────────

class TestFormatRelativeTime:
    """覆盖时间格式化的 5 个分支：今天 / 昨天 / N 天前 / 更早 / 解析失败降级。"""

    def test_today_shows_HHMM(self) -> None:
        ts = datetime.now().replace(hour=14, minute=32, second=0).isoformat(timespec="seconds")
        assert _format_relative_time(ts).startswith("今天 ")
        assert "14:32" in _format_relative_time(ts)

    def test_yesterday_shows_HHMM(self) -> None:
        ts = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        assert _format_relative_time(ts).startswith("昨天 ")

    @pytest.mark.parametrize("days", [2, 3, 7])
    def test_within_week_shows_n_days_ago(self, days: int) -> None:
        ts = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        assert _format_relative_time(ts) == f"{days} 天前"

    def test_older_than_week_shows_date(self) -> None:
        ts = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        formatted = _format_relative_time(ts)
        # YYYY-MM-DD 形式
        assert len(formatted) == 10 and formatted.count("-") == 2

    def test_invalid_iso_falls_back(self) -> None:
        # 非 ISO 字符串应降级而不抛异常，原样返回（截到 19 字符内）
        assert _format_relative_time("not-an-iso-string") == "not-an-iso-string"

    def test_invalid_iso_long_string_is_truncated(self) -> None:
        # 超过 19 字符的非 ISO 串截到 19 字符
        result = _format_relative_time("garbage-very-long-string-here")
        assert result == "garbage-very-long-s"
        assert len(result) == 19


# ── list_sessions 输出强化 ───────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> ChatHistoryStore:
    db = ChatHistoryStore(db_path=str(tmp_path / "handlers_test.db"))
    yield db
    db.close()


class TestListSessionsOutput:
    """覆盖 list_sessions 输出的过滤、空态、高亮、query 标题。"""

    def _seed(self, store: ChatHistoryStore) -> None:
        store.append("aaa11111-pre", {"role": "user", "content": "RAG 怎么做"})
        store.append("bbb22222-cur", {"role": "user", "content": "Agent ReAct"})

    def test_empty_store_shows_empty_hint(self, store: ChatHistoryStore) -> None:
        lines: list[str] = []
        handlers.list_sessions(store, out=lines.append)
        assert any("暂无历史 session" in s for s in lines)

    def test_query_no_match_mentions_query(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        lines: list[str] = []
        handlers.list_sessions(store, query="nope-xxx", out=lines.append)
        assert any("没有匹配" in s and "nope-xxx" in s for s in lines)

    def test_query_in_title_when_filtered(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        lines: list[str] = []
        handlers.list_sessions(store, query="ReAct", out=lines.append)
        full = "\n".join(lines)
        assert "过滤" in full and "ReAct" in full
        # ReAct 匹配 bbb22222-cur，不应有 aaa11111-pre
        assert "bbb22222" in full
        assert "aaa11111" not in full

    def test_current_session_is_marked(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        lines: list[str] = []
        handlers.list_sessions(store, current_session_id="bbb22222-cur", out=lines.append)
        full = "\n".join(lines)
        # 当前 session 行带 ▶ 标记，另一行不带
        current_line = next(s for s in lines if "bbb22222" in s)
        other_line = next(s for s in lines if "aaa11111" in s)
        assert "▶" in current_line
        assert "▶" not in other_line


# ── switch_session 预览强化 ─────────────────────────────────────────────────

class TestSwitchSessionPreview:
    """覆盖 switch_session 末尾的最近消息预览（B3）+ 无参防御。"""

    def test_empty_arg_returns_none_with_hint(self, store: ChatHistoryStore) -> None:
        lines: list[str] = []
        # 用 patch 防止真的去构造 Agent（依赖 LLM provider 配置）
        with patch.object(handlers, "make_agent") as m:
            result = handlers.switch_session(
                store, "", custom_prompts={}, default_system_prompt="sys",
                skills_map={}, thinking_cfg=None, out=lines.append,
            )
        assert result is None
        assert m.call_count == 0
        assert any("/session 需要 session id" in s for s in lines)

    def test_switch_appends_recent_preview(self, store: ChatHistoryStore) -> None:
        sid = "preview-sid"
        store.append(sid, {"role": "user", "content": "Q1"})
        store.append(sid, {"role": "assistant", "content": "A1"})
        store.append(sid, {"role": "user", "content": "Q2"})
        store.append(sid, {"role": "assistant", "content": "A2"})

        lines: list[str] = []
        with patch.object(handlers, "make_agent", return_value=object()):
            handlers.switch_session(
                store, sid, custom_prompts={}, default_system_prompt="sys",
                skills_map={}, thinking_cfg=None, out=lines.append,
            )
        full = "\n".join(lines)
        assert "最近对话预览" in full
        # 取 _SWITCH_PREVIEW_COUNT=2 条 → 应是 Q2 + A2
        assert "Q2" in full and "A2" in full
        # Q1/A1 不应出现在预览（但切换信息行的 session 计数无关）
        assert "Q1" not in full and "A1" not in full

    def test_switch_with_no_history_skips_preview(self, store: ChatHistoryStore) -> None:
        lines: list[str] = []
        with patch.object(handlers, "make_agent", return_value=object()):
            handlers.switch_session(
                store, "empty-sid", custom_prompts={}, default_system_prompt="sys",
                skills_map={}, thinking_cfg=None, out=lines.append,
            )
        full = "\n".join(lines)
        # 空 session 不应触发预览块
        assert "最近对话预览" not in full
