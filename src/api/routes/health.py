"""
健康检查端点：验证 API 层存活。

- GET /api/health：返回 ok + version
"""

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "version": "0.1.0"}
