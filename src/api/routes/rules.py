"""每用户偏好 rules 读写端点。

rules 按用户独享，存 `auth.db.user_rules` 表；Agent 每轮对话动态读取当前用户的
rules 注入 system prompt，改完下一轮即生效，无需重启。
"""

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user, get_user_store
from src.api.schemas.rules import RulesReadResponse, RulesWriteRequest, RulesWriteResponse
from src.memory.user_store import UserStore

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
    user: dict = Depends(get_current_user),
) -> RulesWriteResponse:
    store.set_rules(user["id"], req.text)
    return RulesWriteResponse(length=len(req.text))
