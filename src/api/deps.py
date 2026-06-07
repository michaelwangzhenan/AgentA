"""依赖注入：API 层向 Agent core 拿实例的统一入口。

进程级单例，分两类策略：

1. **shared singleton**（plan / quiz / srs / mcp）：复用各 store 模块的
   `get_shared_store()`，跟 LLM 工具共用同一份 connection，无写锁竞争。
2. **独立 connection**（chat_history / user_memory）：API 层用 `lru_cache`
   各起一份 connection，跟 Agent 内置 store 走两个连接、共用底层 DB 文件。
   SQLite 文件级锁保证安全，多 connection 串行写不会损坏数据。

两套并存的历史原因：plan / quiz / srs 的 store 早期就提供了 `get_shared_store()`
便于 LLM 工具复用；chat_history / user_memory 没有，暂不改动以缩小影响面。
未来可统一为 shared，但代价是 Agent 构造路径也要改。
"""

from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, Request, status

import src.config as _cfg
from src.agent.agent import Agent
from src.agent.agent_api import AgentAPI
from src.agent.core.mcp_manager import MCPManager, get_shared_manager
from src.memory.chat_history import ChatHistoryStore
from src.memory.learning_plan_store import LearningPlanStore
from src.memory.learning_plan_store import get_shared_store as _get_shared_plan_store
from src.memory.quiz_store import QuizStore
from src.memory.quiz_store import get_shared_store as _get_shared_quiz_store
from src.memory.srs_store import SRSStore
from src.memory.srs_store import get_shared_store as _get_shared_srs_store
from src.memory.user_memory import UserMemoryStore
from src.memory.user_store import ROLE_ADMIN, UserStore
from src.memory.user_store import get_shared_store as _get_shared_user_store


@lru_cache(maxsize=1)
def get_agent() -> AgentAPI:
    """返回进程级单例 Agent。

    用 `lru_cache(maxsize=1)` 实现"首次调用时构造、之后复用"的语义。
    构造时扫一次 `.agenta/skills/` 注入 Agent，让 LLM 能看到 `## Skills` catalog
    并可调 `load_skill` 工具。Web UI 通过 `POST /api/skills/reload` 调
    `get_agent.cache_clear()` 强制重建实例以读到磁盘新内容（trade-off：会重新打开
    sub-store 连接，但下一轮对话立即看到新 catalog）。

    返回 `AgentAPI` 契约类型，调用方不绑定具体实现。
    """
    from src.skills.skill_loader import scan_skills
    skills_map = scan_skills().loaded
    return Agent(verbose=False, skills=skills_map or None)


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


def get_plan_store() -> LearningPlanStore:
    """复用 learning_plan_store 进程内共享单例（跟 LLM 工具同连接，避免 SQLite 写锁竞争）。"""
    return _get_shared_plan_store()


def get_quiz_store() -> QuizStore:
    """复用 quiz_store 进程内共享单例。"""
    return _get_shared_quiz_store()


def get_srs_store() -> SRSStore:
    """复用 srs_store 进程内共享单例。"""
    return _get_shared_srs_store()


def get_user_store() -> UserStore:
    """复用 user_store 进程内共享单例（账号 / 登录态 / 每用户 rules）。"""
    return _get_shared_user_store()


# 关认证时回落到的默认用户（CLI / 单机自用）；admin 角色让 admin 门也能过
_ANON_USER: dict[str, Any] = {
    "id": _cfg.DEFAULT_USER_ID,
    "username": "local",
    "role": ROLE_ADMIN,
    "created_at": "",
}


def get_current_user(
    request: Request,
    store: UserStore = Depends(get_user_store),
) -> dict[str, Any]:
    """解析当前登录用户：读 cookie token → 查 auth_sessions → 取 user。

    `AUTH_ENABLED=false` 时跳过校验，回落到默认用户（id=DEFAULT_USER_ID，admin）。
    未登录 / token 失效 → 401。
    """
    if not _cfg.AUTH_ENABLED:
        return dict(_ANON_USER)
    token = request.cookies.get(_cfg.AUTH_COOKIE_NAME)
    user = store.get_user_by_token(token or "")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或登录已过期"
        )
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """在 get_current_user 之上要求 admin 角色，否则 403。"""
    if user.get("role") != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限"
        )
    return user
