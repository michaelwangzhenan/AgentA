"""SRS（间隔重复）端点响应模型"""

from pydantic import BaseModel


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
