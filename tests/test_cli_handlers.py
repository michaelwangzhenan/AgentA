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
                store, "", default_system_prompt="sys",
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
                store, sid, default_system_prompt="sys",
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
                store, "empty-sid", default_system_prompt="sys",
                skills_map={}, thinking_cfg=None, out=lines.append,
            )
        full = "\n".join(lines)
        # 空 session 不应触发预览块
        assert "最近对话预览" not in full


# ── Phase 1.2 /memory 子命令（iter_2.md §4.9.2） ──────────────────────────

from collections.abc import Iterator
from src.memory.user_memory import UserMemoryStore


@pytest.fixture
def mem_store(tmp_path: Path) -> Iterator[UserMemoryStore]:
    db = UserMemoryStore(str(tmp_path / "mem.db"))
    yield db
    db.close()


def _call_memory(mem: UserMemoryStore, *args: str) -> list[str]:
    """组装 cmd_parts 调 handle_memory，捕获所有输出行。

    main.py 切割规则：cmd_parts = user_input.split(maxsplit=1) → 子命令含空格时
    全部塞在 cmd_parts[1] 中。这里复现：把 args 用空格拼起来当 cmd_parts[1]。
    """
    cmd_parts: list[str] = ["/memory"]
    if args:
        cmd_parts.append(" ".join(args))
    lines: list[str] = []
    handlers.handle_memory(mem, cmd_parts, out=lines.append)
    return lines


class TestManualWrite:
    """/memory add 与 /memory edit 的手动写入路径。"""

    def test_add_basic(self, mem_store: UserMemoryStore) -> None:
        lines = _call_memory(mem_store, "add", "preference", "lang", "中文回答")
        rows = mem_store.load_all()
        assert len(rows) == 1
        assert rows[0]["category"] == "preference"
        assert rows[0]["key"] == "lang"
        assert rows[0]["value"] == "中文回答"
        assert rows[0]["source"] == "manual"
        assert any("已记录" in s for s in lines)

    def test_add_value_with_spaces_preserved(self, mem_store: UserMemoryStore) -> None:
        """value 中的空格 + 大小写必须原样保留（不能被 lower）。"""
        lines = _call_memory(
            mem_store, "add", "instruction", "cite_style", "APA 7th Edition with page #"
        )
        rows = mem_store.load_all()
        assert rows[0]["value"] == "APA 7th Edition with page #"

    def test_add_category_case_insensitive(self, mem_store: UserMemoryStore) -> None:
        """类别大小写不敏感（用户敲 Preference 也应识别）。"""
        _call_memory(mem_store, "add", "PREFERENCE", "lang", "中文")
        assert mem_store.load_all()[0]["category"] == "preference"

    def test_add_unknown_category_rejected(self, mem_store: UserMemoryStore) -> None:
        lines = _call_memory(mem_store, "add", "bogus", "k", "v")
        assert mem_store.load_all() == []
        assert any("未知类别" in s for s in lines)

    def test_add_missing_args_shows_usage(self, mem_store: UserMemoryStore) -> None:
        lines = _call_memory(mem_store, "add", "preference", "only_key")
        assert mem_store.load_all() == []
        assert any("用法" in s for s in lines)

    def test_edit_updates_value(self, mem_store: UserMemoryStore) -> None:
        mem_store.upsert("preference", "lang", "中文", source="manual")
        row_id = mem_store.load_all()[0]["id"]
        lines = _call_memory(mem_store, "edit", str(row_id), "English with examples")
        assert mem_store.load_all()[0]["value"] == "English with examples"
        assert any("已更新" in s for s in lines)

    def test_edit_missing_id_friendly(self, mem_store: UserMemoryStore) -> None:
        lines = _call_memory(mem_store, "edit", "9999", "new value")
        assert any("不存在" in s for s in lines)

    def test_edit_invalid_id_friendly(self, mem_store: UserMemoryStore) -> None:
        lines = _call_memory(mem_store, "edit", "abc", "x")
        assert any("无效 ID" in s for s in lines)


class TestMemoryOutput:
    """/memory（无参）：分组、source 列、人性化时间。"""

    def test_empty_db_shows_hint(self, mem_store: UserMemoryStore) -> None:
        lines = _call_memory(mem_store)
        assert any("没有任何记忆" in s for s in lines)

    def test_grouped_by_category_in_fixed_order(self, mem_store: UserMemoryStore) -> None:
        mem_store.upsert("background", "job", "工程师", source="auto")
        mem_store.upsert("preference", "lang", "中文", source="manual")
        mem_store.upsert("instruction", "cite", "APA", source="explicit")
        lines = _call_memory(mem_store)
        full = "\n".join(lines)
        # 顺序：preference → background → instruction（MEMORY_CATEGORY_ORDER）
        i_pref = full.find("偏好")
        i_back = full.find("背景")
        i_inst = full.find("指令")
        assert 0 < i_pref < i_back < i_inst

    def test_source_labels_rendered(self, mem_store: UserMemoryStore) -> None:
        mem_store.upsert("preference", "k1", "v1", source="auto")
        mem_store.upsert("preference", "k2", "v2", source="explicit")
        mem_store.upsert("preference", "k3", "v3", source="manual")
        full = "\n".join(_call_memory(mem_store))
        assert "自动" in full
        assert "请记住" in full
        assert "手工" in full

    def test_relative_time_rendered(self, mem_store: UserMemoryStore) -> None:
        """新写入条目应显示 '今天 HH:MM'（_format_relative_time 路径）。"""
        mem_store.upsert("preference", "k", "v", source="manual")
        full = "\n".join(_call_memory(mem_store))
        assert "今天" in full

    def test_unknown_subcmd_shows_usage(self, mem_store: UserMemoryStore) -> None:
        lines = _call_memory(mem_store, "wtf")
        full = "\n".join(lines)
        assert "未知子命令" in full
        assert "add" in full and "edit" in full

    def test_del_still_works(self, mem_store: UserMemoryStore) -> None:
        mem_store.upsert("preference", "k", "v", source="auto")
        row_id = mem_store.load_all()[0]["id"]
        lines = _call_memory(mem_store, "del", str(row_id))
        assert mem_store.load_all() == []
        assert any("已删除" in s for s in lines)

    def test_clear_still_works(self, mem_store: UserMemoryStore) -> None:
        mem_store.upsert("preference", "k1", "v1", source="auto")
        mem_store.upsert("preference", "k2", "v2", source="manual")
        lines = _call_memory(mem_store, "clear")
        assert mem_store.load_all() == []
        assert any("已清空" in s and "2" in s for s in lines)
