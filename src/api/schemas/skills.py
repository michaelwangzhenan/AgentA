"""Skills 端点请求 / 响应模型"""

from typing import Any

from pydantic import BaseModel, Field


class SkillItem(BaseModel):
    name: str
    description: str
    location: str
    body: str = Field(default="", description="SKILL.md frontmatter 之后的 Markdown 正文")
    frontmatter_extra: dict[str, Any] = Field(
        default_factory=dict,
        description="name/description 之外的 frontmatter 字段（如 allowed-tools）；passthrough 保留",
    )


class SkillFailure(BaseModel):
    path: str
    reason: str


class SkillsResponse(BaseModel):
    loaded: list[SkillItem] = Field(description="启用且加载成功（进 ## Skills catalog）")
    disabled: list[SkillItem] = Field(
        default_factory=list,
        description="解析成功但被禁用（UI 显示，不进 catalog）",
    )
    failed: list[SkillFailure] = Field(default_factory=list)


class SkillReloadResponse(BaseModel):
    """POST /api/skills/reload 返回值"""

    loaded_count: int = Field(description="重新扫描后启用且加载成功的 skill 数")
    disabled_count: int = Field(default=0, description="被禁用的 skill 数")
    failed_count: int = Field(description="解析失败的 skill 数")


class SkillCreateRequest(BaseModel):
    """POST /api/skills 创建新 skill"""

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    body: str = Field(default="", description="SKILL.md 正文；可空")
    frontmatter_extra: dict[str, Any] = Field(
        default_factory=dict,
        description="name/description 之外的 frontmatter 字段（按原样写入 SKILL.md）",
    )


class SkillUpdateRequest(BaseModel):
    """PUT /api/skills/{name} 更新现有 skill；改名走单独的 rename 端点"""

    description: str = Field(min_length=1)
    body: str = Field(default="")
    frontmatter_extra: dict[str, Any] | None = Field(
        default=None,
        description="None=保留磁盘原有 extra；{}=清空；非空 dict=整体替换",
    )


class SkillRenameRequest(BaseModel):
    """POST /api/skills/{name}/rename 改名"""

    new_name: str = Field(min_length=1, max_length=64)


class SkillToggleRequest(BaseModel):
    """POST /api/skills/{name}/toggle 启用 / 禁用"""

    enabled: bool


class SkillToggleResponse(BaseModel):
    name: str
    enabled: bool
