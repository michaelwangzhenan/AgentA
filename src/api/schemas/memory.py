"""User Memory 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    id: int
    category: str
    key: str
    value: str
    source: str
    created_at: str
    accessed_at: str


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


class MemoryUpsertRequest(BaseModel):
    category: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    source: str = Field("manual", description="auto / explicit / manual；未知降级为 auto")


class MemoryPatchRequest(BaseModel):
    value: str = Field(..., min_length=1)


class MemoryDeleteResponse(BaseModel):
    deleted: bool


class MemoryClearResponse(BaseModel):
    cleared: int
