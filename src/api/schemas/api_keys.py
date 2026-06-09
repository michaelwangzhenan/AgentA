"""API key 配置端点的请求 / 响应 schema。

GET 永不返回明文：只给脱敏串 + 是否已配置 + 来源。
"""

from pydantic import BaseModel


class ApiKeyView(BaseModel):
    id: str
    label: str
    env: str               # 对应环境变量名（UI 提示）
    configured: bool       # 当前是否有非空值
    masked: str            # 脱敏串（如 sk-…3f9a），未配置为空串
    source: str            # "env" | "override"


class ApiKeysResponse(BaseModel):
    items: list[ApiKeyView]


class ApiKeyUpdateRequest(BaseModel):
    value: str             # 空串等同清除 override（恢复 .env 值）
