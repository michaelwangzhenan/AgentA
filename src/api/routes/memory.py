"""
User Memory 端点：跨 session 用户长期记忆的增删改查。

- GET /api/memory：列出当前用户全部记忆
- POST /api/memory：新增一条记忆
- PATCH /api/memory/{memory_id}：修改一条记忆
- DELETE /api/memory/{memory_id}：删除一条记忆
- DELETE /api/memory：清空当前用户全部记忆
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user, get_user_memory_store
from src.api.permissions import require_write
from src.api.schemas.memory import (
    MemoryClearResponse,
    MemoryCreateRequest,
    MemoryDeleteResponse,
    MemoryItem,
    MemoryListResponse,
    MemoryPatchRequest,
    MemoryPatchResponse,
)
from src.stores.user_memory import UserMemoryStore

router = APIRouter(prefix="/memory", tags=["memory"])


def _require_store(
    store: UserMemoryStore | None = Depends(get_user_memory_store),
) -> UserMemoryStore:
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="USER_MEMORY_ENABLED=false；请在 .env 启用后重启 uvicorn",
        )
    return store


@router.get("", response_model=MemoryListResponse)
def list_memories(
    store: UserMemoryStore = Depends(_require_store),
    user: dict = Depends(get_current_user),
) -> MemoryListResponse:
    rows = store.load_all(user_id=user["id"])
    return MemoryListResponse(memories=[MemoryItem(**row) for row in rows])


@router.post("", response_model=MemoryItem)
def create_memory(
    req: MemoryCreateRequest,
    store: UserMemoryStore = Depends(_require_store),
    user: dict = Depends(require_write("memory")),
) -> MemoryItem:
    new_id = store.add(req.text, source=req.source, user_id=user["id"])
    if new_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="text 清洗后为空，未写入",
        )
    for row in store.load_all(user_id=user["id"]):
        if row["id"] == new_id:
            return MemoryItem(**row)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"新增成功 (id={new_id}) 但查不到该行（可能被其他进程并发删除）",
    )


@router.patch("/{memory_id}", response_model=MemoryPatchResponse)
def patch_memory(
    memory_id: int,
    req: MemoryPatchRequest,
    store: UserMemoryStore = Depends(_require_store),
    user: dict = Depends(require_write("memory")),
) -> MemoryPatchResponse:
    updated = store.update_text(memory_id, req.text, user_id=user["id"])
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"memory id={memory_id} 不存在或更新失败（可能 text 清洗后为空）",
        )
    return MemoryPatchResponse(updated=True)


@router.delete("/{memory_id}", response_model=MemoryDeleteResponse)
def delete_memory(
    memory_id: int,
    store: UserMemoryStore = Depends(_require_store),
    user: dict = Depends(require_write("memory")),
) -> MemoryDeleteResponse:
    return MemoryDeleteResponse(deleted=store.delete(memory_id, user_id=user["id"]))


@router.delete("", response_model=MemoryClearResponse)
def clear_memories(
    store: UserMemoryStore = Depends(_require_store),
    user: dict = Depends(require_write("memory")),
) -> MemoryClearResponse:
    return MemoryClearResponse(cleared=store.clear(user_id=user["id"]))
