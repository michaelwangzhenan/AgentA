"""Chat 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户输入文本")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Agent 最终回答")
    session_id: str = Field(..., description="当前 Agent 实例的 session id")
