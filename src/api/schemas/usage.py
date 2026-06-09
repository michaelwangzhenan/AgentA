"""Token 用量统计的请求 / 响应 schema（iter_11）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UsageSummary(BaseModel):
    """概览卡片数据（某时间范围的合计）。"""

    start: int  # 起始 epoch 秒（含）
    end: int    # 结束 epoch 秒（不含）
    range: str
    currency: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    count: int = 0  # 对话次数（per-run）
    cost: float = 0.0
    has_unpriced: bool = False  # 范围内是否有"无单价"的模型（成本可能偏低）


class SeriesRow(BaseModel):
    """趋势图一条（某天 × 某分组键）的聚合值。"""

    date: str           # YYYY-MM-DD（本地时区）
    key: str            # 分组键：model_id / 用户 id 字符串 / "all"
    key_label: str      # 展示名：模型 label / 用户名 / "全部"
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    count: int = 0
    cost: float = 0.0


class UsageSeries(BaseModel):
    """趋势图数据。"""

    start: int
    end: int
    range: str
    group_by: str  # model / user / none
    currency: str
    rows: list[SeriesRow] = Field(default_factory=list)


class UsageEvent(BaseModel):
    """明细一条（= 一次对话 run）。"""

    id: int
    created_at: int  # epoch 秒
    model_id: str
    model_label: str
    tier: str = ""
    thinking: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    session_id: str | None = None
    user_id: int | None = None       # 仅全员明细带
    username: str | None = None      # 仅全员明细带


class UsageEvents(BaseModel):
    """明细分页响应。"""

    events: list[UsageEvent] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
    currency: str = "$"


class UserUsage(BaseModel):
    """全员视图里某用户的合计（用户排行用）。"""

    user_id: int
    username: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    count: int = 0
    cost: float = 0.0


class UserUsageList(BaseModel):
    """用户排行响应。"""

    users: list[UserUsage] = Field(default_factory=list)
    currency: str = "$"


class PricingItem(BaseModel):
    """单价配置一行（某模型）。"""

    model_id: str
    label: str
    provider: str
    provider_label: str
    tier: str = ""
    input_price: float = 0.0
    output_price: float = 0.0
    is_override: bool = False  # 是否被 admin 覆盖（区别于内置默认）


class PricingResponse(BaseModel):
    """单价配置全表（按 provider 分组由前端处理）。"""

    currency: str = "$"
    items: list[PricingItem] = Field(default_factory=list)


class PricingUpdateItem(BaseModel):
    """单价更新一项。"""

    model_id: str
    input_price: float = Field(..., ge=0)
    output_price: float = Field(..., ge=0)


class PricingUpdateRequest(BaseModel):
    """保存单价覆盖（整表或部分）。"""

    items: list[PricingUpdateItem] = Field(default_factory=list)
