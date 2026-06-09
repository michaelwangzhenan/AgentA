"""User Memory 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    id: int
    text: str
    source: str
    created_at: str
    updated_at: str


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


class MemoryCreateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = Field("manual", description="auto / explicit / manual；未知降级为 auto")


class MemoryPatchRequest(BaseModel):
    text: str = Field(..., min_length=1)


class MemoryDeleteResponse(BaseModel):
    deleted: bool


class MemoryPatchResponse(BaseModel):
    updated: bool


class MemoryClearResponse(BaseModel):
    cleared: int
