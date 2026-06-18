"""
Agent 主控逻辑 —— ReAct（Reason + Act）循环

执行流程：
    1. 接收用户问题，从 SessionStore 加载历史消息
    2. 拼接为 [system] + history + [user]，超长时自动截断
    3. 调用 LLM（携带工具定义）
    4. 若 LLM 返回 tool_calls → 执行工具 → 将结果追加到 messages → 继续循环
    5. 若 LLM 直接返回文本 → 输出最终回答，退出循环
    6. 超过最大迭代次数时强制退出，防止死循环
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from src.agent.core.citation_builder import CitationBuilder
from src.agent.core.event_bus import (
    ALL_EVENT_TYPES,
    EVENT_ERROR,
    EVENT_FINAL_ANSWER,
    EVENT_INFO,
    EVENT_PLAN_STEP_END,
    EVENT_THINKING_CHUNK,
    EVENT_TOKEN_CHUNK,
    AgentEvent,
    EventBus,
)
from src.agent.core.history_manager import HistoryManager
from src.agent.core.memory_manager import MemoryManager
from src.agent.core.rules_loader import build_rules_block
from src.agent.core.thinking_policy import ThinkingConfig, ThinkingPolicy  # noqa: F401 — re-export
from src.agent.core.tool_call_engine import ToolCallEngine
# 公共层（三实现共享）：原先夹在本文件，已抽到 agent_commons；此处原名 re-export 保兼容。
from src.agent.core.agent_commons import (  # noqa: F401 — re-export
    SYSTEM_PROMPT,
    PlanAbortedByUser,
    TokenUsage,
    build_active_study_plan_block,
)
from src.agent.core.agent_commons import get_active_rules as _get_active_rules
from src.agent.core.agent_commons import get_shared_session_store as _get_shared_session_store
from src.agent.tools import get_tools
from src.agent.core.skill_loader import SkillInfo, build_skill_catalog
from src.llm.provider import chat, call_with_thinking
from src.stores.session_store import SessionStore
from src.stores.user_memory import UserMemoryStore
import src.config as _cfg

logger = logging.getLogger(__name__)


# 模块级共享 UserMemoryStore 实例（双检锁保护）
_shared_user_memory: UserMemoryStore | None = None
_shared_user_memory_lock = threading.Lock()

# 多用户：rules 改为按用户独享，存 UserStore（auth.db 的 user_rules 表）。
# 不再用进程级缓存的项目文件 —— 每轮按 current_user_id() 读当前用户的 rules。
# TokenUsage / SYSTEM_PROMPT / PlanAbortedByUser / _get_shared_session_store /
# _get_active_rules / build_active_study_plan_block 等公共资产已抽到 agent_commons
# 并在文件顶部 re-export（保持本模块旧 import 路径不变）。


def _get_shared_user_memory() -> UserMemoryStore | None:
    """
    获取模块级共享 UserMemoryStore（双检锁，线程安全懒加载）。

    USER_MEMORY_ENABLED=false 时返回 None，功能完全禁用。
    """
    if not _cfg.USER_MEMORY_ENABLED:
        return None
    global _shared_user_memory
    if _shared_user_memory is None:
        with _shared_user_memory_lock:
            if _shared_user_memory is None:
                _shared_user_memory = UserMemoryStore(_cfg.USER_MEMORY_DB_PATH)
    return _shared_user_memory


# 轮次上限（MAX_TOOL_ROUNDS / MAX_TOTAL_ROUNDS / MAX_HARD_CAP_ROUNDS）已移到 src.config，
# 运行时读 `_cfg.X` 以支持 UI / .env 改后实时生效。

# plan 步预算：每步预留 N 次 tool 调用（含业务 tool + update_step）
_PLAN_ROUNDS_PER_STEP: int = 4
# Plan-aware total 上限相对 tool 上限的额外余量（含 make_plan + final answer）
_PLAN_TOTAL_HEADROOM: int = 4


class Agent:
    """
    ReAct Agent：通过 LLM + Function Calling 实现推理与工具调用的循环。

    Attributes:
        system_prompt: Agent 的系统提示，定义行为策略。
        max_iterations: 最大总推理轮次（含工具调用和最终回答），超出后强制返回兜底回答。
            None（默认）= 跟随 `_cfg.MAX_TOTAL_ROUNDS`，UI / .env 改后实时生效；显式传值则固定用该值。
        verbose: 是否打印每轮工具调用的调试信息。
        session_id: 会话 ID，用于持久化对话历史。
        max_history_turns: 加载历史时保留最近 N 轮（一轮 = user + assistant），防止超出 context window。
    """

    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int | None = None,
        verbose: bool = True,
        session_id: str | None = None,
        max_history_turns: int = 20,
        session_store: SessionStore | None = None,
        skills: dict[str, SkillInfo] | None = None,
        thinking_config: ThinkingConfig | None = None,
        user_memory: UserMemoryStore | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        approval_callback: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        # 若传入 skills，提取 bodies，并将含 description 的 catalog 追加到 system_prompt
        self._skill_bodies: dict[str, str] = {}
        if skills:
            self._skill_bodies = {name: info.body for name, info in skills.items()}
            system_prompt = system_prompt + build_skill_catalog(skills)
        self.system_prompt = system_prompt
        # 显式传 max_iterations（CLI / 测试）→ 固定用该值；不传 → 跟随 _cfg.MAX_TOTAL_ROUNDS
        # 实时变化（进程级单例被 UI 改配置后，无需重建即生效）。
        self._max_iterations_explicit = max_iterations is not None
        self.max_iterations = max_iterations if max_iterations is not None else _cfg.MAX_TOTAL_ROUNDS
        self.verbose = verbose
        self.session_id: str = session_id or str(uuid.uuid4())
        self.max_history_turns = max_history_turns
        # 支持从外部传入 session_store（便于测试 mock），默认使用模块级共享实例
        self._session_store: SessionStore = (
            session_store if session_store is not None else _get_shared_session_store()
        )
        self.last_usage: TokenUsage | None = None  # 最近一次 run() 的 token 统计
        # Extended Thinking 配置：共享同一 ThinkingConfig 实例，修改后无需重建 Agent
        self.thinking_cfg: ThinkingConfig = thinking_config if thinking_config is not None else ThinkingConfig.from_config()
        # 事件总线：thinking / token / tool_call_start / tool_call_end / final_answer / error / info
        # 简单订阅一类事件：agent.events.subscribe(EVENT_X, fn)
        # 一次性接所有事件类型（带 type/ts 元信息）：agent.set_event_callback(fn)
        self.events: EventBus = EventBus()
        if on_thinking_chunk is not None:
            self.events.subscribe(EVENT_THINKING_CHUNK, on_thinking_chunk)
        # 跨 session 用户记忆：支持从外部传入（便于测试 mock），默认使用模块共享实例
        self._user_memory: UserMemoryStore | None = (
            user_memory if user_memory is not None else _get_shared_user_memory()
        )
        # plan 用户审批 mode：CLI 等 UI 端通过此回调挂自身交互逻辑
        # （CLI 走 input()）；callback 应返 "yes"/"no"
        self.approval_callback: Callable[[dict[str, Any]], str] | None = approval_callback

    def request_plan_approval(self, plan_payload: dict[str, Any]) -> str:
        """
        plan-execute 用户审批入口（make_plan 调用成功后由 tool_call_engine 调用）。

        触发条件需同时满足：
          - cfg.PLAN_PERMISSION_MODE=true
          - self.approval_callback is not None
        任一不满足 → 直接返 "yes"（保持默认放行行为）。

        Returns:
            str：callback 返回值；约定 "yes" 放行 / "no" 由调用方抛 PlanAbortedByUser。
            callback 抛任何异常 → log warning + 静默放行 "yes"（fail-open，避免 UI 异常
            把整个 query 卡死）。
        """
        if not _cfg.PLAN_PERMISSION_MODE or self.approval_callback is None:
            return "yes"
        try:
            answer = self.approval_callback(plan_payload)
        except Exception as exc:
            logger.warning("[Agent] approval_callback 异常 — 静默放行：%s", exc)
            return "yes"
        return (answer or "").strip().lower() or "yes"

    def _on_thinking_chunk(self, chunk: str) -> None:
        """向实例 bus（self.events）发思考流式片段。run() 并发路径用本次局部 bus，
        不走这里；此方法服务实例级订阅者（CLI / 测试）。"""
        self.events.publish(AgentEvent(type=EVENT_THINKING_CHUNK, payload={"text": chunk}))

    def _on_token_chunk(self, chunk: str) -> None:
        """向实例 bus（self.events）发正文 token 流式片段。无订阅者时静默。"""
        self.events.publish(AgentEvent(type=EVENT_TOKEN_CHUNK, payload={"text": chunk}))

    @staticmethod
    def _bind_callback(
        bus: EventBus, callback: Callable[[AgentEvent], None] | None
    ) -> None:
        """把统一事件回调绑定到指定 bus（覆盖语义）：先清空再为每种事件类型注册 wrapper。

        wrapper 把 payload + event_type 包装成 `AgentEvent` 转发给 callback。
        进程级单例并发时，每次 run 用独立 bus 调本方法，即可与其它请求隔离。
        """
        bus.clear()
        if callback is None:
            return
        for evt_type in ALL_EVENT_TYPES:
            def _wrapper(payload: Any, _t: str = evt_type) -> None:
                callback(AgentEvent(type=_t, payload=payload))
            bus.subscribe(evt_type, _wrapper)

    def set_event_callback(self, callback: Callable[[AgentEvent], None] | None) -> None:
        """
        设置实例级统一事件回调（覆盖语义）：传 None 清空所有事件订阅。

        作用于 self.events，服务 CLI 等单实例场景。Web API 多请求并发时不要走这里，
        改用 `run(..., event_callback=...)` 让本次运行用独立 bus，避免串台。

        需按事件类型 fine-grained 订阅时改用 `agent.events.subscribe(EVENT_X, fn)`。
        """
        self._bind_callback(self.events, callback)

    def run(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
        event_callback: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        """
        执行完整的 ReAct 循环，返回最终回答文本。

        会先从 SessionStore 加载历史消息，拼接到当前轮对话后一起发送给 LLM。
        每轮工具调用和最终回答均实时写入 SQLite。

        Args:
            user_input: 用户的自然语言问题。
            session_id: 本次运行使用的会话 ID；不传则用实例的 self.session_id。传入时
                只作用于本次调用、不写回实例字段 —— 让进程级单例 Agent 能被多请求并发
                调用而互不串台。
            event_callback: 本次运行的事件回调；传入时本次用一个独立的局部 EventBus
                （而非 self.events）与其它并发调用隔离。不传则沿用 self.events
                （CLI 等单实例场景：构造期订阅 + set_event_callback 设的回调）。

        Returns:
            Agent 的最终回答字符串。
        """
        # 多请求并发安全：session_id / 事件 bus / usage 全部局部化，不写共享实例字段。
        # 两者都不传 = 老行为（用实例字段），CLI / 测试零改动。
        sid = session_id or self.session_id
        own_state = session_id is None and event_callback is None
        if event_callback is not None:
            bus = EventBus()
            self._bind_callback(bus, event_callback)
        else:
            bus = self.events

        def _on_thinking(chunk: str) -> None:
            bus.publish(AgentEvent(type=EVENT_THINKING_CHUNK, payload={"text": chunk}))

        def _on_token(chunk: str) -> None:
            bus.publish(AgentEvent(type=EVENT_TOKEN_CHUNK, payload={"text": chunk}))

        def _token_cb() -> Callable[[str], None] | None:
            return _on_token if bus.subscribers(EVENT_TOKEN_CHUNK) else None

        # 加载历史，应用截断策略
        history_mgr = HistoryManager(self._session_store, sid, self.max_history_turns)
        history = history_mgr.load_truncated()

        # 构建 system 消息：base → <user_rules>（静态偏好）→ <user_context>（动态记忆）
        #                  → <active_study_plan>（当前 session 已 `/study load` 的学习计划）
        # 顺序原则：稳定基础在前 / 动态状态在后 —— 后注入的内容更易被 LLM 记住，
        # 学习计划与"下一步"决策强相关，放最末贴近 user 消息。
        # 注意：学习计划默认**不**注入，必须用户用 CLI `/study load [id]` 显式激活；
        # 对标 Agent Skills 的 load_skill 生命周期。
        memory_mgr = MemoryManager(self._user_memory, self._session_store, sid, chat)
        base_with_rules = self.system_prompt + build_rules_block(_get_active_rules())
        system_content = memory_mgr.build_system_prompt(base_with_rules)
        system_content = system_content + build_active_study_plan_block(sid)
        # 是否注入了个性化内容（user rules / 记忆 / 学习计划）—— 供语义缓存判定是否可缓存：
        # 个性化答案因人 / 因状态而异，不进缓存。
        _personalized = system_content.strip() != (self.system_prompt or "").strip()

        # 构建当前轮完整 messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
            *history,
            {"role": "user", "content": user_input},
        ]

        # 将当前轮用户输入写入 DB（首次会自动创建 session 记录）
        self._session_store.append(
            sid,
            {"role": "user", "content": user_input},
        )

        tool_rounds = 0  # 已消耗的工具调用轮次计数
        # 空 content 重试：部分 reasoning 模型（如 glm thinking）在长 tool 链后会把答案
        # 全塞进 reasoning_content、正文 content 留空。首次遇空时去掉 tools 再逼一轮正文，
        # 仍空才报错。force_no_tools 标记下一轮强制不带 tools。
        empty_retry_used = False
        force_no_tools = False
        _prompt_tokens = _comp_tokens = 0  # 本次 run() 各轮累计 token
        _llm_rounds: list[dict[str, Any]] = []  # 各轮 LLM 耗时，随 final_answer trace 透传
        # 每轮 new CitationBuilder，跨同轮多次 search_knowledge 累计编号
        citation_builder = CitationBuilder()
        tool_engine = ToolCallEngine(
            self._session_store, sid, self._skill_bodies,
            verbose=self.verbose, events=bus,
            citation_builder=citation_builder,
            approval_fn=self.request_plan_approval,
        )
        # 多用户：本请求若设了 thinking 偏好覆盖（use_llm_prefs），用覆盖值；
        # 否则回落到 agent 构造时的快照 self.thinking_cfg（CLI / 全局默认）。
        _thinking_ov = _cfg.current_thinking_override()
        if _thinking_ov is not None:
            thinking_policy = ThinkingPolicy(
                ThinkingConfig(enabled=_thinking_ov[0], budget=_thinking_ov[1])
            )
        else:
            thinking_policy = ThinkingPolicy(self.thinking_cfg)

        # session 启动事件（payload 含本轮基础元信息，供监听者关联日志）
        bus.publish(AgentEvent(
            type=EVENT_INFO,
            payload={"message": "agent.run.start", "session_id": sid},
        ))

        # 部分模型（如 Gemini 3.x 经 OpenAI 兼容层）无法多轮回传 thought_signature，
        # 带 tools 会在第二轮 400。这类模型整轮不传 tools，降级为纯聊天而不是报错。
        try:
            _, _active_model_cfg = _cfg.get_active_model()
            model_supports_tools = _active_model_cfg.supports_tools
        except Exception:
            model_supports_tools = True
        if not model_supports_tools:
            logger.warning("[Agent] 当前模型不支持工具调用，本轮降级为纯聊天（不启用 tools）")

        for iteration in range(1, _cfg.MAX_HARD_CAP_ROUNDS + 1):
            # 每轮按 active plan 步数重算 tool/total 上限（无 plan 退化为 baseline）
            eff_tool_max, eff_total_max = self._compute_effective_caps(messages)
            if iteration > eff_total_max:
                break  # 下方 fallback 路径处理"达最大迭代次数"
            logger.info(
                "[Agent] 第 %d 轮推理，messages 长度: %d，caps=(tool=%d, total=%d)",
                iteration, len(messages), eff_tool_max, eff_total_max,
            )

            # 工具轮次达上限（或模型不支持工具 / 空内容重试中）时，去掉 tools 参数，
            # 让 LLM 强制生成文本回答
            active_tools = (
                get_tools(self._skill_bodies)
                if (model_supports_tools and tool_rounds < eff_tool_max and not force_no_tools)
                else None
            )
            if active_tools is None and model_supports_tools and not force_no_tools:
                logger.warning("[Agent] 工具调用已达上限 %d 轮（含 plan 自适应），强制生成最终回答", eff_tool_max)

            # 调用 LLM：开启 thinking 时走流式 thinking 分支，否则普通 chat()
            _llm_t0 = time.perf_counter()
            try:
                if thinking_policy.enabled:
                    response = call_with_thinking(
                        messages,
                        budget_tokens=thinking_policy.effective_budget(),
                        tools=active_tools,
                        on_thinking_chunk=_on_thinking,
                        on_token_chunk=_token_cb(),
                    )
                else:
                    response = chat(messages, tools=active_tools, on_token_chunk=_token_cb())
            except Exception as exc:
                logger.error("[Agent] LLM 调用异常: %s", exc)
                bus.publish(AgentEvent(
                    type=EVENT_ERROR,
                    payload={"message": str(exc), "recoverable": False, "phase": "llm_call"},
                ))
                raise
            # 本轮 LLM 耗时记进 _llm_rounds（随 final_answer payload 的 trace 字段透传给采集器，
            # 不污染公共事件流）；end_ts 用 time.time()，与事件 ts 同一时钟，便于排瀑布。
            _llm_rounds.append({
                "round": iteration,
                "duration_ms": (time.perf_counter() - _llm_t0) * 1000.0,
                "end_ts": time.time(),
            })
            _u = getattr(response, "usage", None)
            if _u:
                _prompt_tokens += getattr(_u, "prompt_tokens", 0)
                _comp_tokens += getattr(_u, "completion_tokens", 0)
            message = response.choices[0].message

            # ── 情况 1：LLM 决定调用工具 ──────────────────────────────────────
            if message.tool_calls:
                tool_rounds += 1
                try:
                    tool_engine.process(message, messages)
                except PlanAbortedByUser as exc:
                    logger.info("[Agent] plan 被用户拒绝 — 中止当前 query：%s", exc)
                    cancel_msg = "已按用户要求取消执行 plan。如需重新规划请发起新提问。"
                    self._session_store.append(
                        sid,
                        {"role": "assistant", "content": cancel_msg},
                    )
                    _usage = (
                        TokenUsage(_prompt_tokens, _comp_tokens, _prompt_tokens + _comp_tokens)
                        if (_prompt_tokens or _comp_tokens) else None
                    )
                    if own_state:
                        self.last_usage = _usage
                    bus.publish(AgentEvent(
                        type=EVENT_FINAL_ANSWER,
                        payload={"text": cancel_msg, "usage": _usage,
                                 "aborted_by_user": True,
                                 "trace": {"llm_rounds": _llm_rounds}},
                    ))
                    return cancel_msg
                continue

            # ── 情况 2：LLM 直接返回最终回答 ──────────────────────────────────
            final_answer = message.content or ""
            if final_answer.strip():
                logger.info("[Agent] 第 %d 轮得到最终回答，退出循环", iteration)
                # 扫 LLM 正文实际引到的 [n]，按 builder 已注册的编号
                # 渲染 sources 块并拼到 answer 末尾；无引用时 sources_block 为空，
                # 答案保持原样（用户写 rules 禁引时的合法输出）
                final_answer = final_answer.strip()
                used_nums = citation_builder.extract_used(final_answer)
                sources_block = citation_builder.render(used_nums)
                if sources_block:
                    # 把 sources 块也作为 token_chunk emit，让 CLI
                    # 等流式 UI 能在正文 token 流完后继续渲染 sources 块；非流式
                    # UI（EventBus 无 TOKEN_CHUNK 订阅者）这次 publish 静默无副作用
                    _on_token(sources_block)
                final_answer = final_answer + sources_block
                # 将最终回答（含 sources 块）写入 DB，下一轮 LLM 可见统一来源
                self._session_store.append(
                    sid,
                    {"role": "assistant", "content": final_answer},
                )
                _usage = (
                    TokenUsage(_prompt_tokens, _comp_tokens, _prompt_tokens + _comp_tokens)
                    if (_prompt_tokens or _comp_tokens) else None
                )
                if own_state:
                    self.last_usage = _usage
                # 跨 session 记忆提取：显式触发词 or 自动提取开关
                memory_mgr.try_extract(user_input, final_answer)
                # plan 收尾：LLM 在最后一步常直接出答案而不调 update_step，导致该步的
                # plan_step_end 永不发出、UI 永远转圈。出最终答案前补发剩余 pending 步的
                # 结束事件，让前端把它们收敛为完成态。
                self._finalize_pending_plan_steps(messages, bus)
                bus.publish(AgentEvent(
                    type=EVENT_FINAL_ANSWER,
                    payload={"text": final_answer, "usage": _usage,
                             "trace": {"llm_rounds": _llm_rounds},
                             # 语义缓存判定：无工具 + 未注入个性化 才可缓存
                             "used_tools": tool_rounds > 0,
                             "personalized": _personalized},
                ))
                return final_answer

            # LLM 返回空内容：先尝试去 tools 重试一轮逼出正文（模型常把答案塞进
            # reasoning_content 而漏了 content），仍空才报错退出。
            if not empty_retry_used:
                empty_retry_used = True
                force_no_tools = True
                logger.warning("[Agent] LLM 返回空内容，去 tools 重试一轮强制生成正文")
                continue

            logger.warning("[Agent] LLM 返回空内容（重试后仍空），提前退出")
            _usage = (
                TokenUsage(_prompt_tokens, _comp_tokens, _prompt_tokens + _comp_tokens)
                if (_prompt_tokens or _comp_tokens) else None
            )
            if own_state:
                self.last_usage = _usage
            fallback = "抱歉，未能生成有效回答，请重试。"
            bus.publish(AgentEvent(
                type=EVENT_ERROR,
                payload={"message": "LLM 返回空内容", "recoverable": True, "phase": "empty_response"},
            ))
            bus.publish(AgentEvent(
                type=EVENT_FINAL_ANSWER,
                payload={"text": fallback, "usage": _usage,
                         "trace": {"llm_rounds": _llm_rounds}},
            ))
            return fallback

        # 超过自适应总迭代上限（含 plan 步数扩展）
        logger.warning("[Agent] 达到自适应总轮次上限，强制返回")
        _usage = (
            TokenUsage(_prompt_tokens, _comp_tokens, _prompt_tokens + _comp_tokens)
            if (_prompt_tokens or _comp_tokens) else None
        )
        if own_state:
            self.last_usage = _usage
        fallback = "抱歉，推理过程过于复杂，未能在规定轮次内完成。请尝试更具体的问题。"
        bus.publish(AgentEvent(
            type=EVENT_ERROR,
            payload={"message": "达到最大迭代次数", "recoverable": False, "phase": "max_iterations"},
        ))
        bus.publish(AgentEvent(
            type=EVENT_FINAL_ANSWER,
            payload={"text": fallback, "usage": _usage,
                     "trace": {"llm_rounds": _llm_rounds}},
        ))
        return fallback

    def _finalize_pending_plan_steps(
        self, messages: list[dict[str, Any]], bus: EventBus
    ) -> None:
        """出最终答案前，给 active plan 里仍未结束的步骤补发 plan_step_end。

        LLM 跑到最后一步时常常直接产出最终答案而不调 `update_step`，于是该步的
        plan_step_end 永不发出、前端那一步永远转圈。这里 reconstruct 当前 plan，
        把剩余 pending 步统一标成 success 收尾（仅发事件，不写 session_store —— plan
        状态本就靠 messages 里的 tool_calls 重建，这是纯 UI 收敛）。

        bus 由 run() 传入（本次运行的局部 bus 或 self.events），保证并发隔离。
        """
        if not bus.subscribers(EVENT_PLAN_STEP_END):
            return
        from src.agent.core.plan_manager import reconstruct_from_messages
        plan = reconstruct_from_messages(messages)
        if plan is None or plan.aborted or plan.is_complete():
            return
        for step in plan.steps:
            if step.status == "pending":
                bus.publish(AgentEvent(
                    type=EVENT_PLAN_STEP_END,
                    payload={"step_id": step.id, "status": "success", "note": ""},
                ))

    def _compute_effective_caps(self, messages: list[dict[str, Any]]) -> tuple[int, int]:
        """
        plan-aware：按当前 active plan 步数动态扩展 round 上限。

        无 active plan / plan 已完结 → 退化为 baseline（`_cfg.MAX_TOOL_ROUNDS` / total baseline）。
        active plan N 步 → 按 N × `_PLAN_ROUNDS_PER_STEP` 估算 tool 预算，加 baseline 取大；
        total 上限相对 tool 上限加 `_PLAN_TOTAL_HEADROOM` 余量。任何情况下都不超 `_cfg.MAX_HARD_CAP_ROUNDS`。
        """
        from src.agent.core.plan_manager import reconstruct_from_messages
        # 显式传了 max_iterations 用固定值；否则跟随 _cfg.MAX_TOTAL_ROUNDS（UI 改后实时生效）
        total_baseline = self.max_iterations if self._max_iterations_explicit else _cfg.MAX_TOTAL_ROUNDS
        tool_baseline = _cfg.MAX_TOOL_ROUNDS
        hard_cap = _cfg.MAX_HARD_CAP_ROUNDS
        plan = reconstruct_from_messages(messages)
        if plan is None or not plan.steps or plan.is_complete():
            return tool_baseline, total_baseline
        n = len(plan.steps)
        eff_tool = min(hard_cap, max(tool_baseline, n * _PLAN_ROUNDS_PER_STEP + 2))
        eff_total = min(hard_cap, max(total_baseline, eff_tool + _PLAN_TOTAL_HEADROOM))
        return eff_tool, eff_total

    def activate_skill(self, name: str, body: str) -> bool:
        """
        手动激活 Skill：注入 system_prompt 并从工具枚举中移除，防止 LLM 重复 load_skill。

        Args:
            name: Skill 名称。
            body: Skill 正文内容（SKILL.md body）。

        Returns:
            True — 首次激活成功；False — 该 Skill 已处于激活状态，不重复注入。
        """
        tag = f'<skill_content name="{name}">'
        if tag in self.system_prompt:
            return False
        self.system_prompt = (
            self.system_prompt
            + f"\n\n{tag}\n{body}\n</skill_content>"
        )
        # 从实例级 _skill_bodies 移除，使 get_tools() 的 enum 不再含此 skill，
        # 避免 LLM 再次调用 load_skill 导致内容重复注入
        self._skill_bodies.pop(name, None)
        logger.info("[Agent] Skill [%s] 已手动激活并从工具枚举移除", name)
        self.events.publish(AgentEvent(
            type=EVENT_INFO,
            payload={"message": "skill.activated", "skill_name": name},
        ))
        return True

