"""
Auto-GPT 风格 Agent —— Plan → Execute → Review 三阶段循环

与 ReAct Agent 的区别：
    ReAct (agent.py)   : 单循环交织推理 + 工具调用，LLM 每轮决定下一步。
    Auto-GPT (本文件)  : 先生成完整任务列表（Plan），逐任务执行（Execute），
                         最后综合所有结果生成最终回答（Review）。

执行流程：
    1. [Plan]    接收用户目标，LLM 生成 JSON 格式任务列表（最多 MAX_PLAN_TASKS 个）
    2. [Execute] 对每个任务运行迷你 ReAct 子循环（最多 MAX_TASK_TOOL_ROUNDS 轮工具调用），
                 子循环复用公共层 `ToolCallEngine`（工具编排 + 引用编号 + plan 事件）
    3. [Review]  汇总所有任务结果，按「四层 system」拼接后 LLM 综合生成最终回答 + 引用块
    4. [Persist] 仅将 user + 最终 assistant 消息写入 ChatHistoryStore SQLite

与 design.md §5 对齐：复用公共层（`src/agent/core/*`）helper 组装 loop，差异只在三阶段编排。
本文件为 AutoGPT 实现专属代码（含 `_AutoGPTEphemeralHistory` 等 autogpt 命名隔离件）；
跨 impl 的共享能力直接从 `src/agent/agent.py` 与 `src/agent/core/*` 复用，不复制、不改写。

接口契约（duck-typed，与 Agent / LangChainAgent 一致）：
    run(user_input) -> str
    session_id: str
    activate_skill(name, body) -> bool
    last_usage: TokenUsage | None
    verbose: bool
    thinking_cfg: ThinkingConfig
    events: EventBus
"""

import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from src.agent.agent import (
    ThinkingConfig,
    TokenUsage,
    SYSTEM_PROMPT,
    _get_active_rules,
    _get_shared_user_memory,
    build_active_study_plan_block,
)
from src.agent.core.citation_builder import CitationBuilder
from src.agent.core.event_bus import (
    ALL_EVENT_TYPES,
    EVENT_ERROR,
    EVENT_FINAL_ANSWER,
    EVENT_INFO,
    EVENT_THINKING_CHUNK,
    EVENT_TOKEN_CHUNK,
    AgentEvent,
    EventBus,
)
from src.agent.core.memory_manager import MemoryManager
from src.agent.core.rules_loader import build_rules_block
from src.agent.core.thinking_policy import ThinkingPolicy
from src.agent.core.tool_call_engine import ToolCallEngine
from src.agent.tools import get_tools
from src.skills.skill_loader import SkillInfo, build_skill_catalog
from src.llm.provider import chat, call_with_thinking
from src.memory.chat_history import ChatHistoryStore
from src.memory.user_memory import UserMemoryStore
import src.config as _cfg

logger = logging.getLogger(__name__)


def _parse_plan_json(raw: str) -> dict[str, Any]:
    """从 LLM 输出解析 plan JSON，容忍 markdown 代码围栏包裹。

    模型常把 JSON 包进 ```json ... ``` 围栏，直接 json.loads 会失败。依次尝试：
    ① 原文直接解析；② 剥掉首尾代码围栏后解析；③ 退而取首个 `{` 到末个 `}` 的子串解析。
    任一得到 dict 即返回；全失败抛 ValueError。
    """
    candidates = [raw]
    fenced = raw.strip()
    if fenced.startswith("```"):
        inner = re.sub(r"^```[^\n]*\n?", "", fenced)
        inner = re.sub(r"\n?```$", "", inner).strip()
        candidates.append(inner)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("未找到有效 JSON 对象")


# ── 默认限制（可通过 config 覆盖）────────────────────────────────────────────
_DEFAULT_MAX_PLAN_TASKS: int = 6
_DEFAULT_MAX_TASK_TOOL_ROUNDS: int = 4

# 工具执行结果预览截断长度
_TOOL_PREVIEW_LEN: int = 100
# 历史加载条数上限
_HISTORY_FETCH_LIMIT: int = 40

# ── Planning 阶段提示词 ───────────────────────────────────────────────────────

_PLAN_SYSTEM = """你是一个任务规划助手。
给定用户目标，将其分解为若干个具体、可独立执行的子任务（steps），以 JSON 格式返回。

格式要求：
{{
  "tasks": ["子任务1", "子任务2", ...],
  "reasoning": "为什么这样分解"
}}

规则：
- tasks 数量控制在 1～{max_tasks} 个，不要过度分解
- 每个子任务应该具体、可操作，便于通过工具完成
- 若目标简单，tasks 只需 1～2 个
- 只返回合法 JSON，不要附加任何说明文字
"""

_PLAN_USER_TMPL = """用户目标：{goal}

{history_hint}请生成任务列表："""

# ── Execute 阶段提示词 ────────────────────────────────────────────────────────

_EXECUTE_SYSTEM = """你是一个任务执行助手。
你的职责是通过调用可用工具完成当前分配的子任务。

已完成的前序任务摘要：
{prior_summary}

## 工具使用策略
1. 优先调用 search_knowledge 检索私有知识库。
2. 若知识库无结果，改用 web_search 搜索关键词，再从真实 URL 中选 fetch_url 抓取。
3. 获取足够信息后，输出 TASK_COMPLETE: <简洁的任务执行结果>，不要附加其他说明。
4. 若工具均失败，输出 TASK_COMPLETE: [无法获取有效信息] 原因说明。

## 引用编号保留（重要）
search_knowledge 返回的片段带 [N] 编号。若你的 TASK_COMPLETE 结果引用了某片段内容，
**必须原样保留对应的 [N] 编号**，便于 Review 阶段汇总引用来源。
"""

_EXECUTE_USER_TMPL = """当前子任务：{task}

请执行，完成后输出 TASK_COMPLETE: <结果>"""

# ── Review 阶段提示词 ─────────────────────────────────────────────────────────

_REVIEW_USER_TMPL = """用户目标：{goal}

各子任务执行结果：
{results_text}

请综合以上信息，生成最终回答："""


class _AutoGPTEphemeralHistory:
    """AutoGPT 专属：Execute 子循环用的内存临时历史。

    `ToolCallEngine` 会把每轮 assistant / tool 中间消息 `append` 到 chat_history。
    AutoGPT 约定「只持久化 user + 最终 assistant」，因此子循环改用本内存对象承接
    中间消息，`run()` 结束即随对象一起丢弃，绝不污染真实 ChatHistoryStore。

    仅实现 `ToolCallEngine` 用到的 `append`；其余 ChatHistoryStore 方法不需要。
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def append(self, session_id: str, msg: dict[str, Any]) -> None:  # noqa: ARG002
        self.messages.append(msg)


class AutoGPTAgent:
    """
    Auto-GPT 风格 Agent：Plan → Execute → Review 三阶段循环。

    Attributes:
        system_prompt:       Agent 行为策略提示（用于 Review 阶段的背景注入）。
        verbose:             是否打印调试信息。
        session_id:          会话 ID，用于持久化对话历史。
        last_usage:          最近一次 run() 的累计 token 统计。
        thinking_cfg:        Extended Thinking 配置（Execute / Review 阶段按此启用）。
        events:              EventBus 实例（与 Python / LangChain 接口对齐）。
        max_plan_tasks:      单次规划允许的最大子任务数。
        max_task_tool_rounds:每个子任务允许的最大工具调用轮次。
    """

    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        verbose: bool = True,
        session_id: str | None = None,
        chat_history: ChatHistoryStore | None = None,
        skills: dict[str, SkillInfo] | None = None,
        thinking_config: ThinkingConfig | None = None,
        user_memory: UserMemoryStore | None = None,
        max_plan_tasks: int | None = None,
        max_task_tool_rounds: int | None = None,
    ) -> None:
        self.session_id: str = session_id or str(uuid.uuid4())
        self.verbose = verbose
        self.thinking_cfg: ThinkingConfig = (
            thinking_config if thinking_config is not None else ThinkingConfig.from_config()
        )
        self.last_usage: TokenUsage | None = None

        self._system_prompt = system_prompt

        # Skill 支持：提取 bodies，将含 description 的 catalog 追加到 system_prompt
        self._skill_bodies: dict[str, str] = {}
        if skills:
            self._skill_bodies = {name: info.body for name, info in skills.items()}
            self._system_prompt = self._system_prompt + build_skill_catalog(skills)

        # ChatHistoryStore：支持外部注入（便于测试 mock），默认懒加载全局实例
        self._chat_history: ChatHistoryStore = (
            chat_history if chat_history is not None else self._get_shared_chat_history()
        )

        # 用户记忆：支持外部注入，默认复用模块级共享实例（与 Python Agent 一致）
        self._user_memory: UserMemoryStore | None = (
            user_memory if user_memory is not None else _get_shared_user_memory()
        )

        # 事件总线：与 Python Agent 接口一致。
        # 发出：info / tool_call_* / plan_*（内层 make_plan 经 ToolCallEngine）/
        #       thinking_chunk / token_chunk（Execute/Review）/ final_answer / error
        self.events: EventBus = EventBus()

        # 配置限制，优先使用构造参数，其次读 config，最后用内置默认值
        self._max_plan_tasks: int = (
            max_plan_tasks
            if max_plan_tasks is not None
            else getattr(_cfg, "AUTOGPT_MAX_PLAN_TASKS", _DEFAULT_MAX_PLAN_TASKS)
        )
        self._max_task_tool_rounds: int = (
            max_task_tool_rounds
            if max_task_tool_rounds is not None
            else getattr(_cfg, "AUTOGPT_MAX_TASK_TOOL_ROUNDS", _DEFAULT_MAX_TASK_TOOL_ROUNDS)
        )

        # token 累计
        self._prompt_tokens: int = 0
        self._comp_tokens: int = 0

        # run() 期间的活跃引用编排器；非 run 上下文（如直接单测 _review）为 None
        self._citation_builder: CitationBuilder | None = None

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def run(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
        event_callback: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        """
        执行完整的 Plan → Execute → Review 循环，返回最终回答文本。

        Args:
            user_input: 用户的自然语言目标/问题。
            session_id: 本次运行会话 ID（接口对齐 Python Agent）。
            event_callback: 本次运行事件回调（接口对齐 Python Agent）。

        Returns:
            综合各子任务结果后生成的最终回答。

        并发警告：本实现只支持单用户。Web 端 `get_agent()` 可经 `IMP_METHOD=AUTOGPT`
        路由到这里，但 run() 把 per-run kwargs 折叠回共享实例状态（self.session_id /
        self.events / self._prompt_tokens / self._citation_builder），多用户并发会互相
        覆盖 → 会话串台、事件发错流、token 与引用编号错乱。真正的 per-request 并发隔离
        只在默认 `Agent.run`（PYTHON）内实现；并发场景请用 PYTHON。
        """
        if session_id is not None:
            self.session_id = session_id
        if event_callback is not None:
            self.set_event_callback(event_callback)
        self._prompt_tokens = 0
        self._comp_tokens = 0
        # 每次 run 一个 CitationBuilder，跨所有任务的 Execute 子循环累计编号（B-3）
        self._citation_builder = CitationBuilder()

        history_summary = self._build_history_summary()

        self.events.publish(AgentEvent(
            type=EVENT_INFO,
            payload={"message": "agent.run.start", "session_id": self.session_id, "impl": "autogpt"},
        ))

        # Phase 1: Plan
        logger.info("[AutoGPT] Phase 1: Planning for goal: %r", user_input[:80])
        tasks = self._plan(user_input, history_summary)
        # 外层任务列表用 info 事件上报（B-6 外层；不复用 plan_*，避免与内层 make_plan 打架）
        self.events.publish(AgentEvent(
            type=EVENT_INFO,
            payload={"message": "autogpt.plan", "tasks": list(tasks)},
        ))
        logger.info("[AutoGPT] 生成 %d 个子任务: %s", len(tasks), tasks)
        if self.verbose:
            print(f"\n[AutoGPT] 规划完成，共 {len(tasks)} 个子任务：")
            for i, t in enumerate(tasks, 1):
                print(f"  {i}. {t}")
            print()

        # Phase 2: Execute each task
        task_results: list[tuple[str, str]] = []
        for i, task in enumerate(tasks, 1):
            self.events.publish(AgentEvent(
                type=EVENT_INFO,
                payload={"message": "autogpt.task_start", "index": i, "total": len(tasks), "task": task},
            ))
            if self.verbose:
                print(f"[AutoGPT] 执行子任务 {i}/{len(tasks)}: {task}")
            result = self._execute_task(task, user_input, task_results)
            task_results.append((task, result))
            preview = result[:_TOOL_PREVIEW_LEN].replace("\n", " ")
            self.events.publish(AgentEvent(
                type=EVENT_INFO,
                payload={"message": "autogpt.task_end", "index": i, "task": task, "preview": preview},
            ))
            if self.verbose:
                logger.info("[AutoGPT] 子任务 %d 完成: %s...", i, preview)

        # Phase 3: Review & synthesize
        logger.info("[AutoGPT] Phase 3: Reviewing and synthesizing final answer")
        if self.verbose:
            print("[AutoGPT] 综合子任务结果，生成最终回答...\n")
        final_answer = self._review(user_input, task_results)

        # Persist to ChatHistoryStore（只写 user + 最终 assistant）
        self._persist(user_input, final_answer)

        # Record token usage
        if self._prompt_tokens or self._comp_tokens:
            self.last_usage = TokenUsage(
                self._prompt_tokens,
                self._comp_tokens,
                self._prompt_tokens + self._comp_tokens,
            )
        else:
            self.last_usage = None

        # 跨 session 用户记忆提取（B-4；user_memory 为 None 时静默 no-op）
        self._extract_memory(user_input, final_answer)

        self.events.publish(AgentEvent(
            type=EVENT_FINAL_ANSWER,
            payload={"text": final_answer, "usage": self.last_usage},
        ))
        self._citation_builder = None
        return final_answer

    # ── 事件订阅（与 AgentAPI 一致）────────────────────────────────────────────

    def set_event_callback(self, callback: Callable[[AgentEvent], None] | None) -> None:
        """
        设置统一事件回调（覆盖语义）：传 None 清空所有事件订阅。

        接口与 `AgentAPI` 一致，便于上层 UI 以同一套代码挂三种 Agent 实现。
        """
        self.events.clear()
        if callback is None:
            return
        for evt_type in ALL_EVENT_TYPES:
            def _wrapper(payload: Any, _t: str = evt_type) -> None:
                callback(AgentEvent(type=_t, payload=payload))
            self.events.subscribe(evt_type, _wrapper)

    def activate_skill(self, name: str, body: str) -> bool:
        """
        手动激活 Skill：注入 system_prompt 并从工具枚举中移除。

        Returns:
            True — 首次激活成功；False — 已处于激活状态。
        """
        tag = f'<skill_content name="{name}">'
        if tag in self._system_prompt:
            return False
        self._system_prompt = (
            self._system_prompt + f"\n\n{tag}\n{body}\n</skill_content>"
        )
        self._skill_bodies.pop(name, None)
        logger.info("[AutoGPT] Skill [%s] 已激活并从工具枚举移除", name)
        self.events.publish(AgentEvent(
            type=EVENT_INFO,
            payload={"message": "skill.activated", "skill_name": name},
        ))
        return True

    # ── 内部：三阶段核心方法 ─────────────────────────────────────────────────

    def _plan(self, goal: str, history_summary: str) -> list[str]:
        """
        Phase 1：调用 LLM 生成结构化任务列表。

        规划属内部工序，用规划专用 system（不注入四层 system / 不走 thinking 流）。
        返回任务字符串列表；解析失败时回退为单任务列表（直接以 goal 作为任务）。
        """
        history_hint = (
            f"历史上下文摘要：\n{history_summary}\n\n" if history_summary else ""
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _PLAN_SYSTEM.format(max_tasks=self._max_plan_tasks),
            },
            {
                "role": "user",
                "content": _PLAN_USER_TMPL.format(
                    goal=goal, history_hint=history_hint
                ),
            },
        ]
        response = chat(messages)
        self._accumulate_usage(response)
        raw = (response.choices[0].message.content or "").strip()

        try:
            data = _parse_plan_json(raw)
            tasks: list[str] = data.get("tasks", [])
            if tasks and isinstance(tasks, list):
                # 截断到最大数量，过滤空字符串
                tasks = [str(t).strip() for t in tasks if str(t).strip()]
                return tasks[: self._max_plan_tasks]
        except (ValueError, AttributeError, TypeError):
            logger.warning("[AutoGPT] Plan 阶段 JSON 解析失败，回退为单任务。raw=%r", raw[:200])

        # Fallback：直接以用户目标作为单个任务
        return [goal]

    def _execute_task(
        self,
        task: str,
        goal: str,  # noqa: ARG002 — 保留签名（接口稳定 + 语义可读）
        prior_results: list[tuple[str, str]],
    ) -> str:
        """
        Phase 2：对单个子任务运行迷你 ReAct 子循环。

        复用公共层 `ToolCallEngine`：工具执行 / 结果格式化 / 引导提示 / 引用编号 / plan 事件
        全部由它编排。中间 assistant / tool 消息写入 `_AutoGPTEphemeralHistory`（内存临时），
        不污染真实历史（AutoGPT 只持久化 user + 最终 assistant）。

        工具调用最多 max_task_tool_rounds 轮；达到上限或 LLM 输出 TASK_COMPLETE
        标记时退出，返回任务执行结果字符串。
        """
        prior_summary = self._format_prior_results(prior_results)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _EXECUTE_SYSTEM.format(prior_summary=prior_summary or "（无）"),
            },
            {
                "role": "user",
                "content": _EXECUTE_USER_TMPL.format(task=task),
            },
        ]

        # 子循环专属：内存临时历史承接中间消息，run 结束即丢弃
        tool_engine = ToolCallEngine(
            _AutoGPTEphemeralHistory(),
            self.session_id,
            self._skill_bodies,
            verbose=self.verbose,
            events=self.events,
            citation_builder=self._citation_builder,
        )

        tool_rounds = 0
        for iteration in range(1, self._max_task_tool_rounds + 2):
            active_tools = (
                get_tools(self._skill_bodies) if tool_rounds < self._max_task_tool_rounds else None
            )
            response = self._llm_call(messages, active_tools, stream_tokens=False)
            self._accumulate_usage(response)
            message = response.choices[0].message

            # LLM 决定调用工具 —— 交给公共层 ToolCallEngine 编排
            if message.tool_calls:
                tool_rounds += 1
                tool_engine.process(message, messages)
                continue

            # LLM 返回文本 —— 提取 TASK_COMPLETE 标记后的内容
            text = (message.content or "").strip()
            if "TASK_COMPLETE:" in text:
                idx = text.index("TASK_COMPLETE:")
                return text[idx + len("TASK_COMPLETE:") :].strip()

            # 没有 TASK_COMPLETE 标记：返回全部文本（兜底）
            if text:
                return text

            logger.warning("[AutoGPT] 子任务执行：LLM 返回空内容（iteration=%d）", iteration)

        return f"[子任务未能在 {self._max_task_tool_rounds} 轮内完成]: {task}"

    def _review(self, goal: str, task_results: list[tuple[str, str]]) -> str:
        """
        Phase 3：汇总所有子任务结果，调用 LLM 生成最终回答。

        面向用户输出，按「四层 system」拼接（base + <project_rules> + <user_context>
        + <active_study_plan>，与 Python Agent 一致），并在末尾追加 RAG 引用块。
        """
        results_text = self._format_prior_results(task_results)
        system_content = self._build_review_system()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": _REVIEW_USER_TMPL.format(
                    goal=goal, results_text=results_text
                ),
            },
        ]
        # Review 正文走 token 流（B-8）：有订阅者才推，无订阅者零副作用
        response = self._llm_call(messages, tools=None, stream_tokens=True)
        self._accumulate_usage(response)
        final_answer = (response.choices[0].message.content or "").strip()
        if not final_answer:
            return "抱歉，未能生成有效回答，请重试。"

        # RAG 引用块（B-3）：仅当本 run 持有 citation_builder（即经 run() 进入）时渲染。
        # 直接单测 _review 时 citation_builder 为 None，保持回答原样、不追加 sources。
        if self._citation_builder is not None:
            used_nums = self._citation_builder.extract_used(final_answer)
            sources_block = self._citation_builder.render(used_nums)
            if sources_block:
                # 同步给流式 UI（与正文 token 流衔接）；无订阅者静默
                if self.events.subscribers(EVENT_TOKEN_CHUNK):
                    self.events.publish(AgentEvent(
                        type=EVENT_TOKEN_CHUNK, payload={"text": sources_block}
                    ))
                final_answer = final_answer + sources_block

        return final_answer

    # ── 内部：四层 system / LLM 调用 ────────────────────────────────────────

    def _build_review_system(self) -> str:
        """拼 Review 阶段四层 system：base(+catalog) → rules → user_context → study_plan。

        各层来源与 `Agent.run` 完全同源（复用 agent.py / core 的同一组 helper），
        保证 IMP_METHOD=AUTOGPT 与 PYTHON 的偏好 / 记忆 / 学习计划注入行为一致。
        """
        base = self._system_prompt + build_rules_block(_get_active_rules())
        mem_mgr = MemoryManager(self._user_memory, self._chat_history, self.session_id, chat)
        base = mem_mgr.build_system_prompt(base)
        base = base + build_active_study_plan_block(self.session_id)
        return base

    def _llm_call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        stream_tokens: bool,
    ) -> Any:
        """统一 LLM 调用入口：按 thinking 配置在 chat / call_with_thinking 间分发（B-8）。

        - thinking 开启 → `call_with_thinking`，透传 thinking_chunk（有订阅者才推）
        - token 流 → 仅 `stream_tokens=True` 且有 token 订阅者时推（Review 正文）
        无任何订阅者时回退为普通非流式调用，零副作用——保证不破坏 mock chat 的单测。
        """
        def _on_thinking(text: str) -> None:
            self.events.publish(AgentEvent(type=EVENT_THINKING_CHUNK, payload={"text": text}))

        def _on_token(text: str) -> None:
            self.events.publish(AgentEvent(type=EVENT_TOKEN_CHUNK, payload={"text": text}))

        token_cb = (
            _on_token if (stream_tokens and self.events.subscribers(EVENT_TOKEN_CHUNK)) else None
        )
        if self.thinking_cfg is not None and self.thinking_cfg.enabled:
            thinking_cb = _on_thinking if self.events.subscribers(EVENT_THINKING_CHUNK) else None
            policy = ThinkingPolicy(self.thinking_cfg)
            return call_with_thinking(
                messages,
                budget_tokens=policy.effective_budget(),
                tools=tools,
                on_thinking_chunk=thinking_cb,
                on_token_chunk=token_cb,
            )
        return chat(messages, tools=tools, on_token_chunk=token_cb)

    # ── 内部：辅助方法 ────────────────────────────────────────────────────────

    @staticmethod
    def _get_shared_chat_history() -> ChatHistoryStore:
        """懒加载全局共享 ChatHistoryStore（复用 agent.py 的同一进程级实例）。"""
        from src.agent.agent import _get_shared_chat_history
        return _get_shared_chat_history()

    def _build_history_summary(self) -> str:
        """
        加载最近若干轮历史，压缩为文本摘要供 Plan 阶段参考。
        只取 user / assistant 角色，忽略 tool 消息，限制总字符。
        """
        raw = self._chat_history.load_last_n_messages(
            self.session_id, _HISTORY_FETCH_LIMIT
        )
        lines: list[str] = []
        for m in raw:
            role = m.get("role", "")
            if role not in ("user", "assistant"):
                continue
            label = "用户" if role == "user" else "Agent"
            content = (m.get("content") or "").strip()[:200]
            lines.append(f"{label}：{content}")
        # 只保留最近若干轮
        return "\n".join(lines[-20:])

    def _persist(self, user_input: str, final_answer: str) -> None:
        """将 user + 最终 assistant 消息写入 ChatHistoryStore SQLite。"""
        self._chat_history.append(
            self.session_id,
            {"role": "user", "content": user_input},
        )
        self._chat_history.append(
            self.session_id,
            {"role": "assistant", "content": final_answer},
        )

    def _extract_memory(self, user_input: str, final_answer: str) -> None:
        """跨 session 用户记忆提取（复用 MemoryManager 节流策略）。user_memory 为 None 时 no-op。"""
        if self._user_memory is None:
            return
        mem_mgr = MemoryManager(self._user_memory, self._chat_history, self.session_id, chat)
        mem_mgr.try_extract(user_input, final_answer)

    def _accumulate_usage(self, response: Any) -> None:
        """从 LLM response 中累加 token 统计。"""
        usage = getattr(response, "usage", None)
        if usage:
            self._prompt_tokens += getattr(usage, "prompt_tokens", 0)
            self._comp_tokens += getattr(usage, "completion_tokens", 0)

    @staticmethod
    def _format_prior_results(results: list[tuple[str, str]]) -> str:
        """将已完成子任务列表格式化为可读文本。"""
        if not results:
            return ""
        lines: list[str] = []
        for i, (task, result) in enumerate(results, 1):
            lines.append(f"[任务{i}] {task}")
            lines.append(f"  结果: {result[:500]}")
        return "\n".join(lines)
