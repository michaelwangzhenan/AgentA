"""
每用户偏好 rules 端点：读写 auth.db.user_rules，Agent 每轮对话动态注入 system prompt。

- GET /api/rules：读当前用户 rules
- PUT /api/rules：写当前用户 rules（下一轮对话即生效）
"""

from fastapi import APIRouter, Depends, HTTPException

import src.config as cfg
from src.api.deps import get_current_user, get_user_store
from src.api.permissions import require_write
from src.api.schemas.rules import RulesReadResponse, RulesWriteRequest, RulesWriteResponse
from src.stores.user_store import UserStore

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=RulesReadResponse)
def read_rules(
    store: UserStore = Depends(get_user_store),
    user: dict = Depends(get_current_user),
) -> RulesReadResponse:
    return RulesReadResponse(text=store.get_rules(user["id"]))


@router.put("", response_model=RulesWriteResponse)
def write_rules(
    req: RulesWriteRequest,
    store: UserStore = Depends(get_user_store),
    user: dict = Depends(require_write("memory")),
) -> RulesWriteResponse:
    if len(req.text) > cfg.USER_RULES_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"rules 超出 {cfg.USER_RULES_MAX_CHARS} 字符上限（当前 {len(req.text)}）",
        )
    store.set_rules(user["id"], req.text)
    return RulesWriteResponse(length=len(req.text))
