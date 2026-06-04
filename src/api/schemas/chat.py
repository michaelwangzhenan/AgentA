"""Chat 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户输入文本")
    session_id: str | None = Field(
        None,
        description="目标 session id；不传则用 Agent 当前 session_id（兼容 Step 2 行为）",
    )


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Agent 最终回答")
    session_id: str = Field(..., description="当前 Agent 实例的 session id")
