"""
API key 配置端点，仅 admin：读写 .agenta/api_keys.json 中的 provider key 覆盖项。

- GET /api/api-keys：列出可配 key 的脱敏视图（永不返回明文）
- PUT /api/api-keys/{key_id}：设置某项 key（空串等于清除 override）
- DELETE /api/api-keys/{key_id}：清除 override，恢复 .env 初始值
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.runtime import api_keys as _store
from src.api.deps import require_admin
from src.api.schemas.api_keys import (
    ApiKeysResponse,
    ApiKeyUpdateRequest,
    ApiKeyView,
)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _build_view(item: _store.SecretItem) -> ApiKeyView:
    value = _store.current_value(item)
    return ApiKeyView(
        id=item.id,
        label=item.label,
        env=item.env,
        configured=bool(value),
        masked=_store.mask(value),
        source="override" if _store.is_overridden(item.id) else "env",
    )


@router.get("", response_model=ApiKeysResponse)
def list_api_keys(_: dict = Depends(require_admin)) -> ApiKeysResponse:
    return ApiKeysResponse(items=[_build_view(it) for it in _store.SECRET_ITEMS])


@router.put("/{key_id}", response_model=ApiKeyView)
def set_api_key(
    key_id: str, req: ApiKeyUpdateRequest, _: dict = Depends(require_admin)
) -> ApiKeyView:
    item = _store.get_item(key_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"未知 API key：{key_id}"
        )
    value = req.value.strip()
    if value:
        _store.set_key(key_id, value)
    else:
        # 传空串视为清除 override，恢复 .env 值
        _store.clear_key(key_id)
    return _build_view(item)


@router.delete("/{key_id}", response_model=ApiKeyView)
def reset_api_key(key_id: str, _: dict = Depends(require_admin)) -> ApiKeyView:
    item = _store.get_item(key_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"未知 API key：{key_id}"
        )
    _store.clear_key(key_id)
    return _build_view(item)
