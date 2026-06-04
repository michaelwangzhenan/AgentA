"""Session 管理端点请求 / 响应模型"""

from typing import Any

from pydantic import BaseModel, Field


class SessionInfo(BaseModel):
    """list / get 返回的单条 session 元数据"""

    id: str = Field(..., description="会话 ID")
    title: str = Field("", description="会话标题（自动取首条用户消息预览或用户手动改名）")
    created_at: str = Field(..., description="创建时间，ISO 8601 本地时间")
    msg_count: int = Field(0, description="该会话累计消息数（不含 system）")


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]


class SessionCreateRequest(BaseModel):
    title: str | None = Field(None, description="可选初始标题；不传则为空")


class SessionRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, description="新标题，非空")


class SessionDeleteResponse(BaseModel):
    deleted: bool = Field(..., description="是否实际删除了记录（False = session 不存在）")


class SessionMessagesResponse(BaseModel):
    """拉某 session 的完整 messages 历史"""

    messages: list[dict[str, Any]] = Field(
        ...,
        description="OpenAI messages 格式（含 tool_calls / tool_call_id 等可选字段）",
    )
