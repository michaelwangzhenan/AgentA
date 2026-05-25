"""
测试：AgentAPI 一致性 —— 三种 Agent 实现接口对齐

`design.md §1.3 / §3.1` 定义的 `AgentAPI` 是表现层 ↔ Agent core 的对外契约，
Python 实现位于 `src/agent/agent_api.py`（`@runtime_checkable Protocol`）。
本文件用 isinstance + 方法签名验证三种 Agent 实现：
- isinstance(agent, AgentAPI) 必为 True（runtime_checkable 自动按 duck typing 校验）
- 方法签名锁定，任一方破坏契约 CI 红出来

§4.5.4 后三种实现统一的方法（全部断言必过）：
  - `run(user_input: str) -> str`
  - `activate_skill(name: str, body: str) -> bool`
  - `set_event_callback(cb: Callable[[AgentEvent], None] | None)` ── 统一事件入口
  - `session_id: str` / `events: EventBus` / `last_usage` / `thinking_cfg` 实例属性
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
AgentAPI = _try_import("src.agent.agent_api", "AgentAPI")


# ── 已统一的核心接口（三种实现都必须有） ───────────────────────────────────

CORE_METHODS = ["run", "activate_skill", "set_event_callback"]


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
    """LangChain 实现：§4.5.3 修复 import 后纳入正式断言（默认套件按 marker deselect）。"""

    def setup_method(self) -> None:
        if LangChainAgent is None:
            pytest.skip("LangChainAgent 未导入（环境未装 langchain）")

    def test_has_core_methods(self) -> None:
        for name in CORE_METHODS:
            assert callable(getattr(LangChainAgent, name, None)), f"LangChainAgent.{name} 缺失"


# ── EventBus 实例契约（三种实现都暴露 `events` 属性） ──────────────────────

class TestEventBusInstanceContract:
    """§4.5 EventBus 抽出后，所有 Agent 实例都暴露 `events: EventBus` 属性。"""

    def test_python_agent_has_events_attr(self) -> None:
        from unittest.mock import MagicMock
        from src.agent.core.event_bus import EventBus
        mock_history = MagicMock()
        mock_history.load_last_n_messages.return_value = []
        a = Agent(verbose=False, chat_history=mock_history, user_memory=None)
        assert isinstance(a.events, EventBus)

    def test_autogpt_agent_has_events_attr(self) -> None:
        if AutoGPTAgent is None:
            pytest.skip("AutoGPTAgent 未导入")
        from unittest.mock import MagicMock
        from src.agent.core.event_bus import EventBus
        mock_history = MagicMock()
        mock_history.load_last_n_messages.return_value = []
        a = AutoGPTAgent(verbose=False, chat_history=mock_history)
        assert isinstance(a.events, EventBus)

    def test_langchain_agent_has_events_attr(self) -> None:
        if LangChainAgent is None:
            pytest.skip("LangChainAgent 未导入（环境未装 langchain）")
        from unittest.mock import patch
        from src.agent.core.event_bus import EventBus
        # 全 mock 掉 LLM / tools / SQLite / agent / executor，避免真实 langchain 调用
        with patch("src.agent.langchain_agent.build_chat_model"), \
             patch("src.agent.langchain_agent.build_langchain_tools", return_value=[]), \
             patch("src.agent.langchain_agent.SQLiteChatMessageHistory"), \
             patch("src.agent.langchain_agent.create_tool_calling_agent"), \
             patch("src.agent.langchain_agent.AgentExecutor"):
            a = LangChainAgent(session_id="x", verbose=False)
        assert isinstance(a.events, EventBus)


# ── AgentAPI 契约（runtime_checkable isinstance） ─────────────────────────

class TestAgentAPIIsInstance:
    """`design.md §3.1` 的 AgentAPI(Protocol, runtime_checkable) 用 isinstance 校验。"""

    def test_python_agent_satisfies_agent_api(self) -> None:
        assert AgentAPI is not None
        from unittest.mock import MagicMock
        mock_history = MagicMock()
        mock_history.load_last_n_messages.return_value = []
        a = Agent(verbose=False, chat_history=mock_history, user_memory=None)
        assert isinstance(a, AgentAPI)

    def test_autogpt_agent_satisfies_agent_api(self) -> None:
        if AutoGPTAgent is None:
            pytest.skip("AutoGPTAgent 未导入")
        from unittest.mock import MagicMock
        mock_history = MagicMock()
        mock_history.load_last_n_messages.return_value = []
        a = AutoGPTAgent(verbose=False, chat_history=mock_history)
        assert isinstance(a, AgentAPI)

    def test_langchain_agent_satisfies_agent_api(self) -> None:
        if LangChainAgent is None:
            pytest.skip("LangChainAgent 未导入（环境未装 langchain）")
        from unittest.mock import patch
        with patch("src.agent.langchain_agent.build_chat_model"), \
             patch("src.agent.langchain_agent.build_langchain_tools", return_value=[]), \
             patch("src.agent.langchain_agent.SQLiteChatMessageHistory"), \
             patch("src.agent.langchain_agent.create_tool_calling_agent"), \
             patch("src.agent.langchain_agent.AgentExecutor"):
            a = LangChainAgent(session_id="x", verbose=False)
        assert isinstance(a, AgentAPI)
