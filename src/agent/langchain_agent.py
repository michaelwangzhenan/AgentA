"""
LangChainAgent —— 基于 LangChain `AgentExecutor` 的 Agent 实现

设计取舍（design.md §5）：
- 与 Python `Agent` / `AutoGPTAgent` 共享公共层：tools / LLM provider / Skill loader /
  ChatHistoryStore / EventBus；差异只在 loop 由 LangChain 的 AgentExecutor 接管
- 当前用 langchain 0.3 的 `create_tool_calling_agent + AgentExecutor` 旧 API
  （`langchain.agents.create_agent` 是 1.0+ 的新 API，环境未升级）
- 事件接口对齐：暴露 `events: EventBus` + `set_thinking/token_callback` 转发，
  与另两种实现接口一致。流式 thinking / token 视 LangChain 是否支持后续接入。
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.agent.core.event_bus import (
    ALL_EVENT_TYPES,
    EVENT_ERROR,
    EVENT_FINAL_ANSWER,
    AgentEvent,
    EventBus,
)
from src.agent.langchain_tools import build_langchain_tools
from src.llm.langchain_provider import build_chat_model
from src.memory.langchain_history import SQLiteChatMessageHistory

logger = logging.getLogger(__name__)


def _default_system_prompt() -> str:
    """延迟 import 以避免循环依赖。"""
    from src.agent.agent import SYSTEM_PROMPT
    return SYSTEM_PROMPT


class LangChainAgent:
    """
    LangChain 实现的 ReAct Agent。

    实例属性：
        events:      `EventBus` 实例（接口对齐，AgentExecutor 当前未走 streaming 路径）
        session_id:  会话 ID
        last_usage:  最近一次 run() 的 token 统计（LangChain 默认不暴露细分 token，留 None）
    """

    last_usage = None

    def __init__(
        self,
        system_prompt: str = "",
        verbose: bool = True,
        session_id: str | None = None,
        chat_history: Any = None,  # 接口对齐，本实现走 SQLiteChatMessageHistory 不直读 ChatHistoryStore
        skills: dict | None = None,
        thinking_config: Any = None,  # 接口对齐，LangChain 子实现暂不启用 thinking
        user_memory: Any = None,
        **kwargs: Any,
    ) -> None:
        self._session_id: str = session_id or str(uuid.uuid4())
        self._system_prompt: str = system_prompt or _default_system_prompt()
        self.verbose = verbose
        self.thinking_cfg = thinking_config
        self._skill_bodies: dict[str, str] = {}
        if skills:
            self._skill_bodies = {name: info.body for name, info in skills.items()}

        # 事件总线：与 Python / AutoGPT 接口对齐
        self.events: EventBus = EventBus()

        # SQLite 落地的 LangChain BaseChatMessageHistory（与 ChatHistoryStore 共享 DB）
        self._history = SQLiteChatMessageHistory(self._session_id)

        # 构造 LLM + tools + executor
        self._llm = build_chat_model()
        self._tools = build_langchain_tools(self._skill_bodies if self._skill_bodies else None)
        self._executor = self._build_executor(self._system_prompt)

    # ── 核心循环 ───────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self, user_input: str) -> str:
        """
        执行一次 ReAct 推理：拉历史 → 调 AgentExecutor → 写回历史。

        失败时不抛异常向上，返回 `Error: <message>` 字符串（与原实现一致）；
        失败前会发出 EVENT_ERROR 事件。无论成功失败都会发出 EVENT_FINAL_ANSWER。
        """
        chat_history = self._history.messages  # 不含本轮 user
        try:
            result = self._executor.invoke({
                "input": user_input,
                "chat_history": chat_history,
            })
            answer = (result.get("output") or "").strip()
        except Exception as e:
            logger.error("LangChainAgent.run error: %s", e)
            self.events.publish(AgentEvent(
                type=EVENT_ERROR,
                payload={"message": str(e), "recoverable": False, "phase": "executor_invoke"},
            ))
            answer = "Error: " + str(e)

        # 写回历史（与原实现一致：只持久化 user + 最终 assistant，省略中间工具轮次）
        self._history.add_message(HumanMessage(content=user_input))
        self._history.add_message(AIMessage(content=answer))
        self.events.publish(AgentEvent(
            type=EVENT_FINAL_ANSWER,
            payload={"text": answer, "usage": None},
        ))
        return answer

    # ── Skill 注入 ─────────────────────────────────────────────────────────

    def activate_skill(self, name: str, body: str) -> bool:
        """注入 Skill 到 system_prompt 并从工具枚举中移除，重建 executor。"""
        open_tag = f'<skill_content name="{name}">'
        if open_tag in self._system_prompt:
            return False
        self._system_prompt += f"\n\n{open_tag}\n{body}\n</skill_content>"
        self._skill_bodies.pop(name, None)
        # 工具集合与 prompt 都变了，重建 executor
        self._tools = build_langchain_tools(self._skill_bodies if self._skill_bodies else None)
        self._executor = self._build_executor(self._system_prompt)
        logger.info("[LangChainAgent] Skill [%s] activated", name)
        return True

    # ── 事件订阅（接口对齐 AgentAPI）───────────────────────────────────────

    def set_event_callback(self, callback: Callable[[AgentEvent], None] | None) -> None:
        """
        设置统一事件回调（覆盖语义）：传 None 清空所有事件订阅。

        本实现当前只在 run() 发出 `final_answer` 与 `error`（D3=A，详见 iter_2.md §4.5.4）；
        其它事件类型订阅注册后不会立即收到数据。如需 thinking / token 流式，
        后续可接入 LangChain 的 `BaseCallbackHandler` 体系。
        """
        self.events.clear()
        if callback is None:
            return
        for evt_type in ALL_EVENT_TYPES:
            def _wrapper(payload: Any, _t: str = evt_type) -> None:
                callback(AgentEvent(type=_t, payload=payload))
            self.events.subscribe(evt_type, _wrapper)

    # ── 内部 ───────────────────────────────────────────────────────────────

    def _build_executor(self, system_prompt: str) -> AgentExecutor:
        """用 `create_tool_calling_agent` 构造 executor。

        注意：system_prompt 必须传 `SystemMessage` 实例，不能用 `("system", str)` 元组 ——
        后者会被 LangChain 当 f-string 模板解析，而 SYSTEM_PROMPT 含 `{...}` JSON schema
        字面量，会触发 "Nested replacement fields" 报错。
        `agent_scratchpad` 是 `create_tool_calling_agent` 约定的占位符名。
        """
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=system_prompt),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_tool_calling_agent(self._llm, self._tools, prompt)
        return AgentExecutor(agent=agent, tools=self._tools, verbose=self.verbose)
