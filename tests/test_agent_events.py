"""
测试：Agent loop 内事件流（§4.5.4 AgentAPI 完整化）

§4.5.4 把 EventBus.publish 升级为 publish(event: AgentEvent) 单参签名，
并要求 Python Agent 在 loop 关键点发出 7 类事件中的下列子集：
    - info             (run 启动 / skill.activated)
    - tool_call_start  (每次 execute_tool 之前)
    - tool_call_end    (每次 execute_tool 之后)
    - final_answer     (run 退出前)
    - error            (LLM 调用异常 / 空内容 fallback / 超轮次)
    - thinking_chunk   (call_with_thinking 的 on_thinking_chunk 回调)
    - token_chunk      (call_with_thinking / chat 的 on_token_chunk 回调)

本文件用 mock 的 LLM response 串出"工具调用→最终回答"流程，
按决策 D4=A 验证 **事件类型顺序 + 个数 + payload 关键字段**，
不过度断言 payload 内全部字段值（避免 snapshot 测试的脆弱性）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.agent.agent import Agent
from src.agent.core.event_bus import (
    EVENT_ERROR,
    EVENT_FINAL_ANSWER,
    EVENT_INFO,
    EVENT_TOOL_CALL_END,
    EVENT_TOOL_CALL_START,
    AgentEvent,
)
from src.agent.tools import ToolResult


# ── 辅助 ───────────────────────────────────────────────────────────────────

def _text_response(content: str) -> Any:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call_response(name: str, args: dict, call_id: str = "c1") -> Any:
    tc = SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )
    message = SimpleNamespace(content="", tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _mk_agent() -> Agent:
    mock_history = MagicMock()
    mock_history.load_last_n_messages.return_value = []
    return Agent(verbose=False, chat_history=mock_history, user_memory=None)


# ── 直接回答路径：info → final_answer ──────────────────────────────────────

class TestDirectAnswerEventFlow:
    """LLM 直接返回文本（不调用工具）时，事件应为 [info, final_answer]。"""

    def test_event_sequence_and_payload_keys(self) -> None:
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        with patch("src.agent.agent.chat", return_value=_text_response("直接答")):
            answer = agent.run("hi")

        assert answer == "直接答"
        types = [e.type for e in captured]
        assert types == [EVENT_INFO, EVENT_FINAL_ANSWER]
        # info payload 含 session_id；final_answer payload 含 text + usage
        assert captured[0].payload.get("message") == "agent.run.start"
        assert captured[0].payload.get("session_id") == agent.session_id
        assert captured[1].payload.get("text") == "直接答"
        assert "usage" in captured[1].payload


# ── 工具调用路径：info → tool_call_start → tool_call_end → final_answer ────

class TestToolCallEventFlow:
    """第一轮 LLM 调工具 + 第二轮直接回答的标准事件流。"""

    def test_event_sequence(self) -> None:
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        responses = [
            _tool_call_response("search_knowledge", {"query": "q"}, "call-1"),
            _text_response("最终答"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat), \
             patch(
                "src.agent.core.tool_call_engine.execute_tool",
                return_value=ToolResult(status="ok", content="hit text"),
             ):
            answer = agent.run("hi")

        assert answer == "最终答"
        types = [e.type for e in captured]
        assert types == [EVENT_INFO, EVENT_TOOL_CALL_START, EVENT_TOOL_CALL_END, EVENT_FINAL_ANSWER]

        # tool_call_start payload 关键字段
        start_pl = captured[1].payload
        assert start_pl.get("name") == "search_knowledge"
        assert start_pl.get("args") == {"query": "q"}
        assert start_pl.get("call_id") == "call-1"

        # tool_call_end payload 关键字段
        end_pl = captured[2].payload
        assert end_pl.get("call_id") == "call-1"
        assert end_pl.get("status") == "ok"
        assert "preview" in end_pl


# ── 空内容 fallback 路径：info → error → final_answer ─────────────────────

class TestEmptyResponseEventFlow:

    def test_emits_error_then_fallback_final_answer(self) -> None:
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        with patch("src.agent.agent.chat", return_value=_text_response("")):
            answer = agent.run("hi")

        assert "抱歉" in answer
        types = [e.type for e in captured]
        assert types == [EVENT_INFO, EVENT_ERROR, EVENT_FINAL_ANSWER]
        assert captured[1].payload.get("phase") == "empty_response"
        assert captured[2].payload.get("text") == answer


# ── activate_skill：info ───────────────────────────────────────────────────

class TestActivateSkillInfoEvent:

    def test_emits_info_on_first_activation(self) -> None:
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        assert agent.activate_skill("demo", "body") is True
        infos = [e for e in captured if e.type == EVENT_INFO]
        assert any(
            i.payload.get("message") == "skill.activated"
            and i.payload.get("skill_name") == "demo"
            for i in infos
        )

    def test_no_event_when_already_active(self) -> None:
        agent = _mk_agent()
        agent.activate_skill("demo", "body")  # 首次激活，事件不订阅
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)
        assert agent.activate_skill("demo", "body") is False
        assert captured == []
