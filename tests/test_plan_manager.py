"""
测试：`src.agent.core.plan_manager` PlanStep / PlanState / reconstruct_from_messages

覆盖：
- PlanStep / PlanState dataclass 行为（构造 / next_pending_step / is_complete /
  progress / update 的合法/非法/越界路径）
- reconstruct_from_messages 9 类形态（空 / 无 make_plan / 单 plan 全 pending /
  含 update_step / 多次 update_step / abort_plan / 多次 make_plan 取最新 /
  tool_calls 是 SDK 对象 / 非法 args 容错）
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.core.plan_manager import (
    PlanState,
    PlanStep,
    reconstruct_from_messages,
)


# ── PlanStep / PlanState 单元行为 ────────────────────────────────────────────


class TestPlanStateBasics:
    def test_from_step_texts_assigns_sequential_ids_from_1(self) -> None:
        s = PlanState.from_step_texts(["列项目", "对比", "总结"])
        assert [step.id for step in s.steps] == [1, 2, 3]
        assert [step.text for step in s.steps] == ["列项目", "对比", "总结"]
        assert all(step.status == "pending" for step in s.steps)
        assert all(step.note == "" for step in s.steps)
        assert s.aborted is False

    def test_empty_plan_complete_and_progress(self) -> None:
        s = PlanState()
        assert s.is_complete() is True
        assert s.progress() == (0, 0)
        assert s.next_pending_step() is None

    def test_next_pending_step_returns_first_pending(self) -> None:
        s = PlanState.from_step_texts(["a", "b", "c"])
        s.update(1, "success")
        nxt = s.next_pending_step()
        assert nxt is not None
        assert nxt.id == 2 and nxt.text == "b"

    def test_progress_counts_non_pending(self) -> None:
        s = PlanState.from_step_texts(["a", "b", "c"])
        s.update(1, "success")
        s.update(3, "failed")
        done, total = s.progress()
        assert (done, total) == (2, 3)

    def test_is_complete_when_all_non_pending(self) -> None:
        s = PlanState.from_step_texts(["a", "b"])
        s.update(1, "success")
        s.update(2, "skipped")
        assert s.is_complete() is True
        assert s.next_pending_step() is None

    def test_is_complete_when_aborted_even_with_pending(self) -> None:
        s = PlanState.from_step_texts(["a", "b", "c"])
        s.aborted = True
        assert s.is_complete() is True
        assert s.next_pending_step() is None


class TestPlanStateUpdate:
    def test_update_success_with_note_persists(self) -> None:
        s = PlanState.from_step_texts(["a"])
        assert s.update(1, "success", note="找到 3 个项目") is True
        assert s.steps[0].status == "success"
        assert s.steps[0].note == "找到 3 个项目"

    def test_update_failed(self) -> None:
        s = PlanState.from_step_texts(["a"])
        assert s.update(1, "failed", note="503 错误") is True
        assert s.steps[0].status == "failed"
        assert s.steps[0].note == "503 错误"

    def test_update_unknown_step_id_returns_false(self) -> None:
        s = PlanState.from_step_texts(["a"])
        assert s.update(99, "success") is False

    def test_update_invalid_status_returns_false(self) -> None:
        s = PlanState.from_step_texts(["a"])
        assert s.update(1, "weird") is False
        assert s.steps[0].status == "pending"

    def test_update_back_to_pending_forbidden(self) -> None:
        """update 不允许把已完结的 step 反向标回 pending（防 LLM 误操作回退状态）。"""
        s = PlanState.from_step_texts(["a"])
        s.update(1, "success")
        assert s.update(1, "pending") is False
        assert s.steps[0].status == "success"


# ── reconstruct_from_messages 主路径 ────────────────────────────────────────


def _mk_assistant_tc(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    """构造一个 assistant 带单 tool_call 的 dict 形态 message。"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }


def _mk_tool_msg(call_id: str, content: str = "ok") -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class TestReconstructFromMessages:
    def test_empty_messages_returns_none(self) -> None:
        assert reconstruct_from_messages([]) is None

    def test_no_make_plan_returns_none(self) -> None:
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            _mk_assistant_tc("search_knowledge", {"query": "foo"}),
            _mk_tool_msg("c1", "..."),
        ]
        assert reconstruct_from_messages(msgs) is None

    def test_single_make_plan_all_pending(self) -> None:
        msgs = [
            {"role": "user", "content": "对比项目"},
            _mk_assistant_tc("make_plan", {"steps": ["列项目", "对比", "总结"]}, call_id="mp1"),
            _mk_tool_msg("mp1", "plan 已记录"),
        ]
        state = reconstruct_from_messages(msgs)
        assert state is not None
        assert state.progress() == (0, 3)
        assert state.aborted is False
        assert state.next_pending_step().id == 1  # type: ignore[union-attr]

    def test_make_plan_then_update_step(self) -> None:
        msgs = [
            _mk_assistant_tc("make_plan", {"steps": ["a", "b", "c"]}, call_id="mp1"),
            _mk_tool_msg("mp1"),
            _mk_assistant_tc("search_knowledge", {"query": "x"}, call_id="s1"),
            _mk_tool_msg("s1", "hits"),
            _mk_assistant_tc(
                "update_step",
                {"step_id": 1, "status": "success", "note": "找到 3"},
                call_id="u1",
            ),
            _mk_tool_msg("u1"),
        ]
        state = reconstruct_from_messages(msgs)
        assert state is not None
        assert state.steps[0].status == "success"
        assert state.steps[0].note == "找到 3"
        assert state.next_pending_step().id == 2  # type: ignore[union-attr]
        assert state.progress() == (1, 3)

    def test_multiple_update_steps_accumulate(self) -> None:
        msgs = [
            _mk_assistant_tc("make_plan", {"steps": ["a", "b", "c"]}, call_id="mp1"),
            _mk_tool_msg("mp1"),
            _mk_assistant_tc("update_step", {"step_id": 1, "status": "success"}, call_id="u1"),
            _mk_tool_msg("u1"),
            _mk_assistant_tc("update_step", {"step_id": 2, "status": "failed", "note": "503"}, call_id="u2"),
            _mk_tool_msg("u2"),
            _mk_assistant_tc("update_step", {"step_id": 3, "status": "skipped"}, call_id="u3"),
            _mk_tool_msg("u3"),
        ]
        state = reconstruct_from_messages(msgs)
        assert state is not None
        assert [s.status for s in state.steps] == ["success", "failed", "skipped"]
        assert state.steps[1].note == "503"
        assert state.is_complete() is True

    def test_abort_plan_sets_aborted(self) -> None:
        msgs = [
            _mk_assistant_tc("make_plan", {"steps": ["a", "b"]}, call_id="mp1"),
            _mk_tool_msg("mp1"),
            _mk_assistant_tc("update_step", {"step_id": 1, "status": "success"}, call_id="u1"),
            _mk_tool_msg("u1"),
            _mk_assistant_tc("abort_plan", {"reason": "用户取消"}, call_id="ab1"),
            _mk_tool_msg("ab1"),
        ]
        state = reconstruct_from_messages(msgs)
        assert state is not None
        assert state.aborted is True
        assert state.is_complete() is True
        assert state.next_pending_step() is None

    def test_latest_make_plan_wins_when_multiple_present(self) -> None:
        """连续两次 make_plan：取最新那次，老 plan 的 update_step 不影响新 plan。"""
        msgs = [
            _mk_assistant_tc("make_plan", {"steps": ["old-1", "old-2"]}, call_id="mp_old"),
            _mk_tool_msg("mp_old"),
            _mk_assistant_tc("update_step", {"step_id": 1, "status": "success"}, call_id="u_old"),
            _mk_tool_msg("u_old"),
            _mk_assistant_tc("make_plan", {"steps": ["new-1", "new-2", "new-3"]}, call_id="mp_new"),
            _mk_tool_msg("mp_new"),
        ]
        state = reconstruct_from_messages(msgs)
        assert state is not None
        assert [s.text for s in state.steps] == ["new-1", "new-2", "new-3"]
        assert state.progress() == (0, 3)

    def test_tool_calls_in_sdk_object_form(self) -> None:
        """SDK 返回的 tool_calls 是对象形态（非 dict）；reconstruct 应同样能解析。"""
        sdk_tc = SimpleNamespace(
            id="mp_sdk",
            type="function",
            function=SimpleNamespace(
                name="make_plan",
                arguments=json.dumps({"steps": ["a", "b"]}),
            ),
        )
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [sdk_tc]},
            _mk_tool_msg("mp_sdk"),
        ]
        state = reconstruct_from_messages(msgs)
        assert state is not None
        assert [s.text for s in state.steps] == ["a", "b"]

    def test_invalid_args_tolerated_no_exception(self) -> None:
        """update_step 带非 int step_id / 非 str status / 非法 status 都不抛，静默跳过。"""
        msgs = [
            _mk_assistant_tc("make_plan", {"steps": ["a", "b"]}, call_id="mp1"),
            _mk_tool_msg("mp1"),
            _mk_assistant_tc("update_step", {"step_id": "not-int", "status": "success"}, call_id="u1"),
            _mk_tool_msg("u1"),
            _mk_assistant_tc("update_step", {"step_id": 1, "status": "weird"}, call_id="u2"),
            _mk_tool_msg("u2"),
            _mk_assistant_tc("update_step", {"step_id": 99, "status": "success"}, call_id="u3"),
            _mk_tool_msg("u3"),
        ]
        state = reconstruct_from_messages(msgs)
        assert state is not None
        # 所有非法 update_step 都被忽略，plan 保持初始 pending 状态
        assert [s.status for s in state.steps] == ["pending", "pending"]


# ── 边角形态 ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_make_plan_with_non_str_step_silently_rejected(self) -> None:
        """make_plan(steps=[1, 2]) 这种非法 args，reconstruct 不应认为 plan 存在。"""
        msgs = [
            _mk_assistant_tc("make_plan", {"steps": [1, 2, 3]}, call_id="bad"),
            _mk_tool_msg("bad"),
        ]
        assert reconstruct_from_messages(msgs) is None

    def test_make_plan_with_missing_steps_arg_silently_rejected(self) -> None:
        msgs = [
            _mk_assistant_tc("make_plan", {}, call_id="bad"),
            _mk_tool_msg("bad"),
        ]
        # steps 缺失走 isinstance 检查 + empty list，认为 plan 存在但 0 步（完结态）
        state = reconstruct_from_messages(msgs)
        assert state is not None
        assert state.steps == []
        assert state.is_complete() is True
