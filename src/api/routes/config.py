"""
Config 编辑面板端点：读写 .agenta/config_overrides.json 中的运行时配置覆盖项。

- GET /api/config/models：按厂商分组的可选模型目录（供前端级联菜单）
- GET /api/config：返回分组 + 每项 metadata + 当前值 + 来源（default / override）
- PATCH /api/config/{key}：写入单项 override，校验后 setattr 并触发副作用 hook
- DELETE /api/config/{key}：清除单项 override，恢复启动时初始值
- POST /api/config/reload：从磁盘重读 overrides 并同步到运行时（API key 等敏感项不在 registry）
"""

from fastapi import APIRouter, Depends, HTTPException, status

import src.config as _cfg
from src.api.deps import get_current_user, require_admin
from src.api.runtime import config_hooks, config_overrides
from src.api.runtime.config_meta import (
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
    ModelOption,
    ModelsResponse,
    ProviderModels,
)

router = APIRouter(prefix="/config", tags=["config"])


def _build_view(item: ConfigItem) -> ConfigItemView:
    overridden = config_overrides.is_overridden(item.key)
    return ConfigItemView(
        key=item.key,
        group=item.group,
        section=item.section,
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
        hidden=item.hidden,
    )


@router.get("/models", response_model=ModelsResponse)
def list_models(_: dict = Depends(get_current_user)) -> ModelsResponse:
    """两档模型目录：按厂商分组的可选模型，供前端级联菜单使用。"""
    by_provider: dict[str, list[ModelOption]] = {}
    for mid, m in _cfg.MODEL_CONFIGS.items():
        by_provider.setdefault(m.provider, []).append(
            ModelOption(
                id=mid,
                label=m.label or mid,
                thinking=m.thinking is not None,
                tier=m.tier,
            )
        )
    providers = [
        ProviderModels(
            name=pname,
            label=_cfg.PROVIDER_CONFIGS[pname].label or pname,
            models=models,
        )
        for pname, models in by_provider.items()
    ]
    # 评委默认模型：EVAL_JUDGE_MODEL 合法则给前端直接选中显示，否则空（前端回落被测模型）
    judge = (getattr(_cfg, "EVAL_JUDGE_MODEL", "") or "").strip()
    if judge not in _cfg.MODEL_CONFIGS:
        judge = ""
    return ModelsResponse(active=_cfg.ACTIVE_MODEL, eval_judge=judge, providers=providers)


@router.get("", response_model=ConfigResponse)
def get_config(_: dict = Depends(get_current_user)) -> ConfigResponse:
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
def patch_config(
    key: str, req: ConfigPatchRequest, _: dict = Depends(require_admin)
) -> ConfigItemResponse:
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
def reset_config(key: str, _: dict = Depends(require_admin)) -> ConfigItemResponse:
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
def reload_config(_: dict = Depends(require_admin)) -> ConfigReloadResponse:
    """从磁盘 overrides 文件重新加载，同步到 _cfg 并触发变化项的 hook。"""
    changed = config_overrides.reload_from_file()
    return ConfigReloadResponse(
        changed_keys=sorted(changed.keys()),
        config=get_config(),
    )
