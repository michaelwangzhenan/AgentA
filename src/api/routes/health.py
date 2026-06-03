"""健康检查端点 —— 验证 API 层活着"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"ok": True, "version": "0.1.0"}
