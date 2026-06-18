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

from src.agent.agent import Agent, SYSTEM_PROMPT
from src.config import MAX_TOTAL_ROUNDS
from src.agent.core.tool_call_engine import TOOL_EMPTY_HINT
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
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=ToolResult(status="ok", content="RAG 相关文档片段")):
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
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=ToolResult(status="ok", content="工具返回内容")):
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
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=ToolResult(status="ok", content="结果")):
            agent.run("问题")

        tool_messages = [m for m in captured if m.get("role") == "tool"]
        assert any(m.get("tool_call_id") == "call_xyz" for m in tool_messages)


class TestAgentPlanExecuteE2E:
    """Phase 2.1 e2e：完整 plan 生命周期跑通（make_plan → 业务 tool × N → update_step × N → final）。

    覆盖联动：
    - Agent loop plan-aware 循环上限自适应未触发硬上限（plan 2 步 → eff_tool 10 ≥ 实际 6 轮）
    - ToolCallEngine 按真实 plan tool 实现执行 + 发 plan_* 事件
    - plan_manager.reconstruct_from_messages 在每次 update_step 内被调用并给出正确进度
    - 最终 events 序列顺序：info → tool_call_start/end × 5 → plan_created → plan_step_start ×2 → plan_step_end × 2 → final_answer
    """

    def _route_real_plan_tools(self, search_returns: dict[str, str] | None = None):
        """返回 execute_tool mock：search_knowledge 走假 hit，plan tools 走真实实现。"""
        from src.agent.tools import execute_tool as real_execute_tool
        hits = search_returns or {}

        def mock_exec(name, args, *a, **kw):
            if name == "search_knowledge":
                q = args.get("query", "")
                return ToolResult(status="ok", content=hits.get(q, f"假装找到 {q}"))
            return real_execute_tool(name, args, *a, **kw)

        return mock_exec

    def test_two_step_plan_full_lifecycle(self) -> None:
        from src.agent.core.event_bus import (
            EVENT_FINAL_ANSWER,
            EVENT_INFO,
            EVENT_PLAN_CREATED,
            EVENT_PLAN_STEP_END,
            EVENT_PLAN_STEP_START,
            EVENT_TOOL_CALL_END,
            EVENT_TOOL_CALL_START,
            AgentEvent,
        )

        agent = Agent(verbose=False)
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        # 6 轮 LLM 响应脚本
        responses = [
            _make_tool_call_response("make_plan", {"steps": ["查 RAG", "查 Agent"]}, call_id="mp1"),
            _make_tool_call_response("search_knowledge", {"query": "RAG"}, call_id="s1"),
            _make_tool_call_response(
                "update_step", {"step_id": 1, "status": "success", "note": "查到 RAG"}, call_id="u1",
            ),
            _make_tool_call_response("search_knowledge", {"query": "Agent"}, call_id="s2"),
            _make_tool_call_response(
                "update_step", {"step_id": 2, "status": "success", "note": "查到 Agent"}, call_id="u2",
            ),
            _make_text_response("RAG 是检索增强；Agent 是自主推理工具。"),
        ]
        chat_calls: list[int] = []

        def fake_chat(messages, tools=None, **kwargs):
            chat_calls.append(len(messages))
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat), \
             patch(
                "src.agent.core.tool_call_engine.execute_tool",
                side_effect=self._route_real_plan_tools(),
             ):
            answer = agent.run("对比 RAG 和 Agent")

        assert answer.startswith("RAG 是检索增强")
        assert len(chat_calls) == 6

        # 事件序列：去重后必须包含 plan 全套
        types = [e.type for e in captured]
        # 顺序约束：plan_created 在 make_plan 的 tool_call_end 后；2 个 plan_step_start；2 个 plan_step_end
        assert types[0] == EVENT_INFO
        assert types[-1] == EVENT_FINAL_ANSWER
        assert types.count(EVENT_TOOL_CALL_START) == 5
        assert types.count(EVENT_TOOL_CALL_END) == 5
        assert types.count(EVENT_PLAN_CREATED) == 1
        assert types.count(EVENT_PLAN_STEP_START) == 2
        assert types.count(EVENT_PLAN_STEP_END) == 2

        # plan_step_start 顺序：step 1 在 step 2 之前
        step_starts = [e for e in captured if e.type == EVENT_PLAN_STEP_START]
        assert [e.payload["step_id"] for e in step_starts] == [1, 2]
        step_ends = [e for e in captured if e.type == EVENT_PLAN_STEP_END]
        assert [e.payload["step_id"] for e in step_ends] == [1, 2]
        assert [e.payload["status"] for e in step_ends] == ["success", "success"]

        # plan_created payload 含全部步骤
        plan_created = next(e for e in captured if e.type == EVENT_PLAN_CREATED)
        assert [s["text"] for s in plan_created.payload["steps"]] == ["查 RAG", "查 Agent"]

    def test_plan_with_failed_step_then_recovers(self) -> None:
        """中间步骤标记 failed，LLM 决策跳过到下一步，plan 仍可完成。"""
        agent = Agent(verbose=False)

        responses = [
            _make_tool_call_response("make_plan", {"steps": ["a", "b", "c"]}, call_id="mp1"),
            _make_tool_call_response("search_knowledge", {"query": "a"}, call_id="s1"),
            _make_tool_call_response(
                "update_step", {"step_id": 1, "status": "failed", "note": "查无结果"}, call_id="u1",
            ),
            _make_tool_call_response(
                "update_step", {"step_id": 2, "status": "skipped", "note": "依赖前置"}, call_id="u2",
            ),
            _make_tool_call_response(
                "update_step", {"step_id": 3, "status": "success"}, call_id="u3",
            ),
            _make_text_response("基于有限信息，给出部分答案。"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat), \
             patch(
                "src.agent.core.tool_call_engine.execute_tool",
                side_effect=self._route_real_plan_tools(),
             ):
            answer = agent.run("尝试")

        assert "部分答案" in answer

    def test_plan_aborted_mid_way_returns_final_text(self) -> None:
        """active plan 中途 abort_plan，Agent 仍正常出最终答（不卡死）。"""
        agent = Agent(verbose=False)

        responses = [
            _make_tool_call_response("make_plan", {"steps": ["a", "b"]}, call_id="mp1"),
            _make_tool_call_response("search_knowledge", {"query": "a"}, call_id="s1"),
            _make_tool_call_response(
                "abort_plan", {"reason": "依赖数据缺失"}, call_id="ab1",
            ),
            _make_text_response("无法继续，原因：依赖数据缺失。"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat), \
             patch(
                "src.agent.core.tool_call_engine.execute_tool",
                side_effect=self._route_real_plan_tools(),
             ):
            answer = agent.run("尝试")

        assert "依赖数据缺失" in answer


class TestAgentMaxIterations:
    """测试最大迭代次数保护"""

    def test_max_iterations_returns_fallback(self) -> None:
        """LLM 一直调用工具超过上限，应返回 fallback 提示"""
        agent = Agent(max_iterations=3, verbose=False)

        with patch("src.agent.agent.chat",
                   return_value=_make_tool_call_response("search_knowledge", {"query": "q"})), \
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=ToolResult(status="ok", content="结果")):
            result = agent.run("无法结束的问题")

        assert "抱歉" in result or "规定轮次" in result

    def test_max_iterations_chat_called_correct_times(self) -> None:
        """chat() 的调用次数应等于 max_iterations"""
        agent = Agent(max_iterations=3, verbose=False)
        mock_chat = MagicMock(
            return_value=_make_tool_call_response("search_knowledge", {"query": "q"})
        )

        with patch("src.agent.agent.chat", mock_chat), \
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=ToolResult(status="ok", content="结果")):
            agent.run("问题")

        assert mock_chat.call_count == 3


# ── 集成测试 ──────────────────────────────────────────────────────────────────

class TestAgentIntegration:
    """端到端真实 API 测试（消耗真实 API quota）"""

    @pytest.mark.integration
    def test_run_returns_nonempty_string(self) -> None:
        """任何问题都应返回非空字符串"""
        agent = Agent(verbose=False)
        result = agent.run("你好，请介绍一下你自己")
        assert isinstance(result, str)
        assert len(result) > 0


class TestSystemPromptWebSearch:
    """SYSTEM_PROMPT 字面 string-match 测试已删除（脆性高、prompt 迭代受阻；
    业务策略改由 RAG 评估 / Agent 评估端到端验证）。本类只保留行为级测试。"""

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

        def mock_execute_tool(
            name: str,
            args: dict,
            skill_bodies: dict | None = None,
            **kwargs,  # 容纳 Phase 1.4 新增的 citation_builder 等关键字参数
        ) -> ToolResult:
            tool_calls.append(name)
            if name == "search_knowledge":
                return ToolResult(status="empty", content="知识库为空，未找到相关内容。")
            if name == "fetch_url":
                return ToolResult(status="ok", content="百度新闻页面内容：AI大模型新动态……")
            return ToolResult(status="error", content="未知工具")

        with patch("src.agent.agent.chat", side_effect=mock_chat), \
             patch("src.agent.core.tool_call_engine.execute_tool", side_effect=mock_execute_tool):
            result = agent.run("最新AI新闻是什么？")

        assert "fetch_url" in tool_calls, "知识库为空时，Agent 应调用 fetch_url"
        assert result == "根据百度新闻，最新AI动态如下……"


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
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=error_result):
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
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=empty_result):
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
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=ok_result):
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
             patch("src.agent.core.tool_call_engine.execute_tool", return_value=ok_result):
            result = agent.run("测试工具轮次上限")

        assert chat_calls[-1] is None, "达到工具轮次上限后应以 tools=None 调用 chat()"
        assert result == "最终回答"


class TestCustomSystemPrompt:
    """测试 Agent 支持外部传入自定义 system_prompt 覆盖默认 SYSTEM_PROMPT。"""

    @pytest.fixture(autouse=True)
    def _isolate_user_rules(self, monkeypatch):
        """隔离当前用户的 rules，否则数据库里真有 rules 时会把 <user_rules>
        块拼到 system_prompt 末尾，破坏本类"裸 system_prompt"的断言。"""
        monkeypatch.setattr("src.agent.agent._get_active_rules", lambda: None)

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


# ── thinking / token_usage / activate_skill 新增测试 ─────────────────────────

class TestAgentThinkingInit:
    """测试 ThinkingConfig 初始化行为"""

    def test_thinking_defaults_from_config(self) -> None:
        """未传参时，thinking_cfg 应取 config 默认值。"""
        import src.config as _cfg
        agent = Agent()
        assert agent.thinking_cfg.enabled == _cfg.THINKING_ENABLED
        assert agent.thinking_cfg.budget == _cfg.THINKING_BUDGET

    def test_thinking_explicit_true_overrides_config(self) -> None:
        """显式传入 ThinkingConfig 时，应优先于 config。"""
        from src.agent.agent import ThinkingConfig
        cfg = ThinkingConfig(enabled=True, budget=16000)
        agent = Agent(thinking_config=cfg)
        assert agent.thinking_cfg.enabled is True
        assert agent.thinking_cfg.budget == 16000

    def test_thinking_explicit_false_overrides_config(self) -> None:
        """显式传入 False 时，enabled 应为 False。"""
        from src.agent.agent import ThinkingConfig
        cfg = ThinkingConfig(enabled=False, budget=1024)
        agent = Agent(thinking_config=cfg)
        assert agent.thinking_cfg.enabled is False
        assert agent.thinking_cfg.budget == 1024

    def test_last_usage_initially_none(self) -> None:
        """初始化后 last_usage 应为 None。"""
        agent = Agent()
        assert agent.last_usage is None

    def test_thinking_budget_defaults_from_config(self) -> None:
        """未传参时，budget 应取 config.THINKING_BUDGET 值。"""
        import src.config as _cfg
        agent = Agent()
        assert agent.thinking_cfg.budget == _cfg.THINKING_BUDGET


class TestTokenUsage:
    """测试 run() 完成后 last_usage 的 token 统计行为"""

    def test_last_usage_set_after_run(self) -> None:
        """response.usage 正常时，last_usage 应被正确填充。"""
        agent = Agent(verbose=False)
        mock_resp = _make_text_response("答案")
        mock_resp.usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        with patch("src.agent.agent.chat", return_value=mock_resp):
            agent.run("问题")
        assert agent.last_usage is not None
        assert agent.last_usage.prompt_tokens == 100
        assert agent.last_usage.completion_tokens == 20
        assert agent.last_usage.total_tokens == 120

    def test_last_usage_none_when_no_usage_attribute(self) -> None:
        """response 没有 usage 属性时，last_usage 应为 None。"""
        agent = Agent(verbose=False)
        with patch("src.agent.agent.chat", return_value=_make_text_response("答案")):
            agent.run("问题")
        assert agent.last_usage is None

    def test_last_usage_accumulates_across_tool_rounds(self) -> None:
        """多轮 LLM 调用的 token 数应累加到 last_usage。"""
        agent = Agent(verbose=False)
        call_count = 0

        def mock_chat_with_usage(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = (
                _make_tool_call_response("search_knowledge", {"query": "q"})
                if call_count == 1
                else _make_text_response("最终回答")
            )
            resp.usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10)
            return resp

        with patch("src.agent.agent.chat", side_effect=mock_chat_with_usage), \
             patch("src.agent.core.tool_call_engine.execute_tool",
                   return_value=ToolResult(status="ok", content="结果")):
            agent.run("跨轮次问题")

        assert agent.last_usage is not None
        assert agent.last_usage.prompt_tokens == 100   # 2 次 × 50
        assert agent.last_usage.completion_tokens == 20  # 2 次 × 10
        assert agent.last_usage.total_tokens == 120


class TestAgentThinkingRun:
    """测试 thinking_cfg.enabled 开关对 run() 内部分支的影响"""

    def test_thinking_enabled_calls_call_with_thinking(self) -> None:
        """thinking_cfg.enabled=True 时，run() 应调用 call_with_thinking 而非 chat。"""
        from src.agent.agent import ThinkingConfig
        agent = Agent(verbose=False, thinking_config=ThinkingConfig(enabled=True, budget=4000))
        mock_resp = _make_text_response("thinking回答")

        with patch("src.agent.agent.call_with_thinking",
                   return_value=mock_resp) as mock_cwt, \
             patch("src.agent.agent.chat") as mock_chat:
            agent.run("测试 thinking")

        mock_cwt.assert_called_once()
        mock_chat.assert_not_called()
        # budget_tokens 应与初始化值一致
        _, kwargs = mock_cwt.call_args
        assert kwargs.get("budget_tokens") == 4000

    def test_thinking_disabled_calls_regular_chat(self) -> None:
        """thinking_cfg.enabled=False 时，run() 应调用 chat 而非 call_with_thinking。"""
        from src.agent.agent import ThinkingConfig
        agent = Agent(verbose=False, thinking_config=ThinkingConfig(enabled=False))

        with patch("src.agent.agent.chat",
                   return_value=_make_text_response("普通回答")) as mock_chat, \
             patch("src.agent.agent.call_with_thinking") as mock_cwt:
            agent.run("普通问题")

        mock_chat.assert_called_once()
        mock_cwt.assert_not_called()

    def test_thinking_uses_fixed_budget(self) -> None:
        """thinking 开启时 call_with_thinking 收到固定 budget（UI 档位决定，无自适应估算）。"""
        from src.agent.agent import ThinkingConfig
        agent = Agent(verbose=False, thinking_config=ThinkingConfig(
            enabled=True, budget=4000,
        ))

        with patch("src.agent.agent.call_with_thinking",
                   return_value=_make_text_response("ok")) as mock_cwt:
            agent.run("任意问题")

        _, kwargs = mock_cwt.call_args
        assert kwargs.get("budget_tokens") == 4000


class TestActivateSkill:
    """测试 Agent.activate_skill() 方法"""

    @staticmethod
    def _make_skill_info(name: str, desc: str, body: str) -> Any:
        from src.agent.core.skill_loader import SkillInfo
        from pathlib import Path
        return SkillInfo(name=name, description=desc,
                         location=Path(f"/fake/{name}/SKILL.md"), body=body)

    def test_activate_skill_injects_tag_into_system_prompt(self) -> None:
        """首次激活时，system_prompt 应含 skill_content 标签。"""
        info = self._make_skill_info("writer", "写作助手", "# 写作规范\n内容")
        agent = Agent(skills={"writer": info}, verbose=False)
        result = agent.activate_skill("writer", "# 写作规范\n内容")
        assert result is True
        assert '<skill_content name="writer">' in agent.system_prompt
        assert "# 写作规范" in agent.system_prompt

    def test_activate_skill_removes_from_skill_bodies(self) -> None:
        """激活后 _skill_bodies 中不应再含该 skill。"""
        info = self._make_skill_info("writer", "写作助手", "正文")
        agent = Agent(skills={"writer": info}, verbose=False)
        assert "writer" in agent._skill_bodies
        agent.activate_skill("writer", "正文")
        assert "writer" not in agent._skill_bodies

    def test_activate_skill_idempotent_returns_false(self) -> None:
        """已激活的 skill 再次激活应返回 False，system_prompt 不重复追加。"""
        info = self._make_skill_info("writer", "写作助手", "正文")
        agent = Agent(skills={"writer": info}, verbose=False)
        assert agent.activate_skill("writer", "正文") is True
        count_before = agent.system_prompt.count('<skill_content name="writer">')
        assert agent.activate_skill("writer", "正文") is False
        assert agent.system_prompt.count('<skill_content name="writer">') == count_before

    def test_activate_unknown_skill_still_injects(self) -> None:
        """不在初始 skills 中的 name 也能通过 activate_skill 注入（pop 安全忽略）。"""
        agent = Agent(verbose=False)
        result = agent.activate_skill("new_skill", "新内容")
        assert result is True
        assert '<skill_content name="new_skill">' in agent.system_prompt
