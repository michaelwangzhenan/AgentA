"""
ToolCallEngine —— 工具调用编排（Helper 层）

职责：
- 把 LLM 返回的 assistant.tool_calls 转成标准 dict（`assistant_message`）
- 逐个执行 tool_call → 拿到 ToolResult
- DB 写入"干净内容"（无引导提示），messages 注入"含引导提示"版本
  · 这种"写历史 vs 进 LLM context"的分支属于业务策略，是 Helper 的核心价值
- 全程串到 SessionStore（依赖层），不感知 thinking / streaming 等其它 loop 状态

被三种 Agent 实现共享：Python / LangChain / AutoGPT。
"""
from __future__ import annotations

import contextvars
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from src.agent.core.event_bus import (
    EVENT_PLAN_CREATED,
    EVENT_PLAN_STEP_END,
    EVENT_PLAN_STEP_START,
    EVENT_TOOL_CALL_END,
    EVENT_TOOL_CALL_START,
    AgentEvent,
    EventBus,
    tool_progress_scope,
)
from src.agent.tools import execute_tool, ToolResult
from src.stores.session_store import SessionStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.agent.core.citation_builder import CitationBuilder  # noqa: F401

logger = logging.getLogger(__name__)

# 工具结果预览截断长度
_TOOL_PREVIEW_LEN: int = 100

# plan 类 tool：有顺序依赖（审批 / reconstruct / 事件），永远串行执行，排除在并行外
_PLAN_TOOLS: frozenset[str] = frozenset({"make_plan", "update_step", "abort_plan"})

# 同一轮多个工具并行执行的线程数上限；多为网络 IO，封顶避免并行精排把 CPU 打满
_MAX_PARALLEL_TOOLS: int = 4

# search_knowledge 返回空结果时追加给 LLM 的引导提示
TOOL_EMPTY_HINT: str = (
    "\n\n[提示] 知识库中未找到相关内容，请立即调用 web_search 工具搜索关键词，"
    "再从返回的真实 URL 中选择合适的链接调用 fetch_url，不允许直接回答。"
)


class ToolCallEngine:
    """
    工具调用编排 helper。

    Args:
        session_store:    SessionStore 实例（CRUD 依赖）。
        session_id:       当前会话 ID。
        skill_bodies:     已加载的 skill 正文映射，传给 execute_tool 用于 load_skill。
        verbose:          是否打印调用 / 结果预览（CLI 调试用）。
        events:           可选 EventBus；非 None 时发出 tool_call_start / tool_call_end 事件。
        citation_builder: 可选引用编排器；非 None 时透传给 execute_tool，
                          仅 search_knowledge 路径会注册 hits、分配编号。
    """

    def __init__(
        self,
        session_store: SessionStore,
        session_id: str,
        skill_bodies: dict[str, str],
        verbose: bool = False,
        events: EventBus | None = None,
        citation_builder: "CitationBuilder | None" = None,
        approval_fn: "Callable[[dict[str, Any]], str] | None" = None,
    ) -> None:
        self._session_store = session_store
        self._session_id = session_id
        self._skill_bodies = skill_bodies
        self._verbose = verbose
        self._events = events
        self._citation_builder = citation_builder
        # plan-execute 用户审批入口；非 None 时在 make_plan 后调用，
        # 返 "no" 即抛 PlanAbortedByUser 让 agent.run 中止当前 query
        self._approval_fn = approval_fn

    def process(self, message: Any, messages: list[dict[str, Any]]) -> None:
        """
        执行本轮所有 tool_calls，将结果注入 `messages` 并写入 DB。

        DB 写入使用不含引导提示的干净内容；当前轮 messages 注入含引导提示的版本，
        避免引导提示污染下次加载的历史记录。
        """
        assistant_msg = self.assistant_message(message)
        # thinking provider 的多轮工具调用：本轮 reasoning_content 必须随 assistant 消息回传，
        # 否则部分 provider（如 kimi）下一轮直接 400。只挂到内存 messages 供后续轮次发送，
        # 不写入 session_store（thinking 内容不持久化，防 prompt injection）。
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            messages.append({**assistant_msg, "reasoning_content": reasoning})
        else:
            messages.append(assistant_msg)
        self._session_store.append(self._session_id, assistant_msg)

        parsed = [
            (tc, tc.function.name, json.loads(tc.function.arguments))
            for tc in message.tool_calls
        ]

        # 同一轮 ≥2 个工具且都不是 plan tool 时并行执行：工具多为网络 IO，
        # GIL 不挡；plan tool 有顺序依赖（审批 / reconstruct）故排除、退回串行。
        if len(parsed) >= 2 and all(name not in _PLAN_TOOLS for _, name, _ in parsed):
            # 先按序发 tool_call_start，保证 UI 里工具内部 progress 事件不早于 start
            for tc, name, args in parsed:
                self._emit_start(tc, name, args)
            results = self._run_parallel(parsed, messages)
            for (tc, name, args), result in zip(parsed, results):
                self._consume_result(tc, name, args, result, messages)
            return

        # 串行路径（单工具 / 含 plan tool 轮）：逐个 start → 执行 → 落地，
        # 保证 plan tool 执行时 messages 已含本轮先前工具结果。
        for tc, name, args in parsed:
            self._emit_start(tc, name, args)
            with tool_progress_scope(self._events, tc.id):
                result = self._exec(name, args, messages)
            self._consume_result(tc, name, args, result, messages)

    def _emit_start(self, tool_call: Any, tool_name: str, tool_args: dict[str, Any]) -> None:
        """打调用日志（verbose）+ 发 tool_call_start 事件。"""
        if self._verbose:
            logger.info(
                "[ToolCallEngine] 调用工具: %s，参数: %s",
                tool_name,
                json.dumps(tool_args, ensure_ascii=False),
            )
        if self._events is not None:
            self._events.publish(AgentEvent(
                type=EVENT_TOOL_CALL_START,
                payload={"name": tool_name, "args": tool_args, "call_id": tool_call.id},
            ))

    def _exec(
        self, tool_name: str, tool_args: dict[str, Any], messages: list[dict[str, Any]],
    ) -> ToolResult:
        """执行单个工具。

        仅当持有 citation_builder 时才走 kwarg 路径，否则保持现有 3-arg 签名，
        避免破坏外部 mock execute_tool 的测试 fixture。messages 透传给 plan tools
        （update_step / abort_plan）用于 reconstruct；非 plan 工具忽略此参数。
        """
        if self._citation_builder is not None:
            return execute_tool(
                tool_name, tool_args, self._skill_bodies,
                citation_builder=self._citation_builder,
                messages=messages,
            )
        return execute_tool(
            tool_name, tool_args, self._skill_bodies,
            messages=messages,
        )

    def _run_parallel(
        self,
        parsed: list[tuple[Any, str, dict[str, Any]]],
        messages: list[dict[str, Any]],
    ) -> list[ToolResult]:
        """并行执行多个非 plan 工具，结果按入参顺序返回。

        每个 worker 在自己线程内 set tool_progress contextvar，工具内部
        publish_tool_progress 才能拿到 (bus, call_id)。execute_tool 自身捕获所有
        异常并以 status=error 返回，故 future 不会抛出。并行期间不改 messages（只读透传），
        落地（写历史 / 追加 messages）统一在主线程串行做，保证顺序与 plan 一致性。

        子线程默认不继承父 context，故每个任务在主线程复制一份当前 context 带进去
        （`copy_context().run`）——让日志的 session/request、user/llm_prefs 等 contextvar
        在工具线程内仍有效（否则并行工具的日志会丢成 `s:- r:-`，无法串链路）。
        每个任务各自一份独立 copy，互不串扰；tool_progress 仍在各自 copy 内现场设。
        """
        def work(tool_call: Any, name: str, args: dict[str, Any]) -> ToolResult:
            with tool_progress_scope(self._events, tool_call.id):
                return self._exec(name, args, messages)

        with ThreadPoolExecutor(max_workers=min(len(parsed), _MAX_PARALLEL_TOOLS)) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, work, tc, name, args)
                for tc, name, args in parsed
            ]
            return [f.result() for f in futures]

    def _consume_result(
        self,
        tool_call: Any,
        tool_name: str,
        tool_args: dict[str, Any],
        result: ToolResult,
        messages: list[dict[str, Any]],
    ) -> None:
        """工具执行后的统一落地：结果日志 / tool_call_end / 写历史 / 注入 messages / plan 事件。"""
        if self._verbose:
            preview = result.content[:_TOOL_PREVIEW_LEN].replace("\n", " ").replace("\r", " ")
            logger.info("[ToolCallEngine] 工具结果 [%s] 预览: %s", result.status, preview)

        if self._events is not None:
            preview = result.content[:_TOOL_PREVIEW_LEN].replace("\n", " ").replace("\r", " ")
            self._events.publish(AgentEvent(
                type=EVENT_TOOL_CALL_END,
                payload={"call_id": tool_call.id, "status": result.status, "preview": preview},
            ))

        # DB 写入干净内容（无引导提示），避免污染历史。
        # 先写入 tool 结果再调 plan 审批 hook，保证 PlanAbortedByUser
        # 抛出时 session_store 一致性（assistant_msg 已写入 + tool_msg 已写入）。
        db_content = result.to_llm_str()
        if tool_name == "load_skill" and result.status == "ok":
            from src.agent.core.skill_loader import skill_ref_stub

            skill_name = str(tool_args.get("name") or "")
            raw_body = ""
            if skill_name:
                raw_body = self._skill_bodies.get(skill_name, "")
            db_content = skill_ref_stub(skill_name, raw_body) if skill_name else db_content
        db_msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": db_content,
        }
        self._session_store.append(self._session_id, db_msg)

        # 当前轮 messages 注入含引导提示的版本，引导 LLM 下一步决策
        llm_content = db_content
        if result.status == "error":
            llm_content += "\n\n[提示] 请换一种方式（换参数或换工具）重试，不要直接回答。"
        elif result.status == "empty" and tool_name == "search_knowledge":
            llm_content += TOOL_EMPTY_HINT

        live_msg: dict[str, Any] = {**db_msg, "content": llm_content}
        messages.append(live_msg)

        # plan tool 调用成功后叠加发 plan_* 事件，供 CLI 渲染 plan checkbox 进度。
        # reconstruct_from_messages 此时 messages 已含本轮 assistant tool_calls，
        # 所以 update_step 状态即得最新 plan 视图。make_plan 分支内会调 approval_fn，
        # 用户拒绝即抛 PlanAbortedByUser；此时 session_store 已含 assistant_msg + tool_msg
        # （一致性保住），让上游 agent.run 接住异常后追加 cancel_msg 即可。
        if self._events is not None and result.status == "ok":
            self._maybe_publish_plan_events(tool_name, tool_args, messages)

    def _maybe_publish_plan_events(
        self, tool_name: str, tool_args: dict[str, Any], messages: list[dict[str, Any]],
    ) -> None:
        """plan tool 调用成功后 publish 对应 plan_* 事件。

        make_plan 分支在 publish plan_created 前调用 approval_fn 询问用户；
        用户回 "no" → 抛 PlanAbortedByUser 让上游 agent.run 中止 query。
        """
        if self._events is None:
            return
        if tool_name == "make_plan":
            steps = tool_args.get("steps") or []
            if not (isinstance(steps, list) and all(isinstance(s, str) and s.strip() for s in steps)):
                return
            cleaned = [s.strip() for s in steps]
            plan_payload = {"steps": [{"id": i + 1, "text": t} for i, t in enumerate(cleaned)]}

            if self._approval_fn is not None:
                answer = self._approval_fn(plan_payload)
                if answer == "no":
                    from src.agent.agent import PlanAbortedByUser
                    raise PlanAbortedByUser(f"用户拒绝 plan：{cleaned}")

            self._events.publish(AgentEvent(
                type=EVENT_PLAN_CREATED,
                payload=plan_payload,
            ))
            self._events.publish(AgentEvent(
                type=EVENT_PLAN_STEP_START,
                payload={"step_id": 1, "text": cleaned[0]},
            ))
        elif tool_name == "update_step":
            step_id = tool_args.get("step_id")
            status = tool_args.get("status")
            note = tool_args.get("note", "") or ""
            if not (isinstance(step_id, int) and isinstance(status, str)):
                return
            self._events.publish(AgentEvent(
                type=EVENT_PLAN_STEP_END,
                payload={"step_id": step_id, "status": status, "note": note},
            ))
            from src.agent.core.plan_manager import reconstruct_from_messages
            state = reconstruct_from_messages(messages)
            if state is not None and not state.is_complete():
                nxt = state.next_pending_step()
                if nxt is not None:
                    self._events.publish(AgentEvent(
                        type=EVENT_PLAN_STEP_START,
                        payload={"step_id": nxt.id, "text": nxt.text},
                    ))
        # abort_plan 不发 plan_* 事件（plan 终结由 final_answer 渲染说明）

    @staticmethod
    def assistant_message(message: Any) -> dict[str, Any]:
        """将 LLM 返回的 assistant message 转换为标准 dict 格式。"""
        tool_calls_data = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]
        return {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": tool_calls_data,
        }
