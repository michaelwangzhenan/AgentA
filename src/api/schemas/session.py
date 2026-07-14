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
    """拉某 session 的消息历史（支持分页）"""

    messages: list[dict[str, Any]] = Field(
        ...,
        description="OpenAI messages 格式（含 id / user_index / tool_calls 等可选字段）",
    )
    has_more: bool = Field(
        False,
        description="是否还有更早的消息（上滚加载时传 oldest_id 为 before_id）",
    )
    oldest_id: int | None = Field(
        None,
        description="本页最早一条消息的 DB id，作为下一页 before_id 游标",
    )


class SessionTruncateRequest(BaseModel):
    """编辑重发 / 重新生成：从第 user_message_index 条 user 消息起截断"""

    user_message_index: int = Field(
        ..., ge=0, description="第几条 user 消息（0 基），从它起（含）删除后续全部消息"
    )


class SessionTruncateResponse(BaseModel):
    deleted: int = Field(..., description="实际删除的消息行数")
