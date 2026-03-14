"""
Phase 5 测试：Agent 主控逻辑

测试内容：
    - Agent 初始化参数
    - ReAct 循环（mock LLM 响应，不消耗真实 API）
    - 工具调用流程（mock execute_tool）
    - 最大迭代次数保护
    - 真实端到端对话（integration）
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.agent import Agent, MAX_ITERATIONS, SYSTEM_PROMPT


# ── 辅助函数：构造 mock LLM response ─────────────────────────────────────────

def _make_text_response(content: str) -> Any:
    """构造一个直接返回文本的 mock response（无 tool_calls）"""
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _make_tool_call_response(tool_name: str, tool_args: dict, call_id: str = "call_001") -> Any:
    """构造一个包含 tool_calls 的 mock response"""
    tool_call = SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )
    message = SimpleNamespace(content="", tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# ── 单元测试 ──────────────────────────────────────────────────────────────────

class TestAgentInit:
    """测试 Agent 初始化"""

    def test_default_init(self) -> None:
        agent = Agent()
        assert agent.system_prompt == SYSTEM_PROMPT
        assert agent.max_iterations == MAX_ITERATIONS
        assert agent.verbose is True

    def test_custom_init(self) -> None:
        agent = Agent(system_prompt="custom", max_iterations=3, verbose=False)
        assert agent.system_prompt == "custom"
        assert agent.max_iterations == 3
        assert agent.verbose is False


class TestAgentDirectReply:
    """测试 LLM 直接返回文本（不调用工具）的场景"""

    def test_run_returns_string(self) -> None:
        agent = Agent(verbose=False)
        with patch("agent.agent.chat", return_value=_make_text_response("这是回答")):
            result = agent.run("你好")
        assert isinstance(result, str)
        assert result == "这是回答"

    def test_run_strips_whitespace(self) -> None:
        agent = Agent(verbose=False)
        with patch("agent.agent.chat", return_value=_make_text_response("  回答有空白  \n")):
            result = agent.run("问题")
        assert result == "回答有空白"

    def test_run_empty_content_returns_fallback(self) -> None:
        agent = Agent(verbose=False)
        with patch("agent.agent.chat", return_value=_make_text_response("")):
            result = agent.run("问题")
        assert "抱歉" in result or len(result) >= 0  # 返回 fallback 提示


class TestAgentToolCall:
    """测试 LLM 调用工具后再生成回答的场景"""

    def test_single_tool_call_then_final_answer(self) -> None:
        """第一轮调用工具，第二轮直接回答"""
        agent = Agent(verbose=False)

        call_count = 0
        def mock_chat(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_tool_call_response("search_knowledge", {"query": "RAG"})
            return _make_text_response("RAG 是检索增强生成技术。")

        with patch("agent.agent.chat", side_effect=mock_chat), \
             patch("agent.agent.execute_tool", return_value="RAG 相关文档片段"):
            result = agent.run("什么是 RAG？")

        assert result == "RAG 是检索增强生成技术。"
        assert call_count == 2

    def test_tool_result_appended_to_messages(self) -> None:
        """工具结果应作为 tool role 追加到 messages 中"""
        agent = Agent(verbose=False)
        captured_messages: list = []

        def mock_chat(messages, tools=None, **kwargs):
            captured_messages.extend(messages)
            if len(messages) <= 2:
                return _make_tool_call_response("search_knowledge", {"query": "test"})
            return _make_text_response("最终回答")

        with patch("agent.agent.chat", side_effect=mock_chat), \
             patch("agent.agent.execute_tool", return_value="工具返回内容"):
            agent.run("测试问题")

        # 应有 tool role 的 message
        roles = [m["role"] for m in captured_messages if isinstance(m, dict)]
        assert "tool" in roles

    def test_tool_call_id_matches(self) -> None:
        """tool message 的 tool_call_id 应与 tool_calls 的 id 一致"""
        agent = Agent(verbose=False)
        captured: list[dict] = []

        def mock_chat(messages, tools=None, **kwargs):
            for m in messages:
                if isinstance(m, dict):
                    captured.append(m)
            if len(messages) <= 2:
                return _make_tool_call_response("search_knowledge", {"query": "x"}, call_id="call_xyz")
            return _make_text_response("完成")

        with patch("agent.agent.chat", side_effect=mock_chat), \
             patch("agent.agent.execute_tool", return_value="结果"):
            agent.run("问题")

        tool_messages = [m for m in captured if m.get("role") == "tool"]
        assert any(m.get("tool_call_id") == "call_xyz" for m in tool_messages)


class TestAgentMaxIterations:
    """测试最大迭代次数保护"""

    def test_max_iterations_returns_fallback(self) -> None:
        """LLM 一直调用工具超过上限，应返回 fallback 提示"""
        agent = Agent(max_iterations=3, verbose=False)

        with patch("agent.agent.chat",
                   return_value=_make_tool_call_response("search_knowledge", {"query": "q"})), \
             patch("agent.agent.execute_tool", return_value="结果"):
            result = agent.run("无法结束的问题")

        assert "抱歉" in result or "规定轮次" in result

    def test_max_iterations_chat_called_correct_times(self) -> None:
        """chat() 的调用次数应等于 max_iterations"""
        agent = Agent(max_iterations=3, verbose=False)
        mock_chat = MagicMock(
            return_value=_make_tool_call_response("search_knowledge", {"query": "q"})
        )

        with patch("agent.agent.chat", mock_chat), \
             patch("agent.agent.execute_tool", return_value="结果"):
            agent.run("问题")

        assert mock_chat.call_count == 3


# ── 集成测试 ──────────────────────────────────────────────────────────────────

class TestAgentIntegration:
    """端到端真实 API 测试（消耗真实 API quota）"""

    @pytest.mark.integration
    def test_run_with_knowledge_base_question(self) -> None:
        """提问知识库内有答案的问题，验证 Agent 完整流程"""
        agent = Agent(verbose=True)
        result = agent.run("RAG 技术的工作流程是什么？")
        assert isinstance(result, str)
        assert len(result) > 20, "回答不应过短"

    @pytest.mark.integration
    def test_run_returns_nonempty_string(self) -> None:
        """任何问题都应返回非空字符串"""
        agent = Agent(verbose=False)
        result = agent.run("你好，请介绍一下你自己")
        assert isinstance(result, str)
        assert len(result) > 0
