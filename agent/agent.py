"""
Agent 主控逻辑 —— ReAct（Reason + Act）循环

执行流程：
    1. 接收用户问题，构建初始 messages
    2. 调用 LLM（携带工具定义）
    3. 若 LLM 返回 tool_calls → 执行工具 → 将结果追加到 messages → 继续循环
    4. 若 LLM 直接返回文本 → 输出最终回答，退出循环
    5. 超过最大迭代次数时强制退出，防止死循环

使用方式：
    from agent.agent import Agent
    agent = Agent()
    reply = agent.run("什么是 RAG？")
    print(reply)
"""

import json
import logging
from typing import Any

from agent.tools import TOOLS, execute_tool
from llm.provider import chat

logger = logging.getLogger(__name__)

# Agent 系统提示：指导 LLM 的行为策略
SYSTEM_PROMPT = """你是一个私有知识库智能助手。

## 工具使用策略
1. 收到问题后，**优先调用 `search_knowledge`** 在私有知识库中检索相关信息。
2. 若检索结果足以回答问题，直接基于检索内容生成回答。
3. 若知识库无相关内容，可调用 `fetch_url` 抓取指定网页获取补充信息。
4. 所有工具调用结束后，综合已获取的信息生成最终回答。

## 回答要求
- 回答须基于工具返回的实际内容，不要凭空捏造。
- 若工具未返回有效信息，如实告知用户"知识库中暂无相关内容"。
- 回答简洁、准确，使用中文。
"""

# 最大 ReAct 循环迭代次数，防止 LLM 无限调用工具
MAX_ITERATIONS: int = 10


class Agent:
    """
    ReAct Agent：通过 LLM + Function Calling 实现推理与工具调用的循环。

    Attributes:
        system_prompt: Agent 的系统提示，定义行为策略。
        max_iterations: 最大工具调用轮次，超出后强制返回当前结果。
        verbose: 是否打印每轮工具调用的调试信息。
    """

    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int = MAX_ITERATIONS,
        verbose: bool = True,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.verbose = verbose

    def run(self, user_input: str) -> str:
        """
        执行完整的 ReAct 循环，返回最终回答文本。

        Args:
            user_input: 用户的自然语言问题。

        Returns:
            Agent 的最终回答字符串。
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]

        for iteration in range(1, self.max_iterations + 1):
            logger.debug(f"[Agent] 第 {iteration} 轮推理，messages 长度: {len(messages)}")

            # 调用 LLM（携带工具定义）
            response = chat(messages, tools=TOOLS)
            message = response.choices[0].message

            # ── 情况 1：LLM 决定调用工具 ──────────────────────────────────────
            if message.tool_calls:
                # 将 assistant 的 tool_calls 消息追加到 messages
                messages.append(self._assistant_message(message))

                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    if self.verbose:
                        logger.info(
                            f"[Agent] 调用工具: {tool_name}，"
                            f"参数: {json.dumps(tool_args, ensure_ascii=False)}"
                        )

                    # 执行工具
                    tool_result = execute_tool(tool_name, tool_args)

                    if self.verbose:
                        preview = tool_result[:100].replace("\n", " ")
                        logger.info(f"[Agent] 工具结果预览: {preview}...")

                    # 将工具结果追加到 messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    })

                # 继续下一轮推理
                continue

            # ── 情况 2：LLM 直接返回最终回答 ──────────────────────────────────
            final_answer = message.content or ""
            if final_answer.strip():
                logger.debug(f"[Agent] 第 {iteration} 轮得到最终回答，退出循环")
                return final_answer.strip()

            # LLM 返回了空内容（异常情况），退出
            logger.warning("[Agent] LLM 返回空内容，提前退出")
            return "抱歉，未能生成有效回答，请重试。"

        # 超过最大迭代次数
        logger.warning(f"[Agent] 达到最大迭代次数 {self.max_iterations}，强制返回")
        return "抱歉，推理过程过于复杂，未能在规定轮次内完成。请尝试更具体的问题。"

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
