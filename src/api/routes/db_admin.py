#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB 秀：只读巡检 Chroma / SQLite / BM25 的管理员 API。

读逻辑全部委托 src.db_inspect（与 tools/db_show.py CLI 共用）；本文件只负责
HTTP 封装与 404 处理。全部 GET、只读，依赖 require_admin。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

import src.db_inspect as inspect
from src.api.deps import require_admin

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
) -> dict:
    data = inspect.sqlite_table_rows(db_key, table, limit=limit, offset=offset)
    if data is None:
        raise HTTPException(status_code=404, detail=f"库不存在: {db_key}")
    if data.get("error"):
        raise HTTPException(status_code=404, detail=data["error"])
    return data
