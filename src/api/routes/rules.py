"""项目 rules 读写端点。

路径取 `config.USER_RULES_FILE`（默认 `.agenta/rules.md`）；
进程内 Agent 已缓存 rules 文本，写完需要重启或新 session 才能生效。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status

import src.config as _cfg
from src.api.schemas.rules import RulesReadResponse, RulesWriteRequest, RulesWriteResponse

router = APIRouter(prefix="/rules", tags=["rules"])


def _resolve_path() -> Path:
    return (Path.cwd() / _cfg.USER_RULES_FILE).resolve()


@router.get("", response_model=RulesReadResponse)
def read_rules() -> RulesReadResponse:
    path = _resolve_path()
    if not path.exists() or not path.is_file():
        return RulesReadResponse(text="", path=str(path), exists=False)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取 rules 失败: {exc}",
        ) from exc
    return RulesReadResponse(text=text, path=str(path), exists=True)


@router.put("", response_model=RulesWriteResponse)
def write_rules(req: RulesWriteRequest) -> RulesWriteResponse:
    path = _resolve_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(req.text, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"写入 rules 失败: {exc}",
        ) from exc
    return RulesWriteResponse(path=str(path), length=len(req.text), restart_required=True)
