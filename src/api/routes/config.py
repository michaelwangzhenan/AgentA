"""Config 编辑面板端点。

- `GET /api/config`：返回分组 + 每项 metadata + 当前值 + 来源（default / override）
- `PATCH /api/config/{key}`：写入单项；后端校验 → setattr 到 src.config → 持久化到
  `.agenta/config_overrides.json` → 触发副作用 hook（如 LOG_LEVEL / MCP 重载）
- `DELETE /api/config/{key}`：清除该项 override，恢复到启动时 initial 值
- `POST /api/config/reload`：用户手动改了 overrides 文件后，把磁盘内容同步到 `_cfg` +
  触发变化项的副作用 hook（无需重启 uvicorn）

**API key 等敏感字段不在 registry 中**，永不暴露 / 永不允许修改（详 docs/iter_4_UI2_plus.md §1.1.3）。
"""

from fastapi import APIRouter, HTTPException, status

import src.config as _cfg
from src.api import config_hooks, config_overrides
from src.api.config_meta import (
    GROUP_LABELS,
    REGISTRY,
    ConfigItem,
    get_item,
    validate_value,
)
from src.api.schemas.config import (
    ConfigGroupView,
    ConfigItemResponse,
    ConfigItemView,
    ConfigPatchRequest,
    ConfigReloadResponse,
    ConfigResponse,
)

router = APIRouter(prefix="/config", tags=["config"])


def _build_view(item: ConfigItem) -> ConfigItemView:
    overridden = config_overrides.is_overridden(item.key)
    return ConfigItemView(
        key=item.key,
        group=item.group,
        type=item.type.value,
        value=getattr(_cfg, item.key, None),
        default=config_overrides.get_initial_value(item.key),
        source="override" if overridden else "default",
        brief=item.brief,
        detail=item.detail,
        options=item.resolve_options(),
        min=item.min,
        max=item.max,
        side_effect_hint=item.side_effect_hint,
        danger=item.danger,
        editable=item.editable,
    )


@router.get("", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    by_group: dict[str, list[ConfigItemView]] = {g: [] for g in GROUP_LABELS}
    for item in REGISTRY:
        by_group.setdefault(item.group, []).append(_build_view(item))
    groups = [
        ConfigGroupView(name=g, label=GROUP_LABELS[g], items=by_group[g])
        for g in GROUP_LABELS
        if by_group.get(g)
    ]
    return ConfigResponse(groups=groups)


@router.patch("/{key}", response_model=ConfigItemResponse)
def patch_config(key: str, req: ConfigPatchRequest) -> ConfigItemResponse:
    item = get_item(key)
    if item is None or not item.editable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown or non-editable config key: {key}",
        )
    try:
        normalized = validate_value(item, req.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    old_value = getattr(_cfg, key, None)
    config_overrides.set_override(key, normalized)
    config_hooks.run_post_change_hook(key, old_value, normalized)
    return ConfigItemResponse(item=_build_view(item))


@router.delete("/{key}", response_model=ConfigItemResponse)
def reset_config(key: str) -> ConfigItemResponse:
    item = get_item(key)
    if item is None or not item.editable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown or non-editable config key: {key}",
        )
    old_value = getattr(_cfg, key, None)
    new_value = config_overrides.clear_override(key)
    config_hooks.run_post_change_hook(key, old_value, new_value)
    return ConfigItemResponse(item=_build_view(item))


@router.post("/reload", response_model=ConfigReloadResponse)
def reload_config() -> ConfigReloadResponse:
    """从磁盘 overrides 文件重新加载，同步到 _cfg 并触发变化项的 hook。"""
    changed = config_overrides.reload_from_file()
    return ConfigReloadResponse(
        changed_keys=sorted(changed.keys()),
        config=get_config(),
    )
