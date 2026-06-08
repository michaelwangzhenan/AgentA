"""FastAPI app 入口：挂载路由 + 开发期 CORS 中间件。

启动方式：
    uvicorn src.api.main:app --reload --port 8000
"""

# .env 必须在 import src.config 之前加载；否则模块顶层的 os.getenv 拿到默认值
from dotenv import load_dotenv

load_dotenv(override=True)

import atexit  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# 经 src.api.run 启动时，uvicorn 已用 build_uvicorn_log_config 把 root 挂上文件 handler，
# 这里检测到 root 已有 handler 就不再重配（否则会冲掉文件 handler）；只有在被直接
# `uvicorn src.api.main:app` 拉起（root 无 handler）时，才补一套终端 logging 兜底。
from src import log_setup  # noqa: E402

if not logging.getLogger().handlers:
    log_setup.setup_cli_logging(os.getenv("LOG_LEVEL"))
else:
    log_setup.quiet_noisy_loggers()

import src.config as _cfg  # noqa: E402
from src.api import config_overrides as _config_overrides  # noqa: E402

# 加载 .agenta/config_overrides.json，覆盖 _cfg 模块属性。
# 必须在 _bootstrap_mcp / 路由首次读 _cfg 之前 —— 这里是 import-time，
# uvicorn 启动 lifespan 时已经生效。
_config_overrides.apply_overrides()

from src.api.routes import (  # noqa: E402
    admin,
    auth,
    chat,
    config as config_route,
    health,
    kb,
    mcp,
    memory,
    plans,
    quizzes,
    rules,
    sessions,
    skills,
    srs,
)

logger = logging.getLogger(__name__)


def _bootstrap_mcp() -> None:
    """CLI `main.py` 启动时会调 `_bootstrap_mcp`；这里复制一份给 uvicorn 入口走。

    失败 server 由 MCPManager 内部标 failed 并 log warning，本函数只负责
    入口编排（load config → start_all → 注册 atexit shutdown），任何异常吞掉。
    """
    if not _cfg.MCP_ENABLED:
        return
    try:
        from src.agent.core.mcp_config import load_mcp_config
        from src.agent.core.mcp_manager import get_shared_manager
        specs = load_mcp_config()
        if not specs:
            return
        manager = get_shared_manager()
        manager.start_all(specs)
        atexit.register(manager.shutdown)
        statuses = manager.status()
        connected = sum(1 for s in statuses if s["status"] == "connected")
        logger.info("[api] MCP server 已加载（%d/%d connected）", connected, len(statuses))
    except Exception as exc:
        logger.warning("[api] MCP bootstrap 失败：%s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_mcp()
    yield


app = FastAPI(
    title="AgentA Web API",
    description="前端 React 通过 HTTP / SSE 调后端 Agent + RAG",
    version="0.1.0",
    lifespan=lifespan,
)

# 给每个请求生成短 request_id 写进日志上下文，使该请求处理期间的日志带 r:<id>
@app.middleware("http")
async def _request_id_middleware(request, call_next):
    log_setup.set_request_id(uuid.uuid4().hex[:8])
    try:
        return await call_next(request)
    finally:
        log_setup.set_request_id(None)


# 开发期前端 dev server 跑在 :5173，跟后端 :8000 不同源，必须放开 CORS
# 生产期靠 Nginx 反代同源、不走 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(admin.router, prefix="/api", tags=["admin"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(sessions.router, prefix="/api", tags=["sessions"])
app.include_router(kb.router, prefix="/api", tags=["kb"])
app.include_router(memory.router, prefix="/api", tags=["memory"])
app.include_router(rules.router, prefix="/api", tags=["rules"])
app.include_router(skills.router, prefix="/api", tags=["skills"])
app.include_router(mcp.router, prefix="/api", tags=["mcp"])
app.include_router(config_route.router, prefix="/api", tags=["config"])
app.include_router(plans.router, prefix="/api", tags=["plans"])
app.include_router(quizzes.router, prefix="/api", tags=["quizzes"])
app.include_router(srs.router, prefix="/api", tags=["srs"])
