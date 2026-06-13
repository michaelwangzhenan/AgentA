#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时数据备份 / 还原的管理员 API。

备份 / 还原逻辑全部委托 src.runtime_backup（与 tools/backup.py CLI 共用）；本文件只负责
HTTP 封装、文件名校验与上传还原的安全把关。全部依赖 require_admin。

安全要点：
- 下载 / 删除的 {name} 强校验为 agenta-backup-<时间戳>.zip，禁路径分隔符 / .. 防穿越；
- 还原先用 runtime_backup.validate_restore_targets 校验 manifest 目标，拒绝逃出项目根的路径。
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

import src.config as config
import src.runtime_backup as rb
from src.api.deps import require_admin
from src.api.schemas.backup import (
    BackupListResponse,
    BackupSnapshot,
    CreateBackupRequest,
    RestoreResponse,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_NAME_RE = re.compile(r"^agenta-backup-\d{8}-\d{6}\.zip$")

router = APIRouter(prefix="/admin/backup", tags=["backup"], dependencies=[Depends(require_admin)])


def _backup_dir() -> Path:
    """当前 BACKUP_DIR 解析为绝对路径（相对项目根）。"""
    p = Path(config.BACKUP_DIR)
    return p if p.is_absolute() else (_PROJECT_ROOT / p)


def _safe_zip_path(name: str) -> Path:
    """校验文件名格式并返回备份目录下的安全路径，防路径穿越。"""
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="非法备份文件名")
    return _backup_dir() / name


def _to_snapshot(s: dict) -> BackupSnapshot:
    return BackupSnapshot(
        name=s["name"],
        timestamp=s["timestamp"],
        created_at=s.get("created_at", ""),
        include_vectors=s.get("include_vectors"),
        file_count=s["file_count"],
        zip_bytes=s["zip_bytes"],
        category_stats=s.get("category_stats", {}),
    )


@router.post("/create", response_model=BackupSnapshot)
def create_backup(req: CreateBackupRequest) -> BackupSnapshot:
    zip_path = rb.make_backup(_backup_dir(), skip_vectors=req.skip_vectors)
    for s in rb.list_snapshots(_backup_dir()):
        if s["name"] == zip_path.name:
            return _to_snapshot(s)
    raise HTTPException(status_code=500, detail="备份生成异常")


@router.get("/list", response_model=BackupListResponse)
def list_backups() -> BackupListResponse:
    return BackupListResponse(items=[_to_snapshot(s) for s in rb.list_snapshots(_backup_dir())])


@router.get("/download/{name}")
def download_backup(name: str) -> FileResponse:
    p = _safe_zip_path(name)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="备份不存在")
    return FileResponse(p, filename=name, media_type="application/zip")


@router.delete("/{name}")
def delete_backup(name: str) -> dict:
    p = _safe_zip_path(name)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="备份不存在")
    p.unlink()
    return {"ok": True}


@router.post("/restore", response_model=RestoreResponse)
async def restore_backup(file: UploadFile = File(...)) -> RestoreResponse:
    if not (file.filename or "").endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 备份文件")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        try:
            manifest = rb.read_manifest(tmp_path)
        except Exception as e:  # noqa: BLE001 —— 任何解析失败都视为无效备份
            raise HTTPException(status_code=400, detail="无效备份：无法读取 manifest") from e

        bad = rb.validate_restore_targets(manifest, _PROJECT_ROOT)
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"备份含项目根外的不安全路径，已拒绝：{bad[:5]}",
            )

        n = rb.restore_backup(tmp_path, _PROJECT_ROOT, manifest)
    finally:
        tmp_path.unlink(missing_ok=True)

    logger.info("[backup] 还原 %d 个文件（来自上传 %s）", n, file.filename)
    return RestoreResponse(
        restored=n, message="还原完成，建议重启后端以加载新数据（已打开的 DB 连接仍指向旧文件）。"
    )
