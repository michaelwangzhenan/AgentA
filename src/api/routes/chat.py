"""Chat 端点 —— 非流式最小聊天回路（iter 4 Step 1）"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.agent.agent_api import AgentAPI
from src.api.deps import get_agent
from src.api.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, agent: AgentAPI = Depends(get_agent)) -> ChatResponse:
    """单轮聊天：转发用户消息给 Agent.run、返回完整答案。

    同步路由（不加 async）—— FastAPI 会自动把它扔到 thread pool 跑，
    不阻塞 event loop。
    """
    try:
        reply = agent.run(req.message)
    except Exception as exc:
        logger.exception("[/api/chat] agent.run 抛异常")
        raise HTTPException(status_code=500, detail=f"agent error: {exc}") from exc

    return ChatResponse(reply=reply, session_id=agent.session_id)
