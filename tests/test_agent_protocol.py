"""
测试：BaseAgent Protocol 一致性 —— 三种 Agent 实现接口对齐

`design.md §3.3.1` 规划的 `BaseAgent` Protocol 是 §4.5 重构的契约支点。
本文件用 duck-typing（`hasattr` + `inspect.signature`）锁定三种实现的方法签名，
任何一方破坏契约都立刻在 CI 红出来。

当前（重构前）已统一的方法：
  - `run(user_input: str) -> str`
  - `activate_skill(name: str, body: str) -> bool`
  - `session_id: str` 实例属性

待 §4.5 EventBus / 接口对齐后才统一的方法（本期 placeholder，xfail）：
  - `set_thinking_callback(cb)` —— 目前仅 Python `Agent` 提供
  - `set_token_callback(cb)`    —— 同上

LangChain 实现：当前环境 `langchain.agents.create_agent` 不可用，整组测试 skip；
LangChain 修复后此 skip 自然解除。
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest


# ── 收集"可被检查"的三种 Agent 类 ───────────────────────────────────────────

def _try_import(module: str, name: str):
    """安全 import，失败返回 None 以便用 pytest.skip 优雅跳过。"""
    try:
        mod = __import__(module, fromlist=[name])
        return getattr(mod, name)
    except Exception:
        return None


Agent = _try_import("src.agent.agent", "Agent")
AutoGPTAgent = _try_import("src.agent.autogpt_agent", "AutoGPTAgent")
LangChainAgent = _try_import("src.agent.langchain_agent", "LangChainAgent")


# ── 已统一的核心接口（三种实现都必须有） ───────────────────────────────────

CORE_METHODS = ["run", "activate_skill"]


class TestPythonAgentProtocol:
    """Python 实现是基准 reference，所有断言必须全部通过。"""

    def test_has_core_methods(self) -> None:
        assert Agent is not None
        for name in CORE_METHODS:
            assert callable(getattr(Agent, name, None)), f"Agent.{name} 缺失"

    def test_run_signature(self) -> None:
        sig = inspect.signature(Agent.run)
        params = list(sig.parameters.values())
        # self + user_input
        assert len(params) >= 2
        assert params[1].name == "user_input"

    def test_activate_skill_signature(self) -> None:
        sig = inspect.signature(Agent.activate_skill)
        params = [p.name for p in sig.parameters.values()]
        assert "name" in params and "body" in params

    def test_session_id_attribute_after_init(self) -> None:
        """session_id 必须在实例化后立刻可读（用 None 让其自动生成 uuid）。"""
        from unittest.mock import MagicMock
        mock_history = MagicMock()
        mock_history.load_last_n_messages.return_value = []
        a = Agent(verbose=False, chat_history=mock_history, user_memory=None)
        assert isinstance(a.session_id, str) and len(a.session_id) > 0


class TestAutoGPTAgentProtocol:
    """AutoGPT 实现：核心接口已对齐，必须通过。"""

    def setup_method(self) -> None:
        if AutoGPTAgent is None:
            pytest.skip("AutoGPTAgent import 失败，跳过 Protocol 检查")

    def test_has_core_methods(self) -> None:
        for name in CORE_METHODS:
            assert callable(getattr(AutoGPTAgent, name, None)), f"AutoGPTAgent.{name} 缺失"

    def test_run_signature(self) -> None:
        sig = inspect.signature(AutoGPTAgent.run)
        params = list(sig.parameters.values())
        assert len(params) >= 2
        assert params[1].name == "user_input"

    def test_activate_skill_signature(self) -> None:
        sig = inspect.signature(AutoGPTAgent.activate_skill)
        params = [p.name for p in sig.parameters.values()]
        assert "name" in params and "body" in params

    def test_session_id_attribute_after_init(self) -> None:
        from unittest.mock import MagicMock
        mock_history = MagicMock()
        mock_history.load_last_n_messages.return_value = []
        a = AutoGPTAgent(verbose=False, chat_history=mock_history)
        assert isinstance(a.session_id, str) and len(a.session_id) > 0


class TestLangChainAgentProtocol:
    """LangChain 实现：当前环境 import 失败，整组 skip。"""

    def setup_method(self) -> None:
        if LangChainAgent is None:
            pytest.skip(
                "LangChainAgent import 失败（langchain.agents.create_agent 不可用），"
                "待 LangChain 环境修复后自然解除 skip"
            )

    def test_has_core_methods(self) -> None:
        for name in CORE_METHODS:
            assert callable(getattr(LangChainAgent, name, None)), f"LangChainAgent.{name} 缺失"


# ── 待重构后统一的事件接口（当前仅 Python 提供） ───────────────────────────

EVENT_METHODS = ["set_thinking_callback", "set_token_callback"]


class TestEventInterfaceCurrentState:
    """记录当前事件接口的实际分布，避免误以为已统一。"""

    def test_python_agent_has_event_methods(self) -> None:
        for name in EVENT_METHODS:
            assert callable(getattr(Agent, name, None)), f"Agent.{name} 必须存在"

    def test_autogpt_event_methods_missing_today(self) -> None:
        """AutoGPT 今天没有 callback 接口；EventBus 重构后应补齐。"""
        if AutoGPTAgent is None:
            pytest.skip("AutoGPTAgent 未导入")
        for name in EVENT_METHODS:
            assert not hasattr(AutoGPTAgent, name), (
                f"AutoGPTAgent.{name} 出现了 —— 已统一？请去掉本 negative assert "
                "并把方法名加进 CORE_METHODS"
            )


class TestEventInterfaceFutureContract:
    """EventBus 抽出后，三种实现都应有事件订阅接口；当前 xfail。"""

    @pytest.mark.xfail(
        strict=True,
        reason="AutoGPT 待 §4.5 EventBus 抽出后统一事件接口（届时移除 xfail）",
    )
    def test_autogpt_has_event_methods_after_refactor(self) -> None:
        if AutoGPTAgent is None:
            pytest.skip("AutoGPTAgent 未导入")
        for name in EVENT_METHODS:
            assert callable(getattr(AutoGPTAgent, name, None))
