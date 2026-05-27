"""
Agent 主控逻辑 —— ReAct（Reason + Act）循环

执行流程：
    1. 接收用户问题，从 ChatHistoryStore 加载历史消息
    2. 拼接为 [system] + history + [user]，超长时自动截断
    3. 调用 LLM（携带工具定义）
    4. 若 LLM 返回 tool_calls → 执行工具 → 将结果追加到 messages → 继续循环
    5. 若 LLM 直接返回文本 → 输出最终回答，退出循环
    6. 超过最大迭代次数时强制退出，防止死循环
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any, NamedTuple

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
from src.agent.core.history_manager import HistoryManager
from src.agent.core.memory_manager import MemoryManager
from src.agent.core.rules_loader import build_rules_block, load_project_rules
from src.agent.core.thinking_policy import ThinkingConfig, ThinkingPolicy  # noqa: F401 — re-export
from src.agent.core.tool_call_engine import ToolCallEngine
from src.agent.tools import get_tools
from src.cli.skill_loader import SkillInfo, build_skill_catalog
from src.llm.provider import chat, call_with_thinking
from src.memory.chat_history import ChatHistoryStore
from src.memory.user_memory import UserMemoryStore
import src.config as _cfg

logger = logging.getLogger(__name__)


class TokenUsage(NamedTuple):
    """单次 Agent.run() 调用累计消耗的 token 统计。"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


# 模块级共享 ChatHistoryStore 实例（单进程内所有 Agent 共享同一个 DB 连接）
_chat_history: ChatHistoryStore | None = None

# 模块级共享 UserMemoryStore 实例（双检锁保护）
_shared_user_memory: UserMemoryStore | None = None
_shared_user_memory_lock = __import__("threading").Lock()

# 模块级缓存的项目 rules 文本（进程启动后只读一次，重启进程才会刷新）
_shared_project_rules: str | None = None
_shared_project_rules_loaded: bool = False


def _get_shared_chat_history() -> ChatHistoryStore:
    """获取模块级共享 ChatHistoryStore，首次调用时懒加载初始化。"""
    global _chat_history
    if _chat_history is None:
        _chat_history = ChatHistoryStore()
    return _chat_history


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


def _get_shared_project_rules() -> str | None:
    """获取项目 rules 文本，首次调用时一次性加载，后续命中缓存。

    返回 `None` 表示 disabled / 文件缺失 / 空。重新加载需重启进程
    （Phase 1.3 设计上不做 watch / 热加载）。
    """
    global _shared_project_rules, _shared_project_rules_loaded
    if not _shared_project_rules_loaded:
        _shared_project_rules = load_project_rules()
        _shared_project_rules_loaded = True
    return _shared_project_rules

# Agent 系统提示：指导 LLM 的行为策略
SYSTEM_PROMPT = """你是用户的**个人私有知识库助手**。知识库里装的是**当前用户本人**的全部资料：
简历、项目复盘、工作经历、岗位定位、研究笔记、个人作品集、联系方式、Cursor 配置、个人写作等等。
用户在与你对话时，"我 / 我的 / 我现在 / 我以前 / 我擅长 / 我做过" 这一类第一人称表达指的都是
**这位用户本人**，不是你（Kimi/助手）。回答关于用户的任何信息**只能来自工具调用结果**，
不能来自你的训练数据，也不能反问用户"您能告诉我吗"——所有答案就在 search_knowledge 里。

可用工具：
- search_knowledge：搜索私有知识库（dense 向量 + BM25 关键词 混合检索；内部已自动做 query 改写/HyDE，你只需关注"传什么 query 给它"）
- web_search：通过搜索引擎查找互联网信息，返回真实 URL 列表及摘要
- fetch_url：抓取指定网页正文（SPA 页面自动通过 Jina Reader 处理）

## 必须首先调用 search_knowledge 的场景（无条件、不解释、不反问）

**只要满足任意一条，就必须先调 search_knowledge，再基于结果回答**：

1. 用户用第一人称问关于自己的任何事
   ——"我在哪家公司"、"我的求职目标"、"我做过什么项目"、"我擅长什么"、"我的邮箱"、"我学的什么专业"、"我工作多少年了"……
2. 用户提到的任何专有名词、项目名、产品名、缩写、人名、文件名
   ——"AgentA"、"L2PS"、"SCellAct"、"CS2Filter"、"DB Split"、"SaS"、"3GPP TS 38.211"……
3. 用户问"是什么 / 怎么样 / 为什么 / 怎么做"等任何开放性问题
   ——只要不是纯闲聊（"你好"、"谢谢"），就先查。
4. 用户提问明显涉及个人观点、经历、决策（"为什么选...而不是..."、"...的核武器项目是哪个"、"面试时年龄问题怎么回答"）
5. 任何你"看起来似乎知道但其实只是 LLM 训练数据里的常识"的话题——**只要话题落在用户的工作领域里就要查**。

**反例：什么时候可以不查**
- 纯闲聊（"你好 / 谢谢 / 再见 / 现在几点"）；
- 用户明确说"不用查 / 直接回答即可"；
- 多轮对话中用户在追问刚才已经查过的内容（可基于上一轮的 search_knowledge 结果回答，但仍可二次确认）。

## 调用 search_knowledge 前的 query 准备

A. **用具体名词，不要用代词类抽象词**。
   - **错误示范**（实际发生过）：用户问"我在哪家公司"，query 写成 `"用户 工作单位"` → 命中一堆无关内容。
     正确写法：直接用用户的原话或其中关键词 `"现在 公司 工作"` / `"工作单位 当前"` / `"目前 任职"`；
     若知道用户工作领域，也可加领域关键词，例如 `"工作单位 5G"`。
   - "我"指代用户本人 → 不要替换成"用户"，可直接省略主语：`"在哪家公司工作"`、`"求职目标 岗位"`、`"擅长的编程语言"`。
B. **代词消解**：上下文里有"它/这个/那个/上面那篇"，先把指代解析成具体名词再 query。
   例：上轮聊 "PRACH"，本轮问"它的最大重传次数是多少？" → query = "PRACH 最大重传次数"。
C. **术语化**：把口语换成专业术语。例：5G 基站→gNB；4G→LTE；用户设备→UE；随机接入→PRACH；无线接入网→RAN；核心网→5GC/EPC。
D. **拆子查询**：复合问题拆成多个子查询，分别调用 search_knowledge；不要把"列出 X 与 Y 的差异、再说说 Z 的注意事项"塞进一个 query。
E. **过滤参数**：明确知道答案语种或文档类型时，传 `where`，例如 {"lang": "zh"} 或 {"ext": {"$in": [".pdf", ".docx"]}}。
F. **top_k**：默认让它用 8，**绝对不要传 1 或 2**（你看不到全部候选就更容易被孤立的低分结果带偏）；
   只有"取唯一一个最高分答案"的极端场景才传 1。

## 完整工具使用策略（严格遵守）

1. 收到问题，先按 A~F 准备 query，**调用 `search_knowledge`**。
2. **看 source 与相关性分数再下判断**：
   - 如果 top 候选的 source 看起来与问题明显无关（比如问"我在哪家公司"却返回 `a2_SaS/0_SaS.md` 的三层架构 PPT），
     **不要硬把这条结果讲成答案**——回到第 3 步重试，或如实说"知识库中暂无相关内容"。
   - 相关性分数低于 0.5 视为弱命中，要二次确认或重试。
3. 若返回 [结果为空] 或所有结果都与问题明显无关，**先尝试 1~2 次"换角度"再调 search_knowledge**：
   - 第 1 次重试：换一个上位概念、同义术语，或把"我"等主语去掉只留谓宾；
   - 第 2 次重试：换一个相关方向的 query（例：原 "gNB 切换流程" → 重试 "Xn handover"）；
   - 任意一次有命中即停止重试。**不要把刚刚失败过的 query 原样再发一次**。
4. 上述重试都无命中时，**再调用 `web_search`**：
   a. 从 web_search 返回的 URL 列表中选择最相关的 URL，调用 `fetch_url` 获取详情。
   b. **严禁凭空猜测或拼凑 URL**，所有传给 fetch_url 的 URL 必须来自 web_search 结果。
   c. 若 fetch_url 失败，从同一 web_search 结果中换另一个 URL 重试（最多 2 次）。
5. 两种工具均无法获取有效信息时，才如实告知用户"当前无法获取相关信息"。
6. 所有工具调用结束后，综合已获取的信息生成最终回答。

## 回答要求

- **凡是关于用户本人的事实，必须 100% 来自 search_knowledge 返回内容**——不要从你训练数据里的"通识"补全，不要反问用户"您能告诉我吗"。
- 若工具确实未返回有效信息，如实告知"知识库中暂无相关内容"，**不要编造、不要把弱相关结果硬套成答案**。
- 回答简洁、准确，使用中文（除非用户用其他语言提问）。

## 引用规范（使用 search_knowledge 时强制）

`search_knowledge` 的返回结果会以 `[1] (source §heading p.N): ...` 形式给出每条片段的全局编号。引用规则：

1. 正文里**直接复用这些编号**，写成 `[1]` `[2]` 等行内标号，紧跟在引用论据后；多源支撑写成 `[1][2]`。
2. **只能引用 prompt 里出现过的编号**——绝不要造 `[7]` `[99]` 这种没分配过的；超出范围的会被静默丢弃。
3. **不要自己在回答末尾手写 references / sources 列表**，系统会程序化追加 `— sources —` 块，重复手写会出现两份。
4. 若用户偏好（rules / memory）显式要求不写引用，遵循用户偏好优先。
"""

# 最大工具调用轮次，防止 LLM 陷入工具调用死循环
MAX_TOOL_ROUNDS: int = 8
# 含最终回答在内的总推理轮次上限
MAX_TOTAL_ROUNDS: int = 12


class Agent:
    """
    ReAct Agent：通过 LLM + Function Calling 实现推理与工具调用的循环。

    Attributes:
        system_prompt: Agent 的系统提示，定义行为策略。
        max_iterations: 最大总推理轮次（含工具调用和最终回答），超出后强制返回兜底回答。
        verbose: 是否打印每轮工具调用的调试信息。
        session_id: 会话 ID，用于持久化对话历史。
        max_history_turns: 加载历史时保留最近 N 轮（一轮 = user + assistant），防止超出 context window。
    """

    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int = MAX_TOTAL_ROUNDS,
        verbose: bool = True,
        session_id: str | None = None,
        max_history_turns: int = 20,
        chat_history: ChatHistoryStore | None = None,
        skills: dict[str, SkillInfo] | None = None,
        thinking_config: ThinkingConfig | None = None,
        user_memory: UserMemoryStore | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> None:
        # 若传入 skills，提取 bodies，并将含 description 的 catalog 追加到 system_prompt
        self._skill_bodies: dict[str, str] = {}
        if skills:
            self._skill_bodies = {name: info.body for name, info in skills.items()}
            system_prompt = system_prompt + build_skill_catalog(skills)
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.session_id: str = session_id or str(uuid.uuid4())
        self.max_history_turns = max_history_turns
        # 支持从外部传入 chat_history（便于测试 mock），默认使用模块级共享实例
        self._chat_history: ChatHistoryStore = (
            chat_history if chat_history is not None else _get_shared_chat_history()
        )
        self.last_usage: TokenUsage | None = None  # 最近一次 run() 的 token 统计
        # Extended Thinking 配置：共享同一 ThinkingConfig 实例，修改后无需重建 Agent
        self.thinking_cfg: ThinkingConfig = thinking_config if thinking_config is not None else ThinkingConfig.from_config()
        # 本次 run() 流式 thinking 状态标志（实例变量，避免嵌套函数）
        self._thinking_started: bool = False
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

    def _on_thinking_chunk(self, chunk: str) -> None:
        """
        思考过程流式回调，分发到 EventBus；无订阅者时降级到 CLI stdout（默认 UX）。

        异常隔离由 EventBus.publish 内部完成，单订阅者抛错不会向上传播。
        """
        subs = self.events.subscribers(EVENT_THINKING_CHUNK)
        if subs:
            self._thinking_started = True
            self.events.publish(AgentEvent(type=EVENT_THINKING_CHUNK, payload={"text": chunk}))
            return
        if not self._thinking_started:
            print("\n\U0001f4ad 思考中...\n", flush=True)
            self._thinking_started = True
        print(chunk, end="", flush=True)

    def _on_token_chunk(self, chunk: str) -> None:
        """正文 token 流式回调：发到 EventBus（订阅者负责渲染）。无订阅者时静默。"""
        self.events.publish(AgentEvent(type=EVENT_TOKEN_CHUNK, payload={"text": chunk}))

    def _token_callback_for_provider(self) -> Callable[[str], None] | None:
        """提供给 LLM provider 的 on_token_chunk 参数；无订阅者时返回 None（保持非流式）。"""
        if self.events.subscribers(EVENT_TOKEN_CHUNK):
            return self._on_token_chunk
        return None

    def set_event_callback(self, callback: Callable[[AgentEvent], None] | None) -> None:
        """
        设置统一事件回调（覆盖语义）：传 None 清空所有事件订阅。

        实现机制：清空 `events` 内所有事件类型的订阅，再为 7 种事件类型分别注册一个
        wrapper handler，wrapper 把 payload 与 event_type 包装成 `AgentEvent`
        转发给 `callback`。`ts` 字段由 wrapper 端 default_factory 即时生成。

        需按事件类型 fine-grained 订阅时改用 `agent.events.subscribe(EVENT_X, fn)`。
        """
        self.events.clear()
        if callback is None:
            return
        for evt_type in ALL_EVENT_TYPES:
            def _wrapper(payload: Any, _t: str = evt_type) -> None:
                callback(AgentEvent(type=_t, payload=payload))
            self.events.subscribe(evt_type, _wrapper)

    def run(self, user_input: str) -> str:
        """
        执行完整的 ReAct 循环，返回最终回答文本。

        会先从 ChatHistoryStore 加载历史消息，拼接到当前轮对话后一起发送给 LLM。
        每轮工具调用和最终回答均实时写入 SQLite。

        Args:
            user_input: 用户的自然语言问题。

        Returns:
            Agent 的最终回答字符串。
        """
        # 加载历史，应用截断策略
        history_mgr = HistoryManager(self._chat_history, self.session_id, self.max_history_turns)
        history = history_mgr.load_truncated()

        # 构建 system 消息：base → <project_rules>（静态偏好）→ <user_context>（动态记忆）
        # rules 在前 / memory 在后：memory 是会话中学到的临时偏好，可覆写 rules 的稳定基础
        memory_mgr = MemoryManager(self._user_memory, self._chat_history, self.session_id, chat)
        base_with_rules = self.system_prompt + build_rules_block(_get_shared_project_rules())
        system_content = memory_mgr.build_system_prompt(base_with_rules)

        # 构建当前轮完整 messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
            *history,
            {"role": "user", "content": user_input},
        ]

        # 将当前轮用户输入写入 DB（首次会自动创建 session 记录）
        self._chat_history.append(
            self.session_id,
            {"role": "user", "content": user_input},
        )

        tool_rounds = 0  # 已消耗的工具调用轮次计数
        _prompt_tokens = _comp_tokens = 0  # 本次 run() 各轮累计 token
        # Phase 1.4：每轮 new CitationBuilder，跨同轮多次 search_knowledge 累计编号
        citation_builder = CitationBuilder()
        tool_engine = ToolCallEngine(
            self._chat_history, self.session_id, self._skill_bodies,
            verbose=self.verbose, events=self.events,
            citation_builder=citation_builder,
        )
        thinking_policy = ThinkingPolicy(self.thinking_cfg)

        # session 启动事件（payload 含本轮基础元信息，供监听者关联日志）
        self.events.publish(AgentEvent(
            type=EVENT_INFO,
            payload={"message": "agent.run.start", "session_id": self.session_id},
        ))

        for iteration in range(1, self.max_iterations + 1):
            logger.info("[Agent] 第 %d 轮推理，messages 长度: %d", iteration, len(messages))

            # 工具轮次达上限时，去掉 tools 参数，让 LLM 强制生成文本回答
            active_tools = get_tools(self._skill_bodies) if tool_rounds < MAX_TOOL_ROUNDS else None
            if active_tools is None:
                logger.warning("[Agent] 工具调用已达上限 %d 轮，强制生成最终回答", MAX_TOOL_ROUNDS)

            # 调用 LLM：开启 thinking 时走流式 thinking 分支，否则普通 chat()
            self._thinking_started = False
            try:
                if thinking_policy.enabled:
                    response = call_with_thinking(
                        messages,
                        budget_tokens=thinking_policy.effective_budget(messages),
                        tools=active_tools,
                        on_thinking_chunk=self._on_thinking_chunk,
                        on_token_chunk=self._token_callback_for_provider(),
                    )
                    # CLI 默认 stdout 模式（无 thinking 订阅者）下打印"思考结束"分隔符
                    if self._thinking_started and not self.events.subscribers(EVENT_THINKING_CHUNK):
                        print("\n\n─── 思考结束 ───\n", flush=True)
                else:
                    response = chat(messages, tools=active_tools, on_token_chunk=self._token_callback_for_provider())
            except Exception as exc:
                logger.error("[Agent] LLM 调用异常: %s", exc)
                self.events.publish(AgentEvent(
                    type=EVENT_ERROR,
                    payload={"message": str(exc), "recoverable": False, "phase": "llm_call"},
                ))
                raise
            _u = getattr(response, "usage", None)
            if _u:
                _prompt_tokens += getattr(_u, "prompt_tokens", 0)
                _comp_tokens += getattr(_u, "completion_tokens", 0)
            message = response.choices[0].message

            # ── 情况 1：LLM 决定调用工具 ──────────────────────────────────────
            if message.tool_calls:
                tool_rounds += 1
                tool_engine.process(message, messages)
                continue

            # ── 情况 2：LLM 直接返回最终回答 ──────────────────────────────────
            final_answer = message.content or ""
            if final_answer.strip():
                logger.info("[Agent] 第 %d 轮得到最终回答，退出循环", iteration)
                # Phase 1.4：扫 LLM 正文实际引到的 [n]，按 builder 已注册的编号
                # 渲染 sources 块并拼到 answer 末尾；无引用时 sources_block 为空，
                # 答案保持原样（用户写 rules 禁引时的合法输出）
                final_answer = final_answer.strip()
                used_nums = citation_builder.extract_used(final_answer)
                sources_block = citation_builder.render(used_nums)
                if sources_block:
                    # 把 sources 块也作为 token_chunk emit，让 CLI / Chainlit
                    # 等流式 UI 能在正文 token 流完后继续渲染 sources 块；非流式
                    # UI（EventBus 无 TOKEN_CHUNK 订阅者）这次 publish 静默无副作用
                    self._on_token_chunk(sources_block)
                final_answer = final_answer + sources_block
                # 将最终回答（含 sources 块）写入 DB，下一轮 LLM 可见统一来源
                self._chat_history.append(
                    self.session_id,
                    {"role": "assistant", "content": final_answer},
                )
                self.last_usage = (
                    TokenUsage(_prompt_tokens, _comp_tokens, _prompt_tokens + _comp_tokens)
                    if (_prompt_tokens or _comp_tokens) else None
                )
                # 跨 session 记忆提取：显式触发词 or 自动提取开关
                memory_mgr.try_extract(user_input, final_answer)
                self.events.publish(AgentEvent(
                    type=EVENT_FINAL_ANSWER,
                    payload={"text": final_answer, "usage": self.last_usage},
                ))
                return final_answer

            # LLM 返回了空内容（异常情况），退出
            logger.warning("[Agent] LLM 返回空内容，提前退出")
            self.last_usage = (
                TokenUsage(_prompt_tokens, _comp_tokens, _prompt_tokens + _comp_tokens)
                if (_prompt_tokens or _comp_tokens) else None
            )
            fallback = "抱歉，未能生成有效回答，请重试。"
            self.events.publish(AgentEvent(
                type=EVENT_ERROR,
                payload={"message": "LLM 返回空内容", "recoverable": True, "phase": "empty_response"},
            ))
            self.events.publish(AgentEvent(
                type=EVENT_FINAL_ANSWER,
                payload={"text": fallback, "usage": self.last_usage},
            ))
            return fallback

        # 超过最大迭代次数
        logger.warning("[Agent] 达到最大迭代次数 %d，强制返回", self.max_iterations)
        self.last_usage = (
            TokenUsage(_prompt_tokens, _comp_tokens, _prompt_tokens + _comp_tokens)
            if (_prompt_tokens or _comp_tokens) else None
        )
        fallback = "抱歉，推理过程过于复杂，未能在规定轮次内完成。请尝试更具体的问题。"
        self.events.publish(AgentEvent(
            type=EVENT_ERROR,
            payload={"message": "达到最大迭代次数", "recoverable": False, "phase": "max_iterations"},
        ))
        self.events.publish(AgentEvent(
            type=EVENT_FINAL_ANSWER,
            payload={"text": fallback, "usage": self.last_usage},
        ))
        return fallback

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

