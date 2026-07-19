"""
模型路由候选池端点，仅 admin：管理 .agenta/routing_pool.json 中的可降级模型池。

- GET /api/routing/pool：读候选池（空表示未配置，回落 provider 已配 key 的模型集合）
- PUT /api/routing/pool：写候选池
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import src.config as _cfg
from src.api.deps import get_current_user, require_admin
from src.llm import model_router

router = APIRouter(prefix="/routing", tags=["routing"])


class RoutingModel(BaseModel):
    model_id: str
    label: str
    provider: str
    provider_label: str
    tier: str
    available: bool   # provider 是否已配 api_key
    selected: bool    # 是否在候选池（显式勾选）


class RoutingPoolResponse(BaseModel):
    enabled: bool
    mode: str
    configured: bool          # 是否已显式配置候选池（false = 回落 available）
    models: list[RoutingModel]


class RoutingPoolUpdate(BaseModel):
    model_ids: list[str]


def _build_response() -> RoutingPoolResponse:
    configured = model_router.get_pool_config()
    configured_set = set(configured)
    models: list[RoutingModel] = []
    for mid, m in _cfg.MODEL_CONFIGS.items():
        prov = _cfg.PROVIDER_CONFIGS.get(m.provider)
        has_key = bool(prov and prov.api_key)
        models.append(RoutingModel(
            model_id=mid,
            label=m.label or mid,
            provider=m.provider,
            provider_label=(prov.label if prov else m.provider) or m.provider,
            tier=m.tier,
            available=has_key,
            selected=(mid in configured_set),
        ))
    return RoutingPoolResponse(
        enabled=_cfg.MODEL_ROUTING_ENABLED,
        mode=_cfg.MODEL_ROUTING_MODE,
        configured=bool(configured),
        models=models,
    )


@router.get("/pool", response_model=RoutingPoolResponse)
def get_pool(_: dict = Depends(get_current_user)) -> RoutingPoolResponse:
    """候选池视图：全部模型 + 是否可用 + 是否已勾选；登录可读（写仅 admin）。"""
    return _build_response()


@router.put("/pool", response_model=RoutingPoolResponse)
def set_pool(req: RoutingPoolUpdate, _: dict = Depends(require_admin)) -> RoutingPoolResponse:
    """保存候选池（仅 admin）；只接受已知模型 id。"""
    model_router.set_pool_config(req.model_ids)
    return _build_response()
