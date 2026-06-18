"""
HistoryManager —— 历史消息加载与截断策略（Helper 层）

职责：封装"何时调 SessionStore CRUD、按什么策略截断、哪些消息要保护"的业务逻辑。
SessionStore 本身只做 CRUD，不感知"轮（turn）/ skill_pair / system 拼接"等 loop 语义。

被三种 Agent 实现共享：Python / LangChain / AutoGPT。
"""
from __future__ import annotations

import logging
from typing import Any

from src.stores.session_store import SessionStore

logger = logging.getLogger(__name__)

# SQL 层粗粒度过滤倍数：每轮实际消息数受 tool_calls 数量影响，× 8 作为安全上限
_HISTORY_FETCH_MULTIPLIER: int = 8


class HistoryManager:
    """
    历史消息管理 helper。

    Args:
        session_store:      底层 SessionStore 实例（CRUD 依赖）。
        session_id:         当前会话 ID。
        max_history_turns:  保留最近 N 轮（一轮以 user 消息为起点）。
    """

    def __init__(
        self,
        session_store: SessionStore,
        session_id: str,
        max_history_turns: int,
    ) -> None:
        self._session_store = session_store
        self._session_id = session_id
        self._max_history_turns = max_history_turns

    def load_truncated(self) -> list[dict[str, Any]]:
        """
        从 SessionStore 加载历史，并按 `max_history_turns` 截断。

        截断策略：
          1. SQL 层粗粒度过滤上限 = `max_history_turns * 8`，避免全量加载长 session
          2. 内存层精确截断：按 user 消息为锚，保留最近 N 轮
          3. system 消息不计入历史（在 run() 中单独拼接）
          4. 被截断段内含 `<skill_content>` 的 assistant+tool 组前置保留（避免协议违反）
        """
        limit = self._max_history_turns * _HISTORY_FETCH_MULTIPLIER
        history = [
            m for m in self._session_store.load_last_n_messages(self._session_id, limit)
            if m["role"] != "system"
        ]

        if not history:
            return []

        user_indices = [i for i, m in enumerate(history) if m["role"] == "user"]
        if len(user_indices) > self._max_history_turns:
            start = user_indices[-self._max_history_turns]
            # 保护被截掉的 skill 内容组，避免孤立 tool 消息违反 OpenAI 协议
            protected = self._collect_skill_pairs(history[:start])
            history = history[start:]
            if protected:
                history = protected + history
                logger.info("[HistoryManager] 保护 %d 条 skill 内容消息免被截断", len(protected))
            logger.info(
                "[HistoryManager] 历史超过 %d 轮，已截断保留最近 %d 轮",
                len(user_indices),
                self._max_history_turns,
            )

        return history

    @staticmethod
    def _collect_skill_pairs(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        从消息列表中提取含 `<skill_content>` 的完整「assistant + 对应 tool」消息组。
        确保不产生孤立的 tool 消息（违反 OpenAI 协议会导致 400 Bad Request）。
        """
        protected: list[dict[str, Any]] = []
        i = 0
        while i < len(msgs):
            m = msgs[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                group: list[dict[str, Any]] = [m]
                j = i + 1
                while j < len(msgs) and msgs[j].get("role") == "tool":
                    group.append(msgs[j])
                    j += 1
                if any("<skill_content" in (msg.get("content") or "") for msg in group):
                    protected.extend(group)
                i = j
            else:
                i += 1
        return protected
