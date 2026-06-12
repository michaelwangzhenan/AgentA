"""
测试：`HistoryManager.load_truncated` + `HistoryManager._collect_skill_pairs`

§4.4.3 时这些用例的主语是 `Agent._load_truncated_history`（行为基线），
§4.5 抽出 helper 后切到 `src.agent.core.history_manager.HistoryManager`，
断言保持不变 —— 这就是"安全网"的兑现。

覆盖分支：
- 空历史 → 空列表
- 历史 ≤ max_history_turns → 不截断
- 历史 > max_history_turns → 按"用户轮"为锚截断到最近 N 轮
- system 消息不计入历史，被过滤掉
- 截断时含 `<skill_content>` 的 assistant+tool 消息组被保护
- SQL 层粗粒度过滤上限 = max_history_turns × _HISTORY_FETCH_MULTIPLIER

设计：直接 mock `ChatHistoryStore.load_last_n_messages`，绕开 SQLite。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agent.core.history_manager import HistoryManager, _HISTORY_FETCH_MULTIPLIER


# ── 测试夹具：构造一个 HistoryManager + mock 过的 ChatHistoryStore ───────────────

def _make_mgr(messages: list[dict[str, Any]], max_history_turns: int = 20) -> HistoryManager:
    """构造 HistoryManager，把 load_last_n_messages mock 成固定返回 messages。"""
    mock_history = MagicMock()
    mock_history.load_last_n_messages.return_value = messages
    return HistoryManager(
        chat_history=mock_history,
        session_id="test-session",
        max_history_turns=max_history_turns,
    )


# ── 基本边界 ─────────────────────────────────────────────────────────────────

class TestLoadTruncatedBasics:

    def test_empty_history_returns_empty_list(self) -> None:
        mgr = _make_mgr([])
        assert mgr.load_truncated() == []

    def test_system_messages_filtered_out(self) -> None:
        msgs = [
            {"role": "system", "content": "应被丢弃"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
        ]
        result = _make_mgr(msgs).load_truncated()
        assert all(m["role"] != "system" for m in result)
        assert len(result) == 2

    def test_under_max_turns_returns_all(self) -> None:
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = _make_mgr(msgs, max_history_turns=20).load_truncated()
        assert result == msgs

    def test_sql_fetch_limit_uses_multiplier(self) -> None:
        mgr = _make_mgr([], max_history_turns=20)
        mgr.load_truncated()
        mgr._chat_history.load_last_n_messages.assert_called_once()
        _, n_arg = mgr._chat_history.load_last_n_messages.call_args[0]
        assert n_arg == 20 * _HISTORY_FETCH_MULTIPLIER


# ── 截断策略：按"用户轮"为锚 ─────────────────────────────────────────────────

class TestLoadTruncatedTruncation:

    def test_truncates_to_last_n_user_turns(self) -> None:
        """5 轮对话，max_history_turns=2 → 只保留最后 2 个 user 起到的尾段。"""
        msgs: list[dict[str, Any]] = []
        for i in range(1, 6):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        result = _make_mgr(msgs, max_history_turns=2).load_truncated()
        contents = [m["content"] for m in result]
        assert contents == ["q4", "a4", "q5", "a5"]

    def test_assistant_with_tool_calls_grouped_with_tools(self) -> None:
        """
        assistant + tool_calls 不带 skill_content → 老逻辑只按 user-anchor 截断，
        不会被特别保护。本测试验证：截断到最后 1 轮时，前面的 tool 组直接被丢弃。
        """
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "tool_call_id": "t1", "content": "result1"},
            {"role": "assistant", "content": "a1 final"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = _make_mgr(msgs, max_history_turns=1).load_truncated()
        # 只剩 q2 + a2 这一轮
        assert [m["content"] for m in result] == ["q2", "a2"]
        # 验证：被截掉的 tool 消息不再出现，防止"孤儿 tool 消息"违反 OpenAI 协议
        assert all(m.get("role") != "tool" for m in result)


# ── skill_pair 保护 ─────────────────────────────────────────────────────────

class TestCollectSkillPairs:

    def test_no_assistant_tool_calls_returns_empty(self) -> None:
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        assert HistoryManager._collect_skill_pairs(msgs) == []

    def test_tool_calls_without_skill_content_not_protected(self) -> None:
        """没有 <skill_content> 标签的 tool 组不被保护（避免无意义保留）。"""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "tool_call_id": "t1", "content": "普通工具结果"},
        ]
        assert HistoryManager._collect_skill_pairs(msgs) == []

    def test_tool_with_skill_content_protected_as_group(self) -> None:
        """tool 内容含 `<skill_content` 时，整组（assistant + 全部连续 tool）被保留。"""
        msgs = [
            {"role": "user", "content": "用 skill"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "tool_call_id": "t1",
             "content": '<skill_content name="x">body</skill_content>'},
        ]
        out = HistoryManager._collect_skill_pairs(msgs)
        assert len(out) == 2  # assistant + tool 整组
        assert out[0]["role"] == "assistant"
        assert out[1]["role"] == "tool"

    def test_multiple_tools_in_one_assistant_all_kept(self) -> None:
        """一个 assistant 多个并行 tool_calls，组内任一 tool 含 skill_content 即保留全组。"""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}, {"id": "t2"}]},
            {"role": "tool", "tool_call_id": "t1", "content": "普通结果"},
            {"role": "tool", "tool_call_id": "t2",
             "content": '<skill_content name="x">body</skill_content>'},
        ]
        out = HistoryManager._collect_skill_pairs(msgs)
        # 整组 3 条全保留
        assert len(out) == 3

    def test_truncation_prepends_protected_skill_group(self) -> None:
        """
        端到端验证：截断会把被丢弃区间内的 skill_content 组前置回保留区段头部，
        避免 skill 上下文丢失。
        """
        msgs = [
            # 第 1 轮：含 skill_content
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "tool_call_id": "t1",
             "content": '<skill_content name="x">body</skill_content>'},
            {"role": "assistant", "content": "a1"},
            # 第 2~3 轮：普通对话
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "a3"},
        ]
        result = _make_mgr(msgs, max_history_turns=1).load_truncated()
        # 最后 1 轮 = q3 + a3，前置保护 skill_pair = assistant + tool（共 2 条）
        contents = [(m["role"], m.get("content")) for m in result]
        assert contents[0] == ("assistant", "")
        assert contents[1][0] == "tool"
        assert "<skill_content" in contents[1][1]
        assert contents[-2:] == [("user", "q3"), ("assistant", "a3")]
