"""
LangChainAgent —— 基于 LangChain 1.x `create_agent`（LangGraph）的 Agent 实现

设计取舍（design.md §5 / iter_a_LangChain.md）：
- 与 Python `Agent` / `AutoGPTAgent` 共享公共层：tools / LLM provider / Skill loader /
  ChatHistoryStore / HistoryManager / EventBus / CitationBuilder / MemoryManager /
  agent_commons（SYSTEM_PROMPT / 四层 prompt 组装 / plan 审批）；差异只在 loop 由
  LangChain 的 LangGraph runtime 接管。本文件 import 公共层，不反向依赖 Python 实现。
- 用 langchain 1.x 的 `create_agent`（取代已被移除的 legacy `AgentExecutor`）。
- 四层 system prompt 与 Python Agent 同构：base(+skill catalog) → <project_rules>
  → <user_context> → <active_study_plan>，每轮 run() 动态拼接后重建 agent。
- 事件桥接：`_EventBridgeHandler`(BaseCallbackHandler) 把 token / tool_call_start /
  tool_call_end / plan_* 事件转发到 EventBus，对齐 CLI 分层渲染。
- 对齐项：历史截断（HistoryManager）、token 统计（usage_metadata）、plan 审批门、
  thinking budget（best-effort）。
- 已知限制（iter_a §4）：thinking_chunk 不单独发；plan 进度为 best-effort 解析。
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.agent.core.agent_commons import (
    PlanAbortedByUser,
    SYSTEM_PROMPT,
    TokenUsage,
    build_layered_system_prompt,
    get_shared_chat_history,
    resolve_plan_approval,
)
from src.agent.core.citation_builder import CitationBuilder
from src.agent.core.event_bus import (
    ALL_EVENT_TYPES,
    EVENT_ERROR,
    EVENT_FINAL_ANSWER,
    EVENT_PLAN_CREATED,
    EVENT_PLAN_STEP_END,
    EVENT_PLAN_STEP_START,
    EVENT_TOKEN_CHUNK,
    EVENT_TOOL_CALL_END,
    EVENT_TOOL_CALL_START,
    AgentEvent,
    EventBus,
)
from src.agent.langchain_tools import build_langchain_tools
from src.llm.langchain_provider import build_chat_model
from src.llm.provider import chat
from src.memory.langchain_history import load_truncated_lc_messages

logger = logging.getLogger(__name__)

_TOOL_PREVIEW_LEN = 100
_PLAN_CANCEL_MSG = "已按用户要求取消执行 plan。如需重新规划请发起新提问。"


def _content_to_text(content: Any) -> str:
    """把 LangChain message.content（str 或 block 列表）规约为纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


class _EventBridgeHandler(BaseCallbackHandler):
    """把 LangChain 回调桥接为 AgentA 的 EventBus 事件。

    覆盖：
    - on_llm_new_token  → token_chunk（需 ChatModel streaming=True）
    - on_tool_start     → tool_call_start；make_plan/update_step 额外补发 plan_* 事件
    - on_tool_end       → tool_call_end

    plan_* 事件为 best-effort：从 tool 入参解析（详 iter_a §4）。
    """

    def __init__(self, events: EventBus) -> None:
        self._events = events

    # ── token 流 ──────────────────────────────────────────────────────────
    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if token:
            self._events.publish(AgentEvent(type=EVENT_TOKEN_CHUNK, payload={"text": token}))

    # ── 工具调用 ──────────────────────────────────────────────────────────
    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        **kwargs: Any,
    ) -> None:
        name = (serialized or {}).get("name", "") or kwargs.get("name", "")
        call_id = str(kwargs.get("run_id", ""))
        args = self._parse_inputs(input_str, kwargs.get("inputs"))
        self._events.publish(AgentEvent(
            type=EVENT_TOOL_CALL_START,
            payload={"name": name, "args": args, "call_id": call_id},
        ))
        self._maybe_emit_plan_events(name, args)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        call_id = str(kwargs.get("run_id", ""))
        text = output if isinstance(output, str) else str(output)
        preview = text[:_TOOL_PREVIEW_LEN].replace("\n", " ")
        self._events.publish(AgentEvent(
            type=EVENT_TOOL_CALL_END,
            payload={"call_id": call_id, "status": "ok", "preview": preview},
        ))

    # ── 内部 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_inputs(input_str: str, inputs: Any) -> dict[str, Any]:
        if isinstance(inputs, dict):
            return inputs
        try:
            parsed = json.loads(input_str)
            return parsed if isinstance(parsed, dict) else {"input": parsed}
        except (json.JSONDecodeError, TypeError):
            return {"input": input_str}

    def _maybe_emit_plan_events(self, name: str, args: dict[str, Any]) -> None:
        """best-effort 补发 plan 事件，让 CLI plan 渲染可见。"""
        if name == "make_plan":
            steps = args.get("steps") or []
            if isinstance(steps, list) and steps:
                payload_steps = [{"id": i, "text": str(s)} for i, s in enumerate(steps, 1)]
                self._events.publish(AgentEvent(
                    type=EVENT_PLAN_CREATED, payload={"steps": payload_steps},
                ))
                self._events.publish(AgentEvent(
                    type=EVENT_PLAN_STEP_START,
                    payload={"step_id": 1, "text": payload_steps[0]["text"]},
                ))
        elif name == "update_step":
            step_id = args.get("step_id")
            status = args.get("status")
            if step_id is not None and status:
                self._events.publish(AgentEvent(
                    type=EVENT_PLAN_STEP_END,
                    payload={"step_id": step_id, "status": status, "note": args.get("note", "")},
                ))


class LangChainAgent:
    """
    LangChain 实现的 ReAct Agent（loop 由 `create_agent` / LangGraph 托管）。

    实例属性：
        events:            `EventBus` 实例
        session_id:        会话 ID
        last_usage:        最近一次 run() 的 `TokenUsage`（拿不到则 None）
        thinking_cfg:      Extended Thinking 配置（best-effort 透传到 ChatModel）
        approval_callback: plan 审批回调（CLI 自动挂载；约定返 "yes"/"no"）
    """

    def __init__(
        self,
        system_prompt: str = "",
        verbose: bool = True,
        session_id: str | None = None,
        max_history_turns: int = 20,
        chat_history: Any = None,
        skills: dict | None = None,
        thinking_config: Any = None,
        user_memory: Any = None,
        approval_callback: Callable[[dict[str, Any]], str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._session_id: str = session_id or str(uuid.uuid4())
        self.verbose = verbose
        self.max_history_turns = max_history_turns
        self.thinking_cfg = thinking_config
        self._user_memory = user_memory
        self.last_usage: TokenUsage | None = None
        # plan 审批回调：CLI 在 run 前置 agent.approval_callback；拒绝即抛 PlanAbortedByUser
        self.approval_callback = approval_callback
        self._plan_aborted: bool = False

        # base system_prompt：常量 + skill catalog（与 Python / AutoGPT 同构）
        base = system_prompt or SYSTEM_PROMPT
        self._skill_bodies: dict[str, str] = {}
        if skills:
            from src.skills.skill_loader import build_skill_catalog
            self._skill_bodies = {name: info.body for name, info in skills.items()}
            base = base + build_skill_catalog(skills)
        self._system_prompt: str = base

        # 事件总线：与 Python / AutoGPT 接口对齐
        self.events: EventBus = EventBus()

        # 每轮 run() 重置的 CitationBuilder（工具闭包通过 getter 读取）
        self._citation: CitationBuilder | None = None

        # 共享 ChatHistoryStore（与 Python / AutoGPT 同源，避免跨实现污染）
        self._chat_history = chat_history if chat_history is not None else get_shared_chat_history()

        # 构造 LLM（streaming 驱动 token_chunk；thinking best-effort）+ tools（动态全覆盖）
        self._llm = build_chat_model(streaming=True, thinking_cfg=self.thinking_cfg)
        self._tools = self._build_tools()

    # ── 核心循环 ───────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
        event_callback: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        """
        执行一次推理：四层 prompt 拼接 → 截断历史 → create_agent.invoke → 引用回填 →
        写回历史 → 记忆提取。

        失败时不抛异常，返回 `Error: <message>`（失败前发 EVENT_ERROR）。plan 审批被
        用户拒绝时返回取消提示（aborted_by_user）。无论成败都发 EVENT_FINAL_ANSWER。
        """
        if session_id is not None and session_id != self._session_id:
            self._session_id = session_id
        if event_callback is not None:
            self.set_event_callback(event_callback)

        sid = self._session_id
        self._citation = CitationBuilder()
        self._plan_aborted = False
        self.last_usage = None

        # 四层 system prompt：base(+catalog) → <project_rules> → <user_context> → <active_study_plan>
        system_content, memory_mgr = build_layered_system_prompt(
            self._system_prompt,
            session_id=sid,
            user_memory=self._user_memory,
            chat_history=self._chat_history,
            llm_chat=chat,
        )

        # 截断历史（复用公共层 HistoryManager）→ LangChain 消息（不含本轮 user）
        history_msgs = load_truncated_lc_messages(self._chat_history, sid, self.max_history_turns)
        messages_in: list[BaseMessage] = [*history_msgs, HumanMessage(content=user_input)]

        agent = self._build_agent(system_content)
        handler = _EventBridgeHandler(self.events)
        usage: TokenUsage | None = None
        try:
            result = agent.invoke(
                {"messages": messages_in},
                config={"callbacks": [handler]},
            )
            out_msgs = result.get("messages", []) if isinstance(result, dict) else []
            answer = self._extract_answer(out_msgs)
            usage = self._sum_usage(out_msgs)
        except PlanAbortedByUser as exc:  # 理论上被 ToolNode 兜住，这里做双保险
            logger.info("[LangChainAgent] plan 被用户拒绝（异常路径）：%s", exc)
            self._plan_aborted = True
            answer = ""
        except Exception as e:
            logger.error("LangChainAgent.run error: %s", e)
            self.events.publish(AgentEvent(
                type=EVENT_ERROR,
                payload={"message": str(e), "recoverable": False, "phase": "agent_invoke"},
            ))
            answer = "Error: " + str(e)

        # plan 审批被拒：给出确定性取消回答（不论 LLM 在 ToolNode 错误后产出什么）
        if self._plan_aborted:
            self.last_usage = usage
            self._persist(user_input, _PLAN_CANCEL_MSG)
            self.events.publish(AgentEvent(
                type=EVENT_FINAL_ANSWER,
                payload={"text": _PLAN_CANCEL_MSG, "usage": usage, "aborted_by_user": True},
            ))
            return _PLAN_CANCEL_MSG

        # 引用回填：扫正文实际引到的 [n]，渲染 sources 块拼到末尾（无引用则原样）
        try:
            used = self._citation.extract_used(answer)
            sources_block = self._citation.render(used)
            if sources_block:
                answer = answer + sources_block
        except Exception as e:  # 引用渲染失败不应吞掉回答
            logger.warning("[LangChainAgent] citation 渲染失败，已忽略: %s", e)

        self._persist(user_input, answer)

        # 记忆自动 / 显式提取（失败静默，不影响主流程）
        try:
            memory_mgr.try_extract(user_input, answer)
        except Exception as e:
            logger.warning("[LangChainAgent] 记忆提取异常，已忽略: %s", e)

        self.last_usage = usage
        self.events.publish(AgentEvent(
            type=EVENT_FINAL_ANSWER,
            payload={"text": answer, "usage": usage},
        ))
        return answer

    # ── Skill 注入 ─────────────────────────────────────────────────────────

    def activate_skill(self, name: str, body: str) -> bool:
        """注入 Skill 到 system_prompt 并从工具枚举中移除，重建工具集。"""
        open_tag = f'<skill_content name="{name}">'
        if open_tag in self._system_prompt:
            return False
        self._system_prompt += f"\n\n{open_tag}\n{body}\n</skill_content>"
        self._skill_bodies.pop(name, None)
        # 工具集合变了（load_skill 枚举），重建 tools；agent 在下轮 run() 动态重建
        self._tools = self._build_tools()
        logger.info("[LangChainAgent] Skill [%s] activated", name)
        return True

    # ── 事件订阅（接口对齐 AgentAPI）───────────────────────────────────────

    def set_event_callback(self, callback: Callable[[AgentEvent], None] | None) -> None:
        """
        设置统一事件回调（覆盖语义）：传 None 清空所有事件订阅。

        本实现发出：token_chunk / tool_call_start / tool_call_end / plan_* /
        final_answer / error；thinking_chunk 暂不发（详 iter_a §4）。
        """
        self.events.clear()
        if callback is None:
            return
        for evt_type in ALL_EVENT_TYPES:
            def _wrapper(payload: Any, _t: str = evt_type) -> None:
                callback(AgentEvent(type=_t, payload=payload))
            self.events.subscribe(evt_type, _wrapper)

    # ── plan 审批 ──────────────────────────────────────────────────────────

    def request_plan_approval(self, plan_payload: dict[str, Any]) -> str:
        """与 Python Agent 同源的审批裁决（PLAN_PERMISSION_MODE + approval_callback）。"""
        return resolve_plan_approval(self.approval_callback, plan_payload)

    def _approve_plan(self, steps: list[Any]) -> None:
        """make_plan 工具成功后的审批 hook：拒绝则置 abort flag 并抛 PlanAbortedByUser。"""
        payload = {"steps": [{"id": i, "text": str(s)} for i, s in enumerate(steps, 1)]}
        if self.request_plan_approval(payload) == "no":
            self._plan_aborted = True
            raise PlanAbortedByUser("用户拒绝 plan")

    # ── 内部 ───────────────────────────────────────────────────────────────

    def _build_tools(self) -> list[Any]:
        return build_langchain_tools(
            self._skill_bodies if self._skill_bodies else None,
            citation_getter=lambda: self._citation,
            approval_fn=self._approve_plan,
        )

    def _build_agent(self, system_content: str):
        """用 `create_agent` 构造编译后的 LangGraph（system_prompt 传 SystemMessage 字面量，
        避免 SYSTEM_PROMPT 内 `{...}` 被当模板解析）。"""
        return create_agent(
            self._llm,
            self._tools,
            system_prompt=SystemMessage(content=system_content),
        )

    @staticmethod
    def _extract_answer(messages: list[BaseMessage]) -> str:
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                text = _content_to_text(m.content)
                if text:
                    return text.strip()
        return ""

    @staticmethod
    def _sum_usage(messages: list[BaseMessage]) -> TokenUsage | None:
        prompt = comp = total = 0
        found = False
        for m in messages:
            um = getattr(m, "usage_metadata", None)
            if um:
                found = True
                prompt += int(um.get("input_tokens", 0) or 0)
                comp += int(um.get("output_tokens", 0) or 0)
                total += int(um.get("total_tokens", 0) or 0)
        if not found:
            return None
        return TokenUsage(prompt, comp, total or (prompt + comp))

    def _persist(self, user_input: str, answer: str) -> None:
        """只持久化 user + 最终 assistant（省略中间工具轮次），共享 store。"""
        try:
            self._chat_history.append(self._session_id, {"role": "user", "content": user_input})
            self._chat_history.append(self._session_id, {"role": "assistant", "content": answer})
        except Exception as e:
            logger.warning("[LangChainAgent] 历史写回失败，已忽略: %s", e)
