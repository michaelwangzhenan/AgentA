"""
测试对话记忆模块（memory/store.py）

测试内容：
    - MemoryStore 基本 CRUD：append / load / clear / list_sessions
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

from src.memory.store import MemoryStore
from src.agent.agent import Agent


# ── 辅助 fixture ──────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    """每个测试用独立临时 DB，互不影响。"""
    db = MemoryStore(db_path=str(tmp_path / "test_memory.db"))
    yield db
    db.close()


SESSION = "test-session-001"


# ── 单元测试：append / load ────────────────────────────────────────────────────

class TestAppendLoad:
    """测试基本写入和读取。"""

    def test_load_empty_session_returns_empty_list(self, store: MemoryStore) -> None:
        assert store.load("nonexistent-session") == []

    def test_append_user_message(self, store: MemoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "你好"})
        msgs = store.load(SESSION)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "你好"

    def test_append_assistant_message(self, store: MemoryStore) -> None:
        store.append(SESSION, {"role": "assistant", "content": "你好，有什么可以帮助你？"})
        msgs = store.load(SESSION)
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "你好，有什么可以帮助你？"

    def test_append_multiple_messages_preserves_order(self, store: MemoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "第一条"})
        store.append(SESSION, {"role": "assistant", "content": "第一个回答"})
        store.append(SESSION, {"role": "user", "content": "第二条"})
        msgs = store.load(SESSION)
        assert len(msgs) == 3
        assert msgs[0]["content"] == "第一条"
        assert msgs[1]["content"] == "第一个回答"
        assert msgs[2]["content"] == "第二条"

    def test_sessions_are_isolated(self, store: MemoryStore) -> None:
        store.append("session-A", {"role": "user", "content": "A的消息"})
        store.append("session-B", {"role": "user", "content": "B的消息"})
        assert len(store.load("session-A")) == 1
        assert len(store.load("session-B")) == 1
        assert store.load("session-A")[0]["content"] == "A的消息"


# ── 单元测试：tool_calls 序列化 ───────────────────────────────────────────────

class TestToolCallsSerialization:
    """测试 tool_calls 和 tool_call_id 的序列化/反序列化。"""

    def test_assistant_tool_calls_roundtrip(self, store: MemoryStore) -> None:
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

    def test_tool_result_message_roundtrip(self, store: MemoryStore) -> None:
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

    def test_message_without_tool_calls_has_no_tool_calls_key(self, store: MemoryStore) -> None:
        """普通 user/assistant 消息加载后不应携带 tool_calls 键（或为空列表）。"""
        store.append(SESSION, {"role": "user", "content": "普通消息"})
        loaded = store.load(SESSION)
        # tool_calls 为空列表时不传给 LLM（store.load 已过滤）
        assert "tool_calls" not in loaded[0] or loaded[0].get("tool_calls") == []


# ── 单元测试：clear ───────────────────────────────────────────────────────────

class TestClear:
    """测试清空 session。"""

    def test_clear_removes_all_messages(self, store: MemoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "消息1"})
        store.append(SESSION, {"role": "assistant", "content": "回答1"})
        store.clear(SESSION)
        assert store.load(SESSION) == []

    def test_clear_removes_session_metadata(self, store: MemoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "消息"})
        store.clear(SESSION)
        sessions = store.list_sessions()
        assert not any(s["session_id"] == SESSION for s in sessions)

    def test_clear_nonexistent_session_is_safe(self, store: MemoryStore) -> None:
        """清空不存在的 session 不应抛异常。"""
        store.clear("does-not-exist")  # 不应 raise


# ── 单元测试：delete_session ──────────────────────────────────────────────────

class TestDeleteSession:
    """测试彻底删除指定历史 session。"""

    def test_delete_existing_session_returns_true(self, store: MemoryStore) -> None:
        store.append("del-session", {"role": "user", "content": "待删除"})
        result = store.delete_session("del-session")
        assert result is True

    def test_delete_removes_all_messages(self, store: MemoryStore) -> None:
        store.append("del-session", {"role": "user", "content": "消息1"})
        store.append("del-session", {"role": "assistant", "content": "回答1"})
        store.delete_session("del-session")
        assert store.load("del-session") == []

    def test_delete_removes_session_metadata(self, store: MemoryStore) -> None:
        store.append("del-session", {"role": "user", "content": "消息"})
        store.delete_session("del-session")
        ids = [s["session_id"] for s in store.list_sessions()]
        assert "del-session" not in ids

    def test_delete_nonexistent_session_returns_false(self, store: MemoryStore) -> None:
        result = store.delete_session("ghost-session")
        assert result is False

    def test_delete_nonexistent_session_is_safe(self, store: MemoryStore) -> None:
        """删除不存在的 session 不应抛异常。"""
        store.delete_session("ghost-session")  # 不应 raise

    def test_delete_does_not_affect_other_sessions(self, store: MemoryStore) -> None:
        store.append("keep-session", {"role": "user", "content": "保留"})
        store.append("del-session", {"role": "user", "content": "删除"})
        store.delete_session("del-session")
        assert store.load("del-session") == []
        assert len(store.load("keep-session")) == 1

    def test_delete_session_no_longer_in_list(self, store: MemoryStore) -> None:
        store.append("s-a", {"role": "user", "content": "A"})
        store.append("s-b", {"role": "user", "content": "B"})
        store.delete_session("s-a")
        ids = [s["session_id"] for s in store.list_sessions()]
        assert "s-a" not in ids
        assert "s-b" in ids


# ── 单元测试：clean_all_sessions ──────────────────────────────────────────────

class TestCleanAllSessions:
    """测试清空所有 session。"""

    def test_clean_all_returns_correct_count(self, store: MemoryStore) -> None:
        store.append("s1", {"role": "user", "content": "A"})
        store.append("s2", {"role": "user", "content": "B"})
        store.append("s3", {"role": "user", "content": "C"})
        count = store.clean_all_sessions()
        assert count == 3

    def test_clean_all_removes_all_messages(self, store: MemoryStore) -> None:
        store.append("s1", {"role": "user", "content": "A"})
        store.append("s2", {"role": "user", "content": "B"})
        store.clean_all_sessions()
        assert store.load("s1") == []
        assert store.load("s2") == []

    def test_clean_all_removes_all_session_metadata(self, store: MemoryStore) -> None:
        store.append("s1", {"role": "user", "content": "A"})
        store.append("s2", {"role": "user", "content": "B"})
        store.clean_all_sessions()
        assert store.list_sessions() == []

    def test_clean_all_on_empty_db_returns_zero(self, store: MemoryStore) -> None:
        count = store.clean_all_sessions()
        assert count == 0

    def test_clean_all_on_empty_db_is_safe(self, store: MemoryStore) -> None:
        """空库时调用不应抛异常。"""
        store.clean_all_sessions()  # 不应 raise


# ── 单元测试：list_sessions ───────────────────────────────────────────────────

class TestListSessions:
    """测试 session 列表查询。"""

    def test_list_sessions_empty(self, store: MemoryStore) -> None:
        assert store.list_sessions() == []

    def test_list_sessions_returns_created_sessions(self, store: MemoryStore) -> None:
        store.append("s1", {"role": "user", "content": "第一个 session"})
        store.append("s2", {"role": "user", "content": "第二个 session"})
        sessions = store.list_sessions()
        ids = [s["session_id"] for s in sessions]
        assert "s1" in ids
        assert "s2" in ids

    def test_list_sessions_includes_msg_count(self, store: MemoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "消息1"})
        store.append(SESSION, {"role": "assistant", "content": "回答1"})
        sessions = store.list_sessions()
        assert sessions[0]["msg_count"] == 2

    def test_list_sessions_first_user_msg_is_recorded(self, store: MemoryStore) -> None:
        store.append(SESSION, {"role": "user", "content": "我的第一个问题"})
        sessions = store.list_sessions()
        assert "我的第一个问题" in sessions[0]["first_user_msg"]


# ── 集成测试：跨 Agent 实例历史拼接 ──────────────────────────────────────────

class TestAgentMemoryIntegration:
    """测试 Agent 跨 run() 调用时历史消息正确拼接（需 mock LLM，不消耗 API）。"""

    def _make_text_response(self, content: str) -> Any:
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_second_run_includes_first_turn_in_messages(self, tmp_path: Path) -> None:
        """第二次 run() 时 messages 应包含第一轮的 user + assistant 历史。"""
        store = MemoryStore(db_path=str(tmp_path / "agent_test.db"))
        agent = Agent(verbose=False, session_id="integ-001", memory=store)

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
        store1 = MemoryStore(db_path=db_path)
        agent1 = Agent(verbose=False, session_id="persist-session", memory=store1)

        with patch("src.agent.agent.chat", return_value=self._make_text_response("第一轮回答")):
            agent1.run("第一轮问题")
        store1.close()

        # 模拟重启：新建 store 和 agent，使用同一 session_id
        store2 = MemoryStore(db_path=db_path)
        captured: list[list[dict]] = []

        def mock_chat2(messages, tools=None, **kwargs):
            captured.append(list(messages))
            return self._make_text_response("第二轮回答")

        agent2 = Agent(verbose=False, session_id="persist-session", memory=store2)
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
        store = MemoryStore(db_path=str(tmp_path / "trunc_test.db"))
        # max_history_turns=2：只保留最近 2 轮
        agent = Agent(verbose=False, session_id="trunc-session", memory=store, max_history_turns=2)

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
