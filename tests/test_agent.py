"""
测试：Agent 主控逻辑

测试内容：
    - Agent 初始化参数
    - ReAct 循环（mock LLM 响应，不消耗真实 API）
    - 工具调用流程（mock execute_tool）
    - 最大迭代次数保护
    - 真实端到端对话（integration）
    - 自定义 system_prompt 覆盖
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.agent.agent import Agent, MAX_TOOL_ROUNDS, MAX_TOTAL_ROUNDS, SYSTEM_PROMPT, TOOL_EMPTY_HINT
from src.agent.tools import ToolResult


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
        assert agent.max_iterations == MAX_TOTAL_ROUNDS
        assert agent.verbose is True

    def test_custom_init(self) -> None:
        agent = Agent(system_prompt="custom", max_iterations=3, verbose=False)
        assert agent.system_prompt == "custom"
        assert agent.max_iterations == 3
        assert agent.verbose is False

    def test_session_id_auto_generated(self) -> None:
        agent = Agent()
        assert isinstance(agent.session_id, str)
        assert len(agent.session_id) > 0

    def test_custom_session_id(self) -> None:
        agent = Agent(session_id="my-session")
        assert agent.session_id == "my-session"

    def test_default_max_history_turns(self) -> None:
        agent = Agent()
        assert agent.max_history_turns == 20


class TestAgentDirectReply:
    """测试 LLM 直接返回文本（不调用工具）的场景"""

    def test_run_returns_string(self) -> None:
        agent = Agent(verbose=False)
        with patch("src.agent.agent.chat", return_value=_make_text_response("这是回答")):
            result = agent.run("你好")
        assert isinstance(result, str)
        assert result == "这是回答"

    def test_run_strips_whitespace(self) -> None:
        agent = Agent(verbose=False)
        with patch("src.agent.agent.chat", return_value=_make_text_response("  回答有空白  \n")):
            result = agent.run("问题")
        assert result == "回答有空白"

    def test_run_empty_content_returns_fallback(self) -> None:
        agent = Agent(verbose=False)
        with patch("src.agent.agent.chat", return_value=_make_text_response("")):
            result = agent.run("问题")
        assert "抱歉" in result


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

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=ToolResult(status="ok", content="RAG 相关文档片段")):
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

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=ToolResult(status="ok", content="工具返回内容")):
            agent.run("测试问题")

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

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=ToolResult(status="ok", content="结果")):
            agent.run("问题")

        tool_messages = [m for m in captured if m.get("role") == "tool"]
        assert any(m.get("tool_call_id") == "call_xyz" for m in tool_messages)


class TestAgentMaxIterations:
    """测试最大迭代次数保护"""

    def test_max_iterations_returns_fallback(self) -> None:
        """LLM 一直调用工具超过上限，应返回 fallback 提示"""
        agent = Agent(max_iterations=3, verbose=False)

        with patch("src.agent.agent.chat",
                   return_value=_make_tool_call_response("search_knowledge", {"query": "q"})), \
             patch("src.agent.agent.execute_tool", return_value=ToolResult(status="ok", content="结果")):
            result = agent.run("无法结束的问题")

        assert "抱歉" in result or "规定轮次" in result

    def test_max_iterations_chat_called_correct_times(self) -> None:
        """chat() 的调用次数应等于 max_iterations"""
        agent = Agent(max_iterations=3, verbose=False)
        mock_chat = MagicMock(
            return_value=_make_tool_call_response("search_knowledge", {"query": "q"})
        )

        with patch("src.agent.agent.chat", mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=ToolResult(status="ok", content="结果")):
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


class TestSystemPromptWebSearch:
    """测试 SYSTEM_PROMPT 包含强制联网搜索策略和国内网站优先引导"""

    def test_system_prompt_has_mandatory_search_strategy(self) -> None:
        """SYSTEM_PROMPT 应包含强制搜索策略：必须、fetch_url、国外备选"""
        assert "必须" in SYSTEM_PROMPT, "SYSTEM_PROMPT 应包含'必须'强制策略"
        assert "fetch_url" in SYSTEM_PROMPT
        assert "国外" in SYSTEM_PROMPT, "SYSTEM_PROMPT 应提及国外网站作为备选"

    def test_system_prompt_has_domestic_site_guidance(self) -> None:
        """SYSTEM_PROMPT 应包含国内优先引导和至少 3 个国内网站"""
        assert "国内" in SYSTEM_PROMPT, "SYSTEM_PROMPT 应包含'国内'字样"
        domestic_keywords = ["xinhuanet", "baidu", "zhihu", "segmentfault", "csdn", "people"]
        matched = [kw for kw in domestic_keywords if kw in SYSTEM_PROMPT]
        assert len(matched) >= 3, f"SYSTEM_PROMPT 应列出至少3个国内网站，实际匹配：{matched}"

    def test_agent_calls_fetch_url_when_knowledge_empty(self) -> None:
        """知识库返回空时，Agent 应主动调用 fetch_url（通过 mock 验证）"""
        agent = Agent(verbose=False)
        call_count = 0

        def mock_chat(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_tool_call_response("search_knowledge", {"query": "最新AI新闻"})
            if call_count == 2:
                return _make_tool_call_response(
                    "fetch_url", {"url": "https://news.baidu.com"}, call_id="call_fetch_001"
                )
            return _make_text_response("根据百度新闻，最新AI动态如下……")

        tool_calls: list[str] = []

        def mock_execute_tool(name: str, args: dict, skill_bodies: dict | None = None) -> ToolResult:
            tool_calls.append(name)
            if name == "search_knowledge":
                return ToolResult(status="empty", content="知识库为空，未找到相关内容。")
            if name == "fetch_url":
                return ToolResult(status="ok", content="百度新闻页面内容：AI大模型新动态……")
            return ToolResult(status="error", content="未知工具")

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", side_effect=mock_execute_tool):
            result = agent.run("最新AI新闻是什么？")

        assert "fetch_url" in tool_calls, "知识库为空时，Agent 应调用 fetch_url"
        assert result == "根据百度新闻，最新AI动态如下……"

    @pytest.mark.integration
    def test_integration_web_search_triggered_by_unknown_topic(self) -> None:
        """集成测试：询问知识库中没有的实时信息时，Agent 应主动联网搜索"""
        agent = Agent(verbose=True)
        result = agent.run("今天的天气怎么样？请上网查一下。")
        assert isinstance(result, str)
        assert len(result) > 10


class TestToolGuidance:
    """测试 ToolResult 引导文字注入与工具轮次分层保护"""

    def test_error_result_appends_hint_in_tool_message(self) -> None:
        """工具返回 error 时，写入 messages 的 content 应含 [提示] 引导文字"""
        agent = Agent(verbose=False)
        captured_tool_messages: list[dict] = []

        def mock_chat(messages, tools=None, **kwargs):
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "tool":
                    captured_tool_messages.append(m)
            if not captured_tool_messages:
                return _make_tool_call_response("fetch_url", {"url": "https://example.com"})
            return _make_text_response("无法获取信息。")

        error_result = ToolResult(status="error", content="请求超时（15s），URL: https://example.com")

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=error_result):
            agent.run("查询某网页")

        assert captured_tool_messages, "应有 tool role 消息"
        tool_content = captured_tool_messages[0]["content"]
        assert "[工具失败]" in tool_content
        assert "[提示]" in tool_content
        assert "换一种方式" in tool_content

    def test_empty_knowledge_appends_tool_empty_hint(self) -> None:
        """search_knowledge 返回 empty 时，tool message 应含 TOOL_EMPTY_HINT"""
        agent = Agent(verbose=False)
        captured_tool_messages: list[dict] = []

        def mock_chat(messages, tools=None, **kwargs):
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "tool":
                    captured_tool_messages.append(m)
            if not captured_tool_messages:
                return _make_tool_call_response("search_knowledge", {"query": "未知主题"})
            return _make_text_response("当前无法获取相关信息。")

        empty_result = ToolResult(status="empty", content="知识库为空，未找到相关内容。")

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=empty_result):
            agent.run("未知主题")

        assert captured_tool_messages
        tool_content = captured_tool_messages[0]["content"]
        assert "[结果为空]" in tool_content
        assert TOOL_EMPTY_HINT.strip() in tool_content

    def test_ok_result_has_no_hint(self) -> None:
        """工具返回 ok 时，tool message 不应含 [提示] 引导文字"""
        agent = Agent(verbose=False)
        captured_tool_messages: list[dict] = []

        def mock_chat(messages, tools=None, **kwargs):
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "tool":
                    captured_tool_messages.append(m)
            if not captured_tool_messages:
                return _make_tool_call_response("search_knowledge", {"query": "RAG"})
            return _make_text_response("这是回答。")

        ok_result = ToolResult(status="ok", content="[1] 来源: doc.txt\nRAG 相关内容")

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=ok_result):
            agent.run("什么是 RAG？")

        assert captured_tool_messages
        tool_content = captured_tool_messages[0]["content"]
        assert "[提示]" not in tool_content
        assert "[工具失败]" not in tool_content

    def test_tool_rounds_limit_disables_tools_in_chat(self) -> None:
        """tool_rounds 达到 MAX_TOOL_ROUNDS 时，chat() 应以 tools=None 调用"""
        agent = Agent(verbose=False)
        chat_calls: list[Any] = []

        def mock_chat(messages, tools=None, **kwargs):
            chat_calls.append(tools)
            if tools is not None:
                return _make_tool_call_response("search_knowledge", {"query": "q"})
            return _make_text_response("最终回答")

        ok_result = ToolResult(status="ok", content="结果内容")

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.agent.execute_tool", return_value=ok_result):
            result = agent.run("测试工具轮次上限")

        assert chat_calls[-1] is None, "达到工具轮次上限后应以 tools=None 调用 chat()"
        assert result == "最终回答"


class TestCustomSystemPrompt:
    """测试 Agent 支持外部传入自定义 system_prompt 覆盖默认 SYSTEM_PROMPT。"""

    def test_custom_system_prompt_replaces_default(self) -> None:
        """传入 system_prompt 时，LLM 收到的 messages[0] 内容应为自定义内容。"""
        custom = "你是一位 5G 通信专家助手。"
        agent = Agent(verbose=False, system_prompt=custom)
        captured_messages: list[list[dict]] = []

        def mock_chat(messages, tools=None, **kwargs):
            captured_messages.append(list(messages))
            return _make_text_response("5G 回答")

        with patch("src.agent.agent.chat", side_effect=mock_chat):
            agent.run("什么是 5G？")

        assert captured_messages, "chat() 应被调用"
        system_msg = captured_messages[0][0]
        assert system_msg["role"] == "system"
        assert system_msg["content"] == custom

    def test_default_system_prompt_used_when_none_passed(self) -> None:
        """不传 system_prompt 时，LLM 收到的 messages[0] 应为默认 SYSTEM_PROMPT。"""
        agent = Agent(verbose=False)
        captured_messages: list[list[dict]] = []

        def mock_chat(messages, tools=None, **kwargs):
            captured_messages.append(list(messages))
            return _make_text_response("默认回答")

        with patch("src.agent.agent.chat", side_effect=mock_chat):
            agent.run("测试默认提示")

        system_msg = captured_messages[0][0]
        assert system_msg["role"] == "system"
        assert system_msg["content"] == SYSTEM_PROMPT
