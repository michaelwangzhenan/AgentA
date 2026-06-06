"""SRS（间隔重复）端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class SRSCard(BaseModel):
    id: int
    source_type: str
    source_ref: int | None = None
    front: str
    back: str
    note: str | None = None
    ease_factor: float
    interval_days: int
    repetitions: int
    lapses: int
    next_review_at: str
    last_reviewed_at: str | None = None
    status: str
    created_at: str
    updated_at: str


class SRSCardListResponse(BaseModel):
    cards: list[SRSCard]


# ─── 写端点请求体 ─────────────────────────────────────────────────────────


class CreateCardRequest(BaseModel):
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    note: str = ""


class ReviewCardRequest(BaseModel):
    # 合法值：again / hard / good / easy（由 scheduler 二次校验）
    rating: str
