#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行时数据备份与还原端点，仅 admin；逻辑委托 src.services.runtime_backup（与 backup_cli 共用）。

- POST /api/admin/backup/create：按类别打包生成备份 zip
- GET /api/admin/backup/list：列出已有备份
- GET /api/admin/backup/download/{name}：下载备份（name 强校验防路径穿越）
- DELETE /api/admin/backup/{name}：删除备份文件
- POST /api/admin/backup/restore：上传 zip 还原（先校验 manifest 目标不逃出项目根）
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

import src.config as config
import src.services.runtime_backup as rb
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
    cats = set(req.categories)
    invalid = cats - set(rb.ALL_CATEGORIES)
    if invalid:
        raise HTTPException(status_code=400, detail=f"非法备份类别：{sorted(invalid)}")
    if not cats:
        raise HTTPException(status_code=400, detail="至少选择一个备份类别")
    zip_path = rb.make_backup(_backup_dir(), categories=cats)
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

    max_upload = config.BACKUP_MAX_UPLOAD_MB * 1024 * 1024
    oversize = False
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_upload:
                oversize = True
                break
            tmp.write(chunk)

    if oversize:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"备份文件过大（上限 {config.BACKUP_MAX_UPLOAD_MB} MiB）",
        )

    try:
        try:
            rb.validate_backup_archive(tmp_path)
        except rb.BackupArchiveError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

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
