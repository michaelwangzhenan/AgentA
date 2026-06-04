"""Skills 只读列表端点。

复用 `src.cli.skill_loader.scan_skills`，返回当前 `.agenta/skills/` 扫描结果。
UI 不提供添加 / 删除（用户改文件后重启进程即可 reload）。
"""

from fastapi import APIRouter

from src.api.schemas.skills import SkillFailure, SkillItem, SkillsResponse
from src.cli.skill_loader import scan_skills

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=SkillsResponse)
def list_skills() -> SkillsResponse:
    result = scan_skills()
    loaded = [
        SkillItem(name=s.name, description=s.description, location=str(s.location))
        for s in result.loaded.values()
    ]
    failed = [SkillFailure(path=str(f.path), reason=f.reason) for f in result.failed]
    return SkillsResponse(loaded=loaded, failed=failed)
