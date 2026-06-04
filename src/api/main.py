"""FastAPI app 入口：挂载路由 + 开发期 CORS 中间件。

启动方式：
    uvicorn src.api.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import chat, health, kb, sessions

app = FastAPI(
    title="AgentA Web API",
    description="前端 React 通过 HTTP / SSE 调后端 Agent + RAG",
    version="0.1.0",
)

# 开发期前端 dev server 跑在 :5173，跟后端 :8000 不同源，必须放开 CORS
# 生产期靠 Nginx 反代同源、不走 CORS（详 docs/iter_4_UI.md §5.1.6）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(sessions.router, prefix="/api", tags=["sessions"])
app.include_router(kb.router, prefix="/api", tags=["kb"])
