#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库巡检与维护端点，仅 admin；读逻辑委托 src.services.db_inspect / db_maintain（与 db_cli 共用）。

- GET /api/admin/db/chroma/collections：列出 Chroma collection
- GET /api/admin/db/chroma/{name}/items：分页浏览 collection 条目
- GET /api/admin/db/chroma/{name}/items/{item_id}：单条 Chroma 条目详情
- GET /api/admin/db/bm25/indexes：列出 BM25 索引
- GET /api/admin/db/bm25/{collection}/docs：分页浏览 BM25 文档
- GET /api/admin/db/bm25/{collection}/docs/{doc_id}：单条 BM25 文档详情
- GET /api/admin/db/sqlite/databases：列出 SQLite 库与表
- GET /api/admin/db/sqlite/{db_key}/{table}：分页浏览 SQLite 表行
- GET /api/admin/db/maintenance/prune/preview：预览可裁剪数据
- POST /api/admin/db/maintenance/prune：执行裁剪
- GET /api/admin/db/maintenance/purge-user/preview：预览某用户可清理数据
- POST /api/admin/db/maintenance/purge-user：清理某用户数据
- POST /api/admin/db/maintenance/vacuum：SQLite VACUUM
- GET /api/admin/db/maintenance/orphan-segments/preview：预览孤儿向量段
- POST /api/admin/db/maintenance/orphan-segments：清理孤儿向量段
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

import src.services.db_inspect as inspect
import src.services.db_maintain as maintain
from src.api.deps import require_admin
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/db", tags=["db-admin"], dependencies=[Depends(require_admin)])


# ── Chroma ────────────────────────────────────────────────────────────────────

@router.get("/chroma/collections")
def chroma_collections() -> dict:
    return inspect.chroma_collections()


@router.get("/chroma/{name}/items")
def chroma_items(
    name: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    filename_q: str | None = Query(default=None),
    body_q: str | None = Query(default=None),
    ts_from: int | None = Query(default=None),
    ts_to: int | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    desc: bool = Query(default=False),
) -> dict:
    try:
        return inspect.chroma_items(
            name, limit=limit, offset=offset,
            filename_q=filename_q, body_q=body_q,
            ts_from=ts_from, ts_to=ts_to, sort_by=sort_by, desc=desc,
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"collection 不存在或不可读: {name}") from e


@router.get("/chroma/{name}/items/{item_id}")
def chroma_item(name: str, item_id: str) -> dict:
    try:
        item = inspect.chroma_item(name, item_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"collection 不存在或不可读: {name}") from e
    if item is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return item


# ── BM25 ──────────────────────────────────────────────────────────────────────

@router.get("/bm25/indexes")
def bm25_indexes() -> dict:
    return inspect.bm25_indexes()


@router.get("/bm25/{collection}/docs")
def bm25_docs(
    collection: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    filename_q: str | None = Query(default=None),
    body_q: str | None = Query(default=None),
    ts_from: int | None = Query(default=None),
    ts_to: int | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    desc: bool = Query(default=False),
) -> dict:
    data = inspect.bm25_docs(
        collection, limit=limit, offset=offset,
        filename_q=filename_q, body_q=body_q,
        ts_from=ts_from, ts_to=ts_to, sort_by=sort_by, desc=desc,
    )
    if data is None:
        raise HTTPException(status_code=404, detail=f"BM25 索引不存在: {collection}")
    return data


@router.get("/bm25/{collection}/docs/{doc_id}")
def bm25_doc(collection: str, doc_id: str) -> dict:
    data = inspect.bm25_doc(collection, doc_id)
    if data is None:
        raise HTTPException(status_code=404, detail="文档块不存在")
    return data


# ── SQLite ────────────────────────────────────────────────────────────────────

@router.get("/sqlite/databases")
def sqlite_databases() -> dict:
    return inspect.sqlite_databases()


@router.get("/sqlite/{db_key}/{table}")
def sqlite_table_rows(
    db_key: str,
    table: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: int | None = Query(default=None),
    time_col: str | None = Query(default=None),
    ts_from: int | None = Query(default=None),
    ts_to: int | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    desc: bool = Query(default=False),
) -> dict:
    data = inspect.sqlite_table_rows(
        db_key, table, limit=limit, offset=offset,
        user_id=user_id, time_col=time_col, ts_from=ts_from, ts_to=ts_to,
        sort_by=sort_by, desc=desc,
    )
    if data is None:
        raise HTTPException(status_code=404, detail=f"库不存在: {db_key}")
    if data.get("error"):
        raise HTTPException(status_code=404, detail=data["error"])
    return data


# ── 维护（破坏性，admin）────────────────────────────────────────────────────

class PruneRequest(BaseModel):
    days: int = Field(ge=1)


class PurgeSelection(BaseModel):
    db: str
    table: str
    all: bool = False
    rowids: list[int] = Field(default_factory=list)


class PurgeUserRequest(BaseModel):
    user_id: int
    selections: list[PurgeSelection] = Field(default_factory=list)


class VacuumRequest(BaseModel):
    db_key: str | None = None


@router.get("/maintenance/prune/preview")
def prune_preview(days: int = Query(ge=1)) -> dict:
    return maintain.prune_preview(days)


@router.post("/maintenance/prune")
def prune(req: PruneRequest) -> dict:
    return maintain.prune(req.days)


@router.get("/maintenance/purge-user/preview")
def purge_user_preview(user_id: int = Query(...)) -> dict:
    return maintain.purge_user_preview(user_id)


@router.post("/maintenance/purge-user")
def purge_user(req: PurgeUserRequest) -> dict:
    return maintain.purge_user(req.user_id, [s.model_dump() for s in req.selections])


@router.post("/maintenance/vacuum")
def vacuum(req: VacuumRequest) -> dict:
    return maintain.vacuum(req.db_key)


@router.get("/maintenance/orphan-segments/preview")
def orphan_segments_preview() -> dict:
    return maintain.orphan_segments_preview()


@router.post("/maintenance/orphan-segments")
def cleanup_orphan_segments() -> dict:
    return maintain.cleanup_orphan_segments()
