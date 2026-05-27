"""
ToolCallEngine —— 工具调用编排（Helper 层）

职责：
- 把 LLM 返回的 assistant.tool_calls 转成标准 dict（`assistant_message`）
- 逐个执行 tool_call → 拿到 ToolResult
- DB 写入"干净内容"（无引导提示），messages 注入"含引导提示"版本
  · 这种"写历史 vs 进 LLM context"的分支属于业务策略，是 Helper 的核心价值
- 全程串到 ChatHistoryStore（依赖层），不感知 thinking / streaming 等其它 loop 状态

被三种 Agent 实现共享：Python / LangChain / AutoGPT。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.agent.core.event_bus import (
    EVENT_TOOL_CALL_END,
    EVENT_TOOL_CALL_START,
    AgentEvent,
    EventBus,
)
from src.agent.tools import execute_tool, ToolResult
from src.memory.chat_history import ChatHistoryStore

if TYPE_CHECKING:
    from src.agent.core.citation_builder import CitationBuilder  # noqa: F401

logger = logging.getLogger(__name__)

# 工具结果预览截断长度
_TOOL_PREVIEW_LEN: int = 100

# search_knowledge 返回空结果时追加给 LLM 的引导提示
TOOL_EMPTY_HINT: str = (
    "\n\n[提示] 知识库中未找到相关内容，请立即调用 web_search 工具搜索关键词，"
    "再从返回的真实 URL 中选择合适的链接调用 fetch_url，不允许直接回答。"
)


class ToolCallEngine:
    """
    工具调用编排 helper。

    Args:
        chat_history:     ChatHistoryStore 实例（CRUD 依赖）。
        session_id:       当前会话 ID。
        skill_bodies:     已加载的 skill 正文映射，传给 execute_tool 用于 load_skill。
        verbose:          是否打印调用 / 结果预览（CLI 调试用）。
        events:           可选 EventBus；非 None 时发出 tool_call_start / tool_call_end 事件。
        citation_builder: 可选 Phase 1.4 引用编排器；非 None 时透传给 execute_tool，
                          仅 search_knowledge 路径会注册 hits、分配编号。
    """

    def __init__(
        self,
        chat_history: ChatHistoryStore,
        session_id: str,
        skill_bodies: dict[str, str],
        verbose: bool = False,
        events: EventBus | None = None,
        citation_builder: "CitationBuilder | None" = None,
    ) -> None:
        self._chat_history = chat_history
        self._session_id = session_id
        self._skill_bodies = skill_bodies
        self._verbose = verbose
        self._events = events
        self._citation_builder = citation_builder

    def process(self, message: Any, messages: list[dict[str, Any]]) -> None:
        """
        执行本轮所有 tool_calls，将结果注入 `messages` 并写入 DB。

        DB 写入使用不含引导提示的干净内容；当前轮 messages 注入含引导提示的版本，
        避免引导提示污染下次加载的历史记录。
        """
        assistant_msg = self.assistant_message(message)
        messages.append(assistant_msg)
        self._chat_history.append(self._session_id, assistant_msg)

        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

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

            # 仅当持有 citation_builder 时才走 kwarg 路径，否则保持现有 3-arg
            # 签名调用，避免破坏外部 mock execute_tool 的测试 fixture
            if self._citation_builder is not None:
                result: ToolResult = execute_tool(
                    tool_name, tool_args, self._skill_bodies,
                    citation_builder=self._citation_builder,
                )
            else:
                result = execute_tool(tool_name, tool_args, self._skill_bodies)

            if self._verbose:
                preview = result.content[:_TOOL_PREVIEW_LEN].replace("\n", " ").replace("\r", " ")
                logger.info("[ToolCallEngine] 工具结果 [%s] 预览: %s", result.status, preview)

            if self._events is not None:
                preview = result.content[:_TOOL_PREVIEW_LEN].replace("\n", " ").replace("\r", " ")
                self._events.publish(AgentEvent(
                    type=EVENT_TOOL_CALL_END,
                    payload={"call_id": tool_call.id, "status": result.status, "preview": preview},
                ))

            # DB 写入干净内容（无引导提示），避免污染历史
            db_content = result.to_llm_str()
            db_msg: dict[str, Any] = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": db_content,
            }
            self._chat_history.append(self._session_id, db_msg)

            # 当前轮 messages 注入含引导提示的版本，引导 LLM 下一步决策
            llm_content = db_content
            if result.status == "error":
                llm_content += "\n\n[提示] 请换一种方式（换参数或换工具）重试，不要直接回答。"
            elif result.status == "empty" and tool_name == "search_knowledge":
                llm_content += TOOL_EMPTY_HINT

            live_msg: dict[str, Any] = {**db_msg, "content": llm_content}
            messages.append(live_msg)

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
