"""
测试对话记忆模块（memory/store.py）

测试内容：
    - ChatHistoryStore 基本 CRUD：append / load / clear / list_sessions
    - tool_calls 字段序列化与反序列化
    - tool role 的 tool_call_id 持久化
    - session 不存在时的降级处理
    - 跨 Agent 实例的历史消息正确拼接（集成）
    - 超长历史截断（集成）
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.memory.chat_history import ChatHistoryStore
from src.agent.agent import Agent


# ── 辅助 fixture ──────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> ChatHistoryStore:
    """每个测试用独立临时 DB，互不影响。"""
    db = ChatHistoryStore(db_path=str(tmp_path / "test_memory.db"))
    yield db
    db.close()


SESSION = "test-session-001"


# ── 单元测试：append / load ────────────────────────────────────────────────────

class TestAppendLoad:
    """测试基本写入和读取。"""

    def test_load_empty_session_returns_empty_list(self, store: ChatHistoryStore) -> None:
        assert store.load("nonexistent-session") == []

    def test_append_user_message(self, store: ChatHistoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "你好"})
        msgs = store.load(SESSION)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "你好"

    def test_append_assistant_message(self, store: ChatHistoryStore) -> None:
        store.append(SESSION, {"role": "assistant", "content": "你好，有什么可以帮助你？"})
        msgs = store.load(SESSION)
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "你好，有什么可以帮助你？"

    def test_append_multiple_messages_preserves_order(self, store: ChatHistoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "第一条"})
        store.append(SESSION, {"role": "assistant", "content": "第一个回答"})
        store.append(SESSION, {"role": "user", "content": "第二条"})
        msgs = store.load(SESSION)
        assert len(msgs) == 3
        assert msgs[0]["content"] == "第一条"
        assert msgs[1]["content"] == "第一个回答"
        assert msgs[2]["content"] == "第二条"

    def test_sessions_are_isolated(self, store: ChatHistoryStore) -> None:
        store.append("session-A", {"role": "user", "content": "A的消息"})
        store.append("session-B", {"role": "user", "content": "B的消息"})
        assert len(store.load("session-A")) == 1
        assert len(store.load("session-B")) == 1
        assert store.load("session-A")[0]["content"] == "A的消息"


# ── 单元测试：tool_calls 序列化 ───────────────────────────────────────────────

class TestToolCallsSerialization:
    """测试 tool_calls 和 tool_call_id 的序列化/反序列化。"""

    def test_assistant_tool_calls_roundtrip(self, store: ChatHistoryStore) -> None:
        tool_calls = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "search_knowledge", "arguments": '{"query": "RAG"}'},
            }
        ]
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": "",
            "tool_calls": tool_calls,
        }
        store.append(SESSION, msg)
        loaded = store.load(SESSION)
        assert loaded[0]["tool_calls"] == tool_calls

    def test_tool_result_message_roundtrip(self, store: ChatHistoryStore) -> None:
        msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": "检索结果内容",
        }
        store.append(SESSION, msg)
        loaded = store.load(SESSION)
        assert loaded[0]["role"] == "tool"
        assert loaded[0]["tool_call_id"] == "call_abc"
        assert loaded[0]["content"] == "检索结果内容"

    def test_message_without_tool_calls_has_no_tool_calls_key(self, store: ChatHistoryStore) -> None:
        """普通 user/assistant 消息加载后不应携带 tool_calls 键（或为空列表）。"""
        store.append(SESSION, {"role": "user", "content": "普通消息"})
        loaded = store.load(SESSION)
        # tool_calls 为空列表时不传给 LLM（store.load 已过滤）
        assert "tool_calls" not in loaded[0] or loaded[0].get("tool_calls") == []


# ── 单元测试：clear ───────────────────────────────────────────────────────────

class TestClear:
    """测试清空 session。"""

    def test_clear_removes_all_messages(self, store: ChatHistoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "消息1"})
        store.append(SESSION, {"role": "assistant", "content": "回答1"})
        store.clear(SESSION)
        assert store.load(SESSION) == []

    def test_clear_removes_session_metadata(self, store: ChatHistoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "消息"})
        store.clear(SESSION)
        sessions = store.list_sessions()
        assert not any(s["session_id"] == SESSION for s in sessions)

    def test_clear_nonexistent_session_is_safe(self, store: ChatHistoryStore) -> None:
        """清空不存在的 session 不应抛异常。"""
        store.clear("does-not-exist")  # 不应 raise


# ── 单元测试：delete_session ──────────────────────────────────────────────────

class TestDeleteSession:
    """测试彻底删除指定历史 session。"""

    def test_delete_existing_session_returns_true(self, store: ChatHistoryStore) -> None:
        store.append("del-session", {"role": "user", "content": "待删除"})
        result = store.delete_session("del-session")
        assert result is True

    def test_delete_removes_all_messages(self, store: ChatHistoryStore) -> None:
        store.append("del-session", {"role": "user", "content": "消息1"})
        store.append("del-session", {"role": "assistant", "content": "回答1"})
        store.delete_session("del-session")
        assert store.load("del-session") == []

    def test_delete_removes_session_metadata(self, store: ChatHistoryStore) -> None:
        store.append("del-session", {"role": "user", "content": "消息"})
        store.delete_session("del-session")
        ids = [s["session_id"] for s in store.list_sessions()]
        assert "del-session" not in ids

    def test_delete_nonexistent_session_returns_false(self, store: ChatHistoryStore) -> None:
        result = store.delete_session("ghost-session")
        assert result is False

    def test_delete_nonexistent_session_is_safe(self, store: ChatHistoryStore) -> None:
        """删除不存在的 session 不应抛异常。"""
        store.delete_session("ghost-session")  # 不应 raise

    def test_delete_does_not_affect_other_sessions(self, store: ChatHistoryStore) -> None:
        store.append("keep-session", {"role": "user", "content": "保留"})
        store.append("del-session", {"role": "user", "content": "删除"})
        store.delete_session("del-session")
        assert store.load("del-session") == []
        assert len(store.load("keep-session")) == 1

    def test_delete_session_no_longer_in_list(self, store: ChatHistoryStore) -> None:
        store.append("s-a", {"role": "user", "content": "A"})
        store.append("s-b", {"role": "user", "content": "B"})
        store.delete_session("s-a")
        ids = [s["session_id"] for s in store.list_sessions()]
        assert "s-a" not in ids
        assert "s-b" in ids


# ── 单元测试：clean_all_sessions ──────────────────────────────────────────────

class TestCleanAllSessions:
    """测试清空所有 session。"""

    def test_clean_all_returns_correct_count(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "A"})
        store.append("s2", {"role": "user", "content": "B"})
        store.append("s3", {"role": "user", "content": "C"})
        count = store.clean_all_sessions()
        assert count == 3

    def test_clean_all_removes_all_messages(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "A"})
        store.append("s2", {"role": "user", "content": "B"})
        store.clean_all_sessions()
        assert store.load("s1") == []
        assert store.load("s2") == []

    def test_clean_all_removes_all_session_metadata(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "A"})
        store.append("s2", {"role": "user", "content": "B"})
        store.clean_all_sessions()
        assert store.list_sessions() == []

    def test_clean_all_on_empty_db_returns_zero(self, store: ChatHistoryStore) -> None:
        count = store.clean_all_sessions()
        assert count == 0

    def test_clean_all_on_empty_db_is_safe(self, store: ChatHistoryStore) -> None:
        """空库时调用不应抛异常。"""
        store.clean_all_sessions()  # 不应 raise


# ── 单元测试：list_sessions ───────────────────────────────────────────────────

class TestListSessions:
    """测试 session 列表查询。"""

    def test_list_sessions_empty(self, store: ChatHistoryStore) -> None:
        assert store.list_sessions() == []

    def test_list_sessions_returns_created_sessions(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "第一个 session"})
        store.append("s2", {"role": "user", "content": "第二个 session"})
        sessions = store.list_sessions()
        ids = [s["session_id"] for s in sessions]
        assert "s1" in ids
        assert "s2" in ids

    def test_list_sessions_includes_msg_count(self, store: ChatHistoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "消息1"})
        store.append(SESSION, {"role": "assistant", "content": "回答1"})
        sessions = store.list_sessions()
        assert sessions[0]["msg_count"] == 2

    def test_list_sessions_first_user_msg_is_recorded(self, store: ChatHistoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "我的第一个问题"})
        sessions = store.list_sessions()
        assert "我的第一个问题" in sessions[0]["first_user_msg"]


class TestListSessionsSearch:
    """测试 list_sessions(query, limit) 过滤行为。"""

    def _seed(self, store: ChatHistoryStore) -> None:
        store.append("abc12345-foo", {"role": "user", "content": "RAG 召回评测怎么做"})
        store.append("def67890-bar", {"role": "user", "content": "Agent ReAct 实现细节"})
        store.append("abc99999-baz", {"role": "user", "content": "LangChain 历史管理"})

    def test_query_none_returns_all(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        assert len(store.list_sessions(query=None)) == 3

    def test_query_empty_string_returns_all(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        assert len(store.list_sessions(query="")) == 3

    def test_query_matches_session_id_prefix(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        ids = {s["session_id"] for s in store.list_sessions(query="abc")}
        assert ids == {"abc12345-foo", "abc99999-baz"}

    def test_query_matches_first_user_msg_substring(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        ids = {s["session_id"] for s in store.list_sessions(query="ReAct")}
        assert ids == {"def67890-bar"}

    def test_query_first_msg_is_case_insensitive(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        ids_upper = {s["session_id"] for s in store.list_sessions(query="REACT")}
        ids_lower = {s["session_id"] for s in store.list_sessions(query="react")}
        assert ids_upper == ids_lower == {"def67890-bar"}

    def test_query_no_match_returns_empty(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        assert store.list_sessions(query="nonsense-xyz-zzz") == []

    def test_query_whitespace_only_is_treated_as_no_filter(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        assert len(store.list_sessions(query="   ")) == 3

    def test_limit_caps_results(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        assert len(store.list_sessions(limit=2)) == 2

    def test_limit_none_returns_all(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        assert len(store.list_sessions(limit=None)) == 3

    def test_limit_zero_treated_as_no_limit(self, store: ChatHistoryStore) -> None:
        """limit<=0 视为不限制（避免外部传 0 把列表截没）。"""
        self._seed(store)
        assert len(store.list_sessions(limit=0)) == 3

    def test_query_combined_with_limit(self, store: ChatHistoryStore) -> None:
        self._seed(store)
        result = store.list_sessions(query="abc", limit=1)
        assert len(result) == 1
        assert result[0]["session_id"].startswith("abc")


# ── 集成测试：跨 Agent 实例历史拼接 ──────────────────────────────────────────

class TestAgentMemoryIntegration:
    """测试 Agent 跨 run() 调用时历史消息正确拼接（需 mock LLM，不消耗 API）。"""

    def _make_text_response(self, content: str) -> Any:
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_second_run_includes_first_turn_in_messages(self, tmp_path: Path) -> None:
        """第二次 run() 时 messages 应包含第一轮的 user + assistant 历史。"""
        store = ChatHistoryStore(db_path=str(tmp_path / "agent_test.db"))
        agent = Agent(verbose=False, session_id="integ-001", chat_history=store)

        captured_messages: list[list[dict]] = []

        def mock_chat(messages, tools=None, **kwargs):
            captured_messages.append(list(messages))
            return self._make_text_response("回答")

        with patch("src.agent.agent.chat", side_effect=mock_chat):
            agent.run("第一个问题")
            agent.run("第二个问题")

        # 第二次调用时 messages 应包含第一轮历史
        second_call_messages = captured_messages[1]
        roles = [m["role"] for m in second_call_messages]
        assert roles.count("user") >= 2, "第二轮应包含历史 user 消息"
        contents = [m.get("content", "") for m in second_call_messages]
        assert "第一个问题" in contents
        assert "第二个问题" in contents

        store.close()

    def test_history_persists_across_agent_instances(self, tmp_path: Path) -> None:
        """新建 Agent 实例（模拟重启），历史应从 DB 中恢复。"""
        db_path = str(tmp_path / "persist_test.db")
        store1 = ChatHistoryStore(db_path=db_path)
        agent1 = Agent(verbose=False, session_id="persist-session", chat_history=store1)

        with patch("src.agent.agent.chat", return_value=self._make_text_response("第一轮回答")):
            agent1.run("第一轮问题")
        store1.close()

        # 模拟重启：新建 store 和 agent，使用同一 session_id
        store2 = ChatHistoryStore(db_path=db_path)
        captured: list[list[dict]] = []

        def mock_chat2(messages, tools=None, **kwargs):
            captured.append(list(messages))
            return self._make_text_response("第二轮回答")

        agent2 = Agent(verbose=False, session_id="persist-session", chat_history=store2)
        with patch("src.agent.agent.chat", side_effect=mock_chat2):
            agent2.run("第二轮问题")

        second_messages = captured[0]
        contents = [m.get("content", "") for m in second_messages]
        assert "第一轮问题" in contents, "重启后应恢复第一轮问题历史"
        assert "第一轮回答" in contents, "重启后应恢复第一轮回答历史"
        store2.close()


# ── 集成测试：超长历史截断 ────────────────────────────────────────────────────

class TestHistoryTruncation:
    """测试超长历史截断策略。"""

    def _make_text_response(self, content: str) -> Any:
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_truncation_limits_messages_passed_to_llm(self, tmp_path: Path) -> None:
        """当历史超过 max_history_turns 时，传给 LLM 的 messages 数量应受限。"""
        store = ChatHistoryStore(db_path=str(tmp_path / "trunc_test.db"))
        # max_history_turns=2：只保留最近 2 轮
        agent = Agent(verbose=False, session_id="trunc-session", chat_history=store, max_history_turns=2)

        captured: list[list[dict]] = []

        def mock_chat(messages, tools=None, **kwargs):
            captured.append(list(messages))
            return self._make_text_response("回答")

        with patch("src.agent.agent.chat", side_effect=mock_chat):
            for i in range(5):
                agent.run(f"第 {i+1} 个问题")

        # 第 5 次调用时：system(1) + 最近 2 轮 user+assistant(4) + 当前 user(1) = 6
        last_messages = captured[-1]
        user_msgs = [m for m in last_messages if m["role"] == "user"]
        # 最多 max_history_turns + 1（当前轮）个 user 消息
        assert len(user_msgs) <= agent.max_history_turns + 1

        store.close()


# ── 单元测试：load_last_n_messages SQL 层粗粒度过滤 ───────────────────────────

class TestLoadLastN:
    """测试 load_last_n_messages() SQL 层粗粒度过滤。"""

    def test_returns_empty_for_nonexistent_session(self, store: ChatHistoryStore) -> None:
        result = store.load_last_n_messages("no-such-session", 10)
        assert result == []

    def test_returns_all_when_n_exceeds_total(self, store: ChatHistoryStore) -> None:
        for i in range(3):
            store.append("s1", {"role": "user", "content": f"msg{i}"})
        result = store.load_last_n_messages("s1", 100)
        assert len(result) == 3

    def test_returns_last_n_in_chronological_order(self, store: ChatHistoryStore) -> None:
        for i in range(5):
            store.append("s2", {"role": "user", "content": f"msg{i}"})
        result = store.load_last_n_messages("s2", 3)
        assert len(result) == 3
        # 应为最后 3 条：msg2, msg3, msg4，且保持时间顺序
        assert result[0]["content"] == "msg2"
        assert result[1]["content"] == "msg3"
        assert result[2]["content"] == "msg4"

    def test_preserves_message_fields(self, store: ChatHistoryStore) -> None:
        store.append("s3", {"role": "user", "content": "hello"})
        result = store.load_last_n_messages("s3", 5)
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hello"

    def test_result_matches_tail_of_full_load(self, store: ChatHistoryStore) -> None:
        """load_last_n_messages(n) 结果应等于 load() 结果的末尾 n 条。"""
        for i in range(8):
            store.append("s4", {"role": "user", "content": f"turn{i}"})
        full = store.load("s4")
        last3 = store.load_last_n_messages("s4", 3)
        assert last3 == full[-3:]

    def test_isolates_sessions(self, store: ChatHistoryStore) -> None:
        """不同 session 的消息不应互相干扰。"""
        for i in range(4):
            store.append("sa", {"role": "user", "content": f"a{i}"})
        for i in range(4):
            store.append("sb", {"role": "user", "content": f"b{i}"})
        result_a = store.load_last_n_messages("sa", 2)
        result_b = store.load_last_n_messages("sb", 2)
        assert all(m["content"].startswith("a") for m in result_a)
        assert all(m["content"].startswith("b") for m in result_b)


# ── 单元测试：set_prompt_name ─────────────────────────────────────────────────

class TestSetPromptName:
    """测试 set_prompt_name() 持久化与 list_sessions() 返回 prompt_name 字段。"""

    def test_set_prompt_name_updates_value(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "hello"})
        store.set_prompt_name("s1", "5g-expert")
        sessions = {s["session_id"]: s for s in store.list_sessions()}
        assert sessions["s1"]["prompt_name"] == "5g-expert"

    def test_set_prompt_name_can_overwrite(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "hello"})
        store.set_prompt_name("s1", "5g-expert")
        store.set_prompt_name("s1", "code-assistant")
        sessions = {s["session_id"]: s for s in store.list_sessions()}
        assert sessions["s1"]["prompt_name"] == "code-assistant"

    def test_set_prompt_name_nonexistent_session_is_ignored(self, store: ChatHistoryStore) -> None:
        # session 不存在时不报错，list_sessions 结果不变
        store.set_prompt_name("no-such-session", "5g-expert")
        assert store.list_sessions() == []

    def test_list_sessions_includes_prompt_name_field(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "hello"})
        sessions = store.list_sessions()
        assert "prompt_name" in sessions[0]

    def test_list_sessions_prompt_name_default_is_empty(self, store: ChatHistoryStore) -> None:
        store.append("s1", {"role": "user", "content": "hello"})
        sessions = store.list_sessions()
        assert sessions[0]["prompt_name"] == ""
