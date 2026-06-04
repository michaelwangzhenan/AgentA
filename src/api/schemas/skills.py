"""Skills 端点响应模型"""

from pydantic import BaseModel


class SkillItem(BaseModel):
    name: str
    description: str
    location: str


class SkillFailure(BaseModel):
    path: str
    reason: str


class SkillsResponse(BaseModel):
    loaded: list[SkillItem]
    failed: list[SkillFailure]
