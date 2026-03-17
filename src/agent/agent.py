"""
Agent 主控逻辑 —— ReAct（Reason + Act）循环

执行流程：
    1. 接收用户问题，从 MemoryStore 加载历史消息
    2. 拼接为 [system] + history + [user]，超长时自动截断
    3. 调用 LLM（携带工具定义）
    4. 若 LLM 返回 tool_calls → 执行工具 → 将结果追加到 messages → 继续循环
    5. 若 LLM 直接返回文本 → 输出最终回答，退出循环
    6. 超过最大迭代次数时强制退出，防止死循环

使用方式：
    from agent.agent import Agent
    agent = Agent(session_id="my-session")
    reply = agent.run("什么是 RAG？")
    print(reply)
"""

import json
import logging
import uuid
from typing import Any

from src.agent.tools import TOOLS, execute_tool, ToolResult
from src.llm.provider import chat
from src.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# 模块级共享 MemoryStore 实例（单进程内所有 Agent 共享同一个 DB 连接）
_shared_memory: MemoryStore | None = None


def _get_shared_memory() -> MemoryStore:
    """获取模块级共享 MemoryStore，首次调用时懒加载初始化。"""
    global _shared_memory
    if _shared_memory is None:
        _shared_memory = MemoryStore()
    return _shared_memory

# Agent 系统提示：指导 LLM 的行为策略
SYSTEM_PROMPT = """你是一个私有知识库智能助手。

## 工具使用策略
1. 收到问题后，**优先调用 `search_knowledge`** 在私有知识库中检索相关信息。
2. 若检索结果足以回答问题，直接基于检索内容生成回答。
3. 若 `search_knowledge` 返回"知识库为空"或内容与问题明显无关，**必须主动调用 `fetch_url` 进行网络搜索**，
   不允许直接回复"暂无内容"。选择 URL 时**优先访问国内可达网站**，例如：
   - 新闻资讯：xinhuanet.com、people.com.cn、news.baidu.com
   - 技术问题：segmentfault.com、csdn.net、zhihu.com
   - 通用搜索：baidu.com、so.com（360搜索）
   若国内网站无法提供有效信息，再尝试访问国外网站。
4. 所有工具调用结束后，综合已获取的信息生成最终回答。
5. 若工具返回 [结果为空] 或 [工具失败]：
   - `search_knowledge` 返回 [结果为空] → 立即改调 `fetch_url` 补充搜索，不允许直接回答
   - `fetch_url` 返回 [工具失败] → 换一个同类型的备选 URL 重试（最多换 2 次）
   - 两种工具均无法获取有效信息时，才如实告知用户"当前无法获取相关信息"

## 回答要求
- 回答须基于工具返回的实际内容，不要凭空捏造。
- 若工具未返回有效信息，如实告知用户"知识库中暂无相关内容"。
- 回答简洁、准确，使用中文。
"""

# 最大工具调用轮次，防止 LLM 陷入工具调用死循环
MAX_TOOL_ROUNDS: int = 8
# 含最终回答在内的总推理轮次上限
MAX_TOTAL_ROUNDS: int = 12
# 向后兼容别名
MAX_ITERATIONS: int = MAX_TOTAL_ROUNDS

# search_knowledge 返回空结果时追加给 LLM 的引导提示
TOOL_EMPTY_HINT: str = (
    "\n\n[提示] 知识库中未找到相关内容，请立即改调 fetch_url 工具进行网络搜索，不允许直接回答。"
)


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
        memory: MemoryStore | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.session_id: str = session_id or str(uuid.uuid4())
        self.max_history_turns = max_history_turns
        # 支持从外部传入 memory（便于测试 mock），默认使用模块级共享实例
        self._memory: MemoryStore = memory if memory is not None else _get_shared_memory()

    def run(self, user_input: str) -> str:
        """
        执行完整的 ReAct 循环，返回最终回答文本。

        会先从 MemoryStore 加载历史消息，拼接到当前轮对话后一起发送给 LLM。
        每轮工具调用和最终回答均实时写入 SQLite。

        Args:
            user_input: 用户的自然语言问题。

        Returns:
            Agent 的最终回答字符串。
        """
        # 加载历史，应用截断策略
        history = self._load_truncated_history()

        # 构建当前轮完整 messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *history,
            {"role": "user", "content": user_input},
        ]

        # 将当前轮用户输入写入 DB
        self._memory.append(self.session_id, {"role": "user", "content": user_input})

        tool_rounds = 0  # 已消耗的工具调用轮次计数

        for iteration in range(1, self.max_iterations + 1):
            logger.info("[Agent] 第 %d 轮推理，messages 长度: %d", iteration, len(messages))

            # 工具轮次达上限时，去掉 tools 参数，让 LLM 强制生成文本回答
            # （不注入 user 消息，保持消息序列格式合法）
            active_tools = TOOLS if tool_rounds < MAX_TOOL_ROUNDS else None
            if active_tools is None and tool_rounds >= MAX_TOOL_ROUNDS:
                logger.warning("[Agent] 工具调用已达上限 %d 轮，强制生成最终回答", MAX_TOOL_ROUNDS)

            # 调用 LLM（携带工具定义，或 None 时强制文本回答）
            response = chat(messages, tools=active_tools)
            message = response.choices[0].message

            # ── 情况 1：LLM 决定调用工具 ──────────────────────────────────────
            if message.tool_calls:
                tool_rounds += 1
                # 将 assistant 的 tool_calls 消息追加到 messages 并写入 DB
                assistant_msg = self._assistant_message(message)
                messages.append(assistant_msg)
                self._memory.append(self.session_id, assistant_msg)

                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    if self.verbose:
                        logger.info(
                            "[Agent] 调用工具: %s，参数: %s",
                            tool_name,
                            json.dumps(tool_args, ensure_ascii=False),
                        )

                    # 执行工具，返回结构化 ToolResult
                    result: ToolResult = execute_tool(tool_name, tool_args)

                    if self.verbose:
                        preview = result.content[:100].replace("\n", " ")
                        logger.info(
                            "[Agent] 工具结果 [%s] 预览: %s...", result.status, preview
                        )

                    # 构造写入 LLM 的 content：状态标签 + 引导提示
                    llm_content = result.to_llm_str()
                    if result.status == "error":
                        llm_content += "\n\n[提示] 请换一种方式（换参数或换工具）重试，不要直接回答。"
                    elif result.status == "empty" and tool_name == "search_knowledge":
                        llm_content += TOOL_EMPTY_HINT

                    # 将工具结果追加到 messages 并写入 DB
                    tool_msg: dict = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": llm_content,
                    }
                    messages.append(tool_msg)
                    self._memory.append(self.session_id, tool_msg)

                # 继续下一轮推理
                continue

            # ── 情况 2：LLM 直接返回最终回答 ──────────────────────────────────
            final_answer = message.content or ""
            if final_answer.strip():
                logger.info("[Agent] 第 %d 轮得到最终回答，退出循环", iteration)
                # 将最终回答写入 DB
                self._memory.append(
                    self.session_id,
                    {"role": "assistant", "content": final_answer.strip()},
                )
                return final_answer.strip()

            # LLM 返回了空内容（异常情况），退出
            logger.warning("[Agent] LLM 返回空内容，提前退出")
            return "抱歉，未能生成有效回答，请重试。"

        # 超过最大迭代次数
        logger.warning("[Agent] 达到最大迭代次数 %d，强制返回", self.max_iterations)
        return "抱歉，推理过程过于复杂，未能在规定轮次内完成。请尝试更具体的问题。"

    def _load_truncated_history(self) -> list[dict[str, Any]]:
        """
        从 MemoryStore 加载历史，并按 max_history_turns 截断。

        截断策略：保留最近 N 轮，一轮以 user 消息为起点计数。
        system 消息不计入轮数，在 run() 中单独拼接。
        """
        all_msgs = self._memory.load(self.session_id)
        # 过滤掉 system 消息（由 run() 单独拼接）
        history = [m for m in all_msgs if m["role"] != "system"]

        if not history:
            return []

        # 找到所有 user 消息的位置，按轮数从后往前截断
        user_indices = [i for i, m in enumerate(history) if m["role"] == "user"]
        if len(user_indices) > self.max_history_turns:
            start = user_indices[-self.max_history_turns]
            history = history[start:]
            logger.info(
                "[Agent] 历史超过 %d 轮，已截断保留最近 %d 轮",
                len(user_indices),
                self.max_history_turns,
            )

        return history

    @staticmethod
    def _assistant_message(message: Any) -> dict[str, Any]:
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
