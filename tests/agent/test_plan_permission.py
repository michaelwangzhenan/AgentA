"""
Phase 3.2 Plan-execute 用户审批 mode UT —— 锁定 Agent.approval_callback 注册、
yes/no 路径、PLAN_PERMISSION_MODE 关闭时静默放行、agent.run 接住 PlanAbortedByUser。
详 docs/iter_2_agent.md §4.9.12 D2 / D8。
"""
from unittest.mock import patch

import pytest

from src.agent.agent import Agent, PlanAbortedByUser
from src.agent.core.tool_call_engine import ToolCallEngine


class TestRequestPlanApproval:
    """Agent.request_plan_approval 行为锁定。"""

    def test_no_callback_returns_yes(self):
        agent = Agent(approval_callback=None)
        assert agent.request_plan_approval({"steps": []}) == "yes"

    def test_permission_mode_off_returns_yes(self):
        """PLAN_PERMISSION_MODE=false 时即使有 callback 也直接放行（不触发交互）。"""
        called = []
        agent = Agent(approval_callback=lambda payload: called.append(payload) or "no")
        with patch("src.agent.agent._cfg.PLAN_PERMISSION_MODE", False):
            assert agent.request_plan_approval({"steps": ["a"]}) == "yes"
        assert called == [], "PLAN_PERMISSION_MODE=false 时 callback 不应被调用"

    def test_permission_mode_on_callback_yes(self):
        agent = Agent(approval_callback=lambda payload: "yes")
        with patch("src.agent.agent._cfg.PLAN_PERMISSION_MODE", True):
            assert agent.request_plan_approval({"steps": ["a"]}) == "yes"

    def test_permission_mode_on_callback_no(self):
        agent = Agent(approval_callback=lambda payload: "no")
        with patch("src.agent.agent._cfg.PLAN_PERMISSION_MODE", True):
            assert agent.request_plan_approval({"steps": ["a"]}) == "no"

    def test_callback_exception_falls_back_to_yes(self):
        """callback 抛异常时 fail-open 放行，避免 UI 异常卡死整个 query。"""
        def boom(payload):
            raise RuntimeError("ui crashed")
        agent = Agent(approval_callback=boom)
        with patch("src.agent.agent._cfg.PLAN_PERMISSION_MODE", True):
            assert agent.request_plan_approval({"steps": ["a"]}) == "yes"

    def test_callback_returns_uppercase_normalized(self):
        agent = Agent(approval_callback=lambda payload: "YES")
        with patch("src.agent.agent._cfg.PLAN_PERMISSION_MODE", True):
            assert agent.request_plan_approval({"steps": ["a"]}) == "yes"


class TestToolCallEngineApprovalHook:
    """tool_call_engine 在 make_plan 后调 approval_fn；返 no 抛 PlanAbortedByUser。"""

    def _make_engine(self, approval_fn):
        from src.agent.core.event_bus import EventBus
        from src.stores.session_store import SessionStore
        # MagicMock session_store（不写真 DB）
        from unittest.mock import MagicMock
        ch = MagicMock(spec=SessionStore)
        return ToolCallEngine(
            session_store=ch,
            session_id="test-session",
            skill_bodies={},
            verbose=False,
            events=EventBus(),
            approval_fn=approval_fn,
        )

    def test_make_plan_yes_no_exception(self):
        engine = self._make_engine(approval_fn=lambda payload: "yes")
        # 直接调 _maybe_publish_plan_events 模拟 make_plan 已成功的场景
        engine._maybe_publish_plan_events(
            tool_name="make_plan",
            tool_args={"steps": ["列项目", "对比"]},
            messages=[],
        )

    def test_make_plan_no_raises(self):
        engine = self._make_engine(approval_fn=lambda payload: "no")
        with pytest.raises(PlanAbortedByUser):
            engine._maybe_publish_plan_events(
                tool_name="make_plan",
                tool_args={"steps": ["列项目", "对比"]},
                messages=[],
            )

    def test_no_approval_fn_no_block(self):
        """approval_fn=None 时不调用，不抛异常（Phase 2.1 默认行为兼容）。"""
        engine = self._make_engine(approval_fn=None)
        engine._maybe_publish_plan_events(
            tool_name="make_plan",
            tool_args={"steps": ["s1"]},
            messages=[],
        )

    def test_non_make_plan_tool_skips_approval(self):
        """update_step / abort_plan 等非 make_plan 不触发审批 hook。"""
        called = []
        engine = self._make_engine(approval_fn=lambda payload: called.append(payload) or "no")
        # update_step 不应触发审批；这里给非法 step_id 让函数提前 return（不影响测试目的）
        engine._maybe_publish_plan_events(
            tool_name="update_step",
            tool_args={"step_id": "not-int", "status": "success"},
            messages=[],
        )
        assert called == []
