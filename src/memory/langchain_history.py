"""
langchain_history —— SessionStore ↔ LangChain 消息桥

提供两类能力（均为 LangChain 子实现专用，故文件以 langchain 命名）：
1. `SQLiteChatMessageHistory`：BaseChatMessageHistory 适配（保留向后兼容，少量代码仍 import）。
2. 模块级转换 / 截断 helper：
   - `to_lc_messages`            ：dict 历史（OpenAI 风格 role/content）→ LangChain BaseMessage
   - `load_truncated_lc_messages`：复用公共层 `HistoryManager` 做截断后再转换

截断 / skill_pair 保护等 loop 语义统一交给公共层 `HistoryManager`（三实现共享），
本模块只做 LangChain 特有的消息类型转换，避免与 Python / AutoGPT 逻辑分叉。
"""
from __future__ import annotations

from typing import Any, List

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.memory.session_store import SessionStore


def to_lc_messages(raw: list[dict[str, Any]]) -> list[BaseMessage]:
    """把 SessionStore 的 dict 历史转换为 LangChain BaseMessage 列表。

    仅保留 user / assistant 正文：
    - system 不进历史（system_prompt 单独拼接）；
    - 纯 tool_call 无正文的 assistant 与 tool 消息跳过（LangChain 侧只需正文上下文，
      工具往返由本轮 graph 重新产生，历史不回放中间步骤）。
    """
    out: list[BaseMessage] = []
    for msg in raw:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant" and content:
            out.append(AIMessage(content=content))
    return out


def load_truncated_lc_messages(
    session_store: SessionStore,
    session_id: str,
    max_history_turns: int,
) -> list[BaseMessage]:
    """复用公共层 `HistoryManager` 截断历史，再转 LangChain 消息（不含本轮 user）。"""
    from src.agent.core.history_manager import HistoryManager

    raw = HistoryManager(session_store, session_id, max_history_turns).load_truncated()
    return to_lc_messages(raw)


class SQLiteChatMessageHistory(BaseChatMessageHistory):
    """BaseChatMessageHistory 适配（向后兼容；新代码优先用上面的 helper + 共享 store）。"""

    def __init__(self, session_id: str, db_path: str | None = None):
        self._session_id = session_id
        self._history = SessionStore(db_path=db_path) if db_path else SessionStore()

    @property
    def messages(self) -> List[BaseMessage]:
        return to_lc_messages(self._history.load(self._session_id))

    def add_message(self, message: BaseMessage) -> None:
        if isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            role = "system"
        self._history.append(self._session_id, {"role": role, "content": message.content})

    def clear(self) -> None:
        self._history.clear(self._session_id)
