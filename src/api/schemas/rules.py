"""Rules 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class RulesReadResponse(BaseModel):
    text: str = Field("", description="rules 文件内容；不存在时返回空字符串")
    path: str = Field(..., description="config.USER_RULES_FILE 解析后的绝对路径")
    exists: bool = Field(..., description="文件是否真实存在")


class RulesWriteRequest(BaseModel):
    text: str = Field(..., description="完整覆盖写入；空串视同删除内容（不删文件）")


class RulesWriteResponse(BaseModel):
    path: str
    length: int = Field(..., description="写入后字符数")
    restart_required: bool = Field(
        True,
        description="当前 process 的 Agent 已缓存 rules；改完需要重启 uvicorn 或建新 session 生效",
    )
