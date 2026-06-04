"""依赖注入：API 层向 Agent core 拿实例的统一入口。

Step 1 用最朴素的默认值实例化单例 Agent；不加载 skills / rules / 自定义 prompt。
Step 5 会把这套配置抽出 composition root 跟 CLI 共用。
"""

from functools import lru_cache

import src.config as _cfg
from src.agent.agent import Agent
from src.agent.agent_api import AgentAPI
from src.agent.core.mcp_manager import MCPManager, get_shared_manager
from src.memory.chat_history import ChatHistoryStore
from src.memory.user_memory import UserMemoryStore


@lru_cache(maxsize=1)
def get_agent() -> AgentAPI:
    """返回进程级单例 Agent。

    用 `lru_cache(maxsize=1)` 实现"首次调用时构造、之后复用"的语义。
    返回 `AgentAPI` 契约类型，调用方不绑定具体实现。
    """
    return Agent(verbose=False)


@lru_cache(maxsize=1)
def get_chat_history() -> ChatHistoryStore:
    """返回进程级单例 ChatHistoryStore（API 层独立 connection，与 Agent 共用底层 DB 文件）。

    SQLite 在文件级锁下天然支持多 connection 串行写。
    """
    return ChatHistoryStore()


@lru_cache(maxsize=1)
def get_user_memory_store() -> UserMemoryStore | None:
    """返回进程级单例 UserMemoryStore；USER_MEMORY_ENABLED=false 时返回 None。

    API 层独立 connection，与 Agent 共用底层 sqlite 文件。
    """
    if not _cfg.USER_MEMORY_ENABLED:
        return None
    return UserMemoryStore(_cfg.USER_MEMORY_DB_PATH)


def get_mcp_manager() -> MCPManager:
    """返回进程级共享 MCPManager（在 Agent 启动时已 start_all）。"""
    return get_shared_manager()
