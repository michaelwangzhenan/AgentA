"""Rules 端点请求 / 响应模型（每用户独享，存 auth.db.user_rules）"""

from pydantic import BaseModel, Field


class RulesReadResponse(BaseModel):
    text: str = Field("", description="当前用户的 rules 文本；未设置时返回空字符串")


class RulesWriteRequest(BaseModel):
    text: str = Field(..., description="完整覆盖写入；空串视同清空")


class RulesWriteResponse(BaseModel):
    length: int = Field(..., description="写入后字符数")
