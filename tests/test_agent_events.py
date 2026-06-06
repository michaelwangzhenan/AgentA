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
    EVENT_PLAN_CREATED,
    EVENT_PLAN_STEP_END,
    EVENT_PLAN_STEP_START,
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


# ── Phase 2.1 — Plan-Execute 事件流 ──────────────────────────────────────────


class TestPlanEventFlow:
    """make_plan / update_step 调用应叠加触发 plan_created / plan_step_start / plan_step_end。"""

    def test_make_plan_emits_plan_created_and_first_step_start(self) -> None:
        """第 1 轮 LLM 调 make_plan，第 2 轮直接回答（避免 plan 未完终止 warning）。"""
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        steps = ["列项目", "对比", "总结"]
        responses = [
            _tool_call_response("make_plan", {"steps": steps}, "mp1"),
            _text_response("最终答"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat):
            agent.run("对比项目")

        types = [e.type for e in captured]
        # plan_created / plan_step_start 必须在 tool_call_end 之后（即 plan tool 执行成功后）
        assert EVENT_PLAN_CREATED in types
        assert EVENT_PLAN_STEP_START in types
        # 顺序：info → tool_call_start → tool_call_end → plan_created → plan_step_start → final_answer
        plan_created_idx = types.index(EVENT_PLAN_CREATED)
        plan_step_start_idx = types.index(EVENT_PLAN_STEP_START)
        tool_end_idx = types.index(EVENT_TOOL_CALL_END)
        assert tool_end_idx < plan_created_idx < plan_step_start_idx

        # payload 关键字段
        plan_created_ev = next(e for e in captured if e.type == EVENT_PLAN_CREATED)
        assert [s["text"] for s in plan_created_ev.payload["steps"]] == steps
        assert plan_created_ev.payload["steps"][0]["id"] == 1

        first_step_start = next(e for e in captured if e.type == EVENT_PLAN_STEP_START)
        assert first_step_start.payload == {"step_id": 1, "text": "列项目"}

    def test_update_step_emits_step_end_and_next_step_start(self) -> None:
        """update_step 后还有 pending 步：发 plan_step_end + plan_step_start(下一步)。"""
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        responses = [
            _tool_call_response("make_plan", {"steps": ["a", "b", "c"]}, "mp1"),
            _tool_call_response(
                "update_step", {"step_id": 1, "status": "success", "note": "ok"}, "u1",
            ),
            _text_response("done"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat):
            agent.run("做任务")

        types = [e.type for e in captured]
        # 期望出现：plan_step_end(id=1) 后紧跟 plan_step_start(id=2)
        step_end_idx = types.index(EVENT_PLAN_STEP_END)
        # plan_step_start 出现 2 次（make_plan 触发 step 1、update_step 触发 step 2）
        step_starts = [e for e in captured if e.type == EVENT_PLAN_STEP_START]
        assert len(step_starts) == 2
        assert step_starts[0].payload == {"step_id": 1, "text": "a"}
        assert step_starts[1].payload == {"step_id": 2, "text": "b"}

        end_ev = next(e for e in captured if e.type == EVENT_PLAN_STEP_END)
        assert end_ev.payload == {"step_id": 1, "status": "success", "note": "ok"}
        # 顺序：plan_step_end 应在第二个 plan_step_start 之前
        second_step_start_idx = [i for i, e in enumerate(captured) if e.type == EVENT_PLAN_STEP_START][1]
        assert step_end_idx < second_step_start_idx

    def test_update_step_completing_plan_emits_only_step_end(self) -> None:
        """update_step 完成最后一步：只发 plan_step_end，不再发 plan_step_start。"""
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        responses = [
            _tool_call_response("make_plan", {"steps": ["only"]}, "mp1"),
            _tool_call_response("update_step", {"step_id": 1, "status": "success"}, "u1"),
            _text_response("完成"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat):
            agent.run("一步任务")

        types = [e.type for e in captured]
        # make_plan 触发 1 个 plan_step_start (id=1)；update_step 完成 plan，不再发新 start
        step_starts = [e for e in captured if e.type == EVENT_PLAN_STEP_START]
        assert len(step_starts) == 1
        assert step_starts[0].payload["step_id"] == 1
        assert EVENT_PLAN_STEP_END in types

    def test_abort_plan_does_not_emit_plan_events(self) -> None:
        """abort_plan 调用不应触发 plan_* 事件（plan 终止由 final_answer 文案承载）。"""
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        responses = [
            _tool_call_response("make_plan", {"steps": ["a", "b"]}, "mp1"),
            _tool_call_response("abort_plan", {"reason": "失败太多"}, "ab1"),
            _text_response("已中止"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat):
            agent.run("尝试")

        # plan_created + plan_step_start(id=1) 来自 make_plan；abort_plan 不增加任何 plan_* 事件
        plan_evs = [e for e in captured if e.type.startswith("plan_")]
        assert {e.type for e in plan_evs} == {EVENT_PLAN_CREATED, EVENT_PLAN_STEP_START}
        assert sum(1 for e in plan_evs if e.type == EVENT_PLAN_STEP_START) == 1


class TestPlanAutoFinalizeOnFinalAnswer:
    """LLM 在最后一步直接出答案（不调 update_step）时，出 final_answer 前应补发剩余 pending 步的 plan_step_end，避免 UI 永远转圈。"""

    def test_pending_steps_closed_before_final_answer(self) -> None:
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        # 3 步 plan：只显式 update_step(1)，第 2/3 步靠 final_answer 前自动收尾
        responses = [
            _tool_call_response("make_plan", {"steps": ["a", "b", "c"]}, "mp1"),
            _tool_call_response("update_step", {"step_id": 1, "status": "success"}, "u1"),
            _text_response("综合答案"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat):
            answer = agent.run("做任务")

        assert answer == "综合答案"
        types = [e.type for e in captured]
        final_idx = types.index(EVENT_FINAL_ANSWER)
        # final_answer 之前补发的 plan_step_end：step 1（显式）+ step 2/3（自动）共 3 条
        ends_before_final = [
            e for e in captured[:final_idx] if e.type == EVENT_PLAN_STEP_END
        ]
        closed_ids = {e.payload["step_id"] for e in ends_before_final}
        assert closed_ids == {1, 2, 3}
        # 自动补发的两步标 success
        auto = [e for e in ends_before_final if e.payload["step_id"] in (2, 3)]
        assert all(e.payload["status"] == "success" for e in auto)

    def test_no_finalize_when_plan_already_complete(self) -> None:
        """所有步都已显式 update_step → final_answer 前不重复补发。"""
        agent = _mk_agent()
        captured: list[AgentEvent] = []
        agent.set_event_callback(captured.append)

        responses = [
            _tool_call_response("make_plan", {"steps": ["only"]}, "mp1"),
            _tool_call_response("update_step", {"step_id": 1, "status": "success"}, "u1"),
            _text_response("完成"),
        ]

        def fake_chat(messages, tools=None, **kwargs):
            return responses.pop(0)

        with patch("src.agent.agent.chat", side_effect=fake_chat):
            agent.run("一步任务")

        # plan_step_end 只来自显式 update_step 一条，无自动补发
        ends = [e for e in captured if e.type == EVENT_PLAN_STEP_END]
        assert len(ends) == 1
        assert ends[0].payload["step_id"] == 1


# ── Phase 2.1 — Plan-aware 循环上限自适应 ─────────────────────────────────────


class TestPlanAwareCaps:
    """`_compute_effective_caps` 在不同 plan 状态下应给出预期上限。"""

    def test_no_plan_uses_baseline_caps(self) -> None:
        from src.agent.agent import MAX_TOOL_ROUNDS
        agent = _mk_agent()
        eff_tool, eff_total = agent._compute_effective_caps([])
        assert eff_tool == MAX_TOOL_ROUNDS
        assert eff_total == agent.max_iterations

    def test_active_plan_expands_caps_proportional_to_steps(self) -> None:
        from src.agent.agent import MAX_HARD_CAP_ROUNDS, MAX_TOOL_ROUNDS
        agent = _mk_agent()
        msgs = [
            _mk_assistant_make_plan(["s1", "s2", "s3", "s4", "s5"], call_id="mp1"),
        ]
        eff_tool, eff_total = agent._compute_effective_caps(msgs)
        # 5 步 × 4 + 2 = 22 > baseline 8
        assert eff_tool == 22
        assert eff_total >= eff_tool + 4
        assert eff_tool <= MAX_HARD_CAP_ROUNDS

    def test_completed_plan_falls_back_to_baseline(self) -> None:
        from src.agent.agent import MAX_TOOL_ROUNDS
        agent = _mk_agent()
        # 2 步 plan，两步都标 success → is_complete()，上限退回 baseline
        msgs = [
            _mk_assistant_make_plan(["a", "b"], call_id="mp1"),
            _mk_assistant_update_step(1, "success", call_id="u1"),
            _mk_assistant_update_step(2, "success", call_id="u2"),
        ]
        eff_tool, eff_total = agent._compute_effective_caps(msgs)
        assert eff_tool == MAX_TOOL_ROUNDS
        assert eff_total == agent.max_iterations

    def test_aborted_plan_falls_back_to_baseline(self) -> None:
        from src.agent.agent import MAX_TOOL_ROUNDS
        agent = _mk_agent()
        msgs = [
            _mk_assistant_make_plan(["a", "b", "c"], call_id="mp1"),
            _mk_assistant_abort_plan("失败", call_id="ab1"),
        ]
        eff_tool, _ = agent._compute_effective_caps(msgs)
        assert eff_tool == MAX_TOOL_ROUNDS


def _mk_assistant_make_plan(steps: list[str], call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": "make_plan", "arguments": json.dumps({"steps": steps})},
        }],
    }


def _mk_assistant_update_step(step_id: int, status: str, call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": "update_step",
                "arguments": json.dumps({"step_id": step_id, "status": status}),
            },
        }],
    }


def _mk_assistant_abort_plan(reason: str, call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": "abort_plan", "arguments": json.dumps({"reason": reason})},
        }],
    }
