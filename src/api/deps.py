"""依赖注入：API 路由从这里拿 Agent、各 store、当前用户等进程级单例。

Store 获取分两类：

1. 模块级共享（plan / quiz / srs / golden / usage / trace / user / mcp 等）：
   直接调各 store 的 get_shared_store()，与 LLM 工具共用同一条 SQLite 连接。
2. 双连接（session_store / user_memory）：
   本文件用 lru_cache 各维护一份实例；Agent 在 agent_commons / agent.py
   里另有模块级单例。两边连同一 DB 文件，各实例自带 threading.Lock，
   SQLite 文件锁兜底，正确性无虞，高并发下偶有 database is locked 重试。

历史原因：plan 等 store 早已在模块内提供 get_shared_store()；session / memory
的单例仍留在 Agent 侧，API 未接入。日后若统一，应把 get_shared_store 下沉
到 session_store / user_memory 模块，而非让本文件依赖 agent 包。
"""

import logging
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

import src.config as _cfg
from src.stores.user_context import set_current_user
from src.agent.agent import Agent
from src.agent.agent_api import AgentAPI
from src.agent.core.mcp_manager import MCPManager, get_shared_manager
from src.stores.session_store import SessionStore
from src.stores.learning_plan_store import LearningPlanStore
from src.stores.learning_plan_store import get_shared_store as _get_shared_plan_store
from src.stores.quiz_store import QuizStore
from src.stores.quiz_store import get_shared_store as _get_shared_quiz_store
from src.stores.srs_store import SRSStore
from src.stores.srs_store import get_shared_store as _get_shared_srs_store
from src.stores.golden_store import GoldenStore
from src.stores.golden_store import get_shared_store as _get_shared_golden_store
from src.stores.security_event_store import SecurityEventStore
from src.stores.security_event_store import get_shared_store as _get_shared_security_event_store
from src.stores.trace_store import TraceStore
from src.stores.trace_store import get_shared_store as _get_shared_trace_store
from src.stores.user_memory import UserMemoryStore
from src.stores.usage_store import UsageStore
from src.stores.usage_store import get_shared_store as _get_shared_usage_store
from src.stores.user_store import ROLE_ADMIN, UserStore
from src.stores.user_store import get_shared_store as _get_shared_user_store

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_agent() -> AgentAPI:
    """返回进程级单例 Agent（AgentAPI 契约，调用方不绑定具体实现）。

    lru_cache 懒加载：首次调用构造，之后复用。构造时扫描 .agenta/skills/ 注入
    catalog，供 load_skill 使用。POST /api/skills/reload 调 cache_clear() 强制重建，
    下一轮对话即见新 skills；各 store 单例独立维护，不受此次重建影响。

    按 IMP_METHOD 选 PYTHON / LANGCHAIN / AUTOGPT，与 CLI make_agent 同源；
    改 IMP_METHOD 后 config hook 清缓存，下次请求重建。

    LANGCHAIN / AUTOGPT 未做每请求状态隔离，多用户并发可能串台，仅适合单用户
    或横向对比；生产环境应使用 PYTHON。
    """
    from src.agent.core.skill_loader import scan_skills
    skills_map = scan_skills().loaded or None
    imp = (_cfg.IMP_METHOD or "PYTHON").upper()
    logger.info("[get_agent] 构造 Agent 实例，IMP_METHOD=%s", imp)
    if imp == "LANGCHAIN":
        from src.agent.langchain_agent import LangChainAgent
        return LangChainAgent(verbose=False, skills=skills_map)
    if imp == "AUTOGPT":
        from src.agent.autogpt_agent import AutoGPTAgent
        return AutoGPTAgent(verbose=False, skills=skills_map)
    return Agent(verbose=False, skills=skills_map)


@lru_cache(maxsize=1)
def get_session_store() -> SessionStore:
    """返回进程级单例 SessionStore（API 层独立 connection，与 Agent 共用底层 DB 文件）。

    SQLite 在文件级锁下天然支持多 connection 串行写。
    """
    return SessionStore()


@lru_cache(maxsize=1)
def get_user_memory_store() -> UserMemoryStore | None:
    """返回进程级单例 UserMemoryStore；USER_MEMORY_ENABLED=false 时返回 None。

    API 层独立 connection，与 Agent 共用底层 sqlite 文件。旧版结构化 schema 触发
    fail-fast（RuntimeError）时转成带操作指引的 503，避免给前端裸抛 500 + traceback。
    lru_cache 不缓存异常，删库重建后下次调用会重新构造成功。
    """
    if not _cfg.USER_MEMORY_ENABLED:
        return None
    try:
        return UserMemoryStore(_cfg.USER_MEMORY_DB_PATH)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


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


def get_usage_store() -> UsageStore:
    """复用 usage_store 进程内共享单例（token 用量记录 + 单价覆盖）。"""
    return _get_shared_usage_store()


def get_trace_store() -> TraceStore:
    """复用 trace_store 进程内共享单例（对话分阶段 trace，写 usage.db）。"""
    return _get_shared_trace_store()


def get_golden_store() -> GoldenStore:
    """复用 golden_store 进程内共享单例（RAG golden CRUD + 审核状态）。"""
    return _get_shared_golden_store()


def get_security_event_store() -> SecurityEventStore:
    """复用 security_event_store 进程内共享单例（实时安全拦截事件，写 usage.db）。"""
    return _get_shared_security_event_store()


# 关认证时回落到的默认用户（CLI / 单机自用）；admin 角色让 admin 门也能过
_ANON_USER: dict[str, Any] = {
    "id": _cfg.DEFAULT_USER_ID,
    "username": "local",
    "role": ROLE_ADMIN,
    "created_at": "",
}


def _resolve_current_user(request: Request, store: UserStore) -> dict[str, Any]:
    """同步解析登录用户（读 cookie token → 查 auth_sessions → 取 user），不碰 contextvar。

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


async def get_current_user(
    request: Request,
    store: UserStore = Depends(get_user_store),
) -> dict[str, Any]:
    """解析当前登录用户，并把 user_id 绑定到请求级 contextvar。

    必须是 async：FastAPI 同步路由跑在线程池里，依赖在同步线程内 set 的 contextvar
    不会传到路由（线程池只拿当前 context 的一份拷贝，改动随即丢弃）。只有在请求的
    async 上下文里 set，run_in_threadpool 才会把它一并拷进路由线程，让路由内任何漏传
    user_id、回落到 current_user_id() 的 store 调用都落到本人，而不是静默回落到
    DEFAULT_USER_ID（曾导致非 1 号用户读不到自己的会话历史）。

    DB 查询仍丢到线程池，避免阻塞 event loop（与全 app 同步路由的设计一致）。
    每个请求是独立 asyncio task、有独立 context 拷贝，故 set 无需 reset、不跨请求泄漏。
    """
    user = await run_in_threadpool(_resolve_current_user, request, store)
    set_current_user(user["id"])
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """在 get_current_user 之上要求 admin 角色，否则 403。"""
    if user.get("role") != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限"
        )
    return user
