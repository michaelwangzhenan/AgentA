"""Config 编辑面板请求 / 响应 schema。

新形态（替换原只读视图）：
- ConfigResponse：分组列表，每组含若干 ConfigItemView
- ConfigItemView：单项的 metadata + 当前值 + 来源标识
- ConfigPatchRequest：单项写入请求
- ConfigItemResponse：写入 / reset 后返回单项的最新视图
"""

from typing import Any

from pydantic import BaseModel


class ConfigItemView(BaseModel):
    key: str
    group: str
    type: str
    value: Any
    default: Any
    source: str  # "default" | "override"
    brief: str
    detail: str
    options: list[str] | None = None
    min: float | None = None
    max: float | None = None
    side_effect_hint: str | None = None
    danger: bool = False
    editable: bool = True
    hidden: bool = False  # true 时前端设置面板不渲染（仍可经 API 读写）


class ConfigGroupView(BaseModel):
    name: str
    label: str
    items: list[ConfigItemView]


class ConfigResponse(BaseModel):
    groups: list[ConfigGroupView]


class ConfigPatchRequest(BaseModel):
    value: Any


class ConfigItemResponse(BaseModel):
    item: ConfigItemView


class ConfigReloadResponse(BaseModel):
    changed_keys: list[str]
    config: ConfigResponse


# ── 模型目录（两档：厂商 → 模型） ──────────────────────────────────────────
class ModelOption(BaseModel):
    id: str          # model id，也是 ACTIVE_MODEL 取值
    label: str
    thinking: bool   # 该模型是否支持 Extended Thinking
    tier: str = ""   # 能力/价位档位（min / low / medium / high / max；空 = 不显示徽章）


class ProviderModels(BaseModel):
    name: str        # 厂商 key
    label: str       # 厂商显示名
    models: list[ModelOption]


class ModelsResponse(BaseModel):
    active: str                     # 当前 ACTIVE_MODEL
    providers: list[ProviderModels]
