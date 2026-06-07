"""Knowledge Base 端点 —— 文档列表 / 拖拽上传 + ingest / 删除文档

设计参考 docs/iter_4_UI.md §6.4.5。上传走 multipart/form-data；后端落盘到
`config.WEB_UPLOAD_DIR` 后调用 `ingest_all` 复用既有幂等增量入库链路。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

import src.config as config
from src.api.deps import get_current_user
from src.api.schemas.kb import (
    KBClearAllResponse,
    KBDeleteResponse,
    KBDocument,
    KBDocumentListResponse,
    KBUploadResponse,
)
from src.rag.ingest import (
    delete_all_kb_documents,
    delete_kb_document,
    ingest_one,
    list_kb_documents,
)
from src.rag.parser import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

router = APIRouter()


def _md_to_kbdoc(md: dict) -> KBDocument:
    """`list_kb_documents` 返回的 dict → KBDocument"""
    return KBDocument(
        doc_id=md["doc_id"],
        filename=md.get("filename", ""),
        source=md.get("source", ""),
        ext=md.get("ext", ""),
        lang=md.get("lang", ""),
        mtime=float(md.get("mtime", 0.0)),
        ingested_at=float(md.get("ingested_at", 0.0)),
        chunks=int(md.get("chunks", 0)),
        total_chars=int(md.get("total_chars", 0)),
    )


@router.get("/kb/documents", response_model=KBDocumentListResponse)
def list_documents(_: dict = Depends(get_current_user)) -> KBDocumentListResponse:
    """列出默认 collection 内已入库的所有文档（按上传时间倒序）。"""
    docs = list_kb_documents(model=config.DEFAULT_EMBEDDING_ALIAS)
    return KBDocumentListResponse(documents=[_md_to_kbdoc(d) for d in docs])


@router.post("/kb/upload", response_model=KBUploadResponse)
async def upload_document(
    file: UploadFile = File(...), _: dict = Depends(get_current_user)
) -> KBUploadResponse:
    """上传一个文件 + 同步 ingest 到默认 collection。

    校验：
      - 扩展名必须在 `SUPPORTED_EXTENSIONS`，否则 415
      - 文件大小 ≤ `config.WEB_MAX_UPLOAD_MB` MB，否则 413
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="filename 不能为空")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的格式 {suffix}；支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # 边读边算大小，超限立即拒（避免把整个大文件读进内存）
    max_bytes = config.WEB_MAX_UPLOAD_MB * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content) / 1024 / 1024:.1f} MB > {config.WEB_MAX_UPLOAD_MB} MB）",
        )
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="文件为空")

    # 落盘到 web_uploads/<filename>
    upload_root = Path(config.WEB_UPLOAD_DIR).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)

    # 安全检查：filename 不能含路径分隔符（防 ../ 跳出）
    safe_name = Path(file.filename).name
    target_path = upload_root / safe_name

    target_path.write_bytes(content)
    logger.info("[KB] 文件落盘: %s (%d bytes)", target_path, len(content))

    # 单文件入库：只处理这一个文件，不扫整个 web_uploads 目录
    # （旧版用 ingest_all 会 re-parse 同目录所有文件，目录里若有大 PDF/docx 会拖慢小文件上传）
    # 用 to_thread 让同步 ingest 跑在 thread pool，避免阻塞 event loop；
    # 用 wait_for 设超时，超时返回 504 让前端解套（注意：后台 thread 仍会跑完，
    # 同步代码无法真正取消 —— 超时仅为"客户端别等"，主要价值是防止 deadlock 拖垮整个后端）
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                ingest_one,
                file_path=target_path,
                docs_root=upload_root,
                model=config.DEFAULT_EMBEDDING_ALIAS,
            ),
            timeout=config.WEB_INGEST_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as exc:
        logger.error(
            "[KB] ingest 超时 (%ds): %s — 后台 thread 仍在跑，但 client 不再等待",
            config.WEB_INGEST_TIMEOUT_SEC, safe_name,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"入库超时（{config.WEB_INGEST_TIMEOUT_SEC}s 内未完成）。"
                "可能是文件过大或后端繁忙；可稍后重传，或调大 WEB_INGEST_TIMEOUT_SEC"
            ),
        ) from exc
    except Exception as exc:
        logger.exception("[KB] ingest 失败: %s", safe_name)
        raise HTTPException(status_code=500, detail=f"ingest error: {exc}") from exc

    status = result["status"]
    chunks = result["chunks"]
    doc_id = result["doc_id"]

    if status == "empty":
        return KBUploadResponse(
            doc_id=doc_id,
            filename=safe_name,
            chunks=0,
            skipped_unchanged=False,
            message="解析失败或内容为空，未入库",
        )

    skipped = status == "skipped_unchanged"
    return KBUploadResponse(
        doc_id=doc_id,
        filename=safe_name,
        chunks=chunks,
        skipped_unchanged=skipped,
        message=(
            f"内容未变化，跳过重新 embedding（{chunks} chunks）"
            if skipped
            else f"已入库 {chunks} 个 chunks"
        ),
    )


@router.delete("/kb/documents/{doc_id}", response_model=KBDeleteResponse)
def delete_document(doc_id: str, _: dict = Depends(get_current_user)) -> KBDeleteResponse:
    """删除单文档（Chroma + BM25 + web_uploads 物理文件，一并清）。

    幂等：doc_id 不存在返回 200 + deleted=False。
    """
    found, chunks_removed = delete_kb_document(
        doc_id=doc_id,
        model=config.DEFAULT_EMBEDDING_ALIAS,
    )
    return KBDeleteResponse(deleted=found, chunks_removed=chunks_removed)


@router.delete("/kb/documents", response_model=KBClearAllResponse)
def clear_all_documents(_: dict = Depends(get_current_user)) -> KBClearAllResponse:
    """清空整个 KB（Chroma collection + BM25 + web_uploads 物理文件）。

    破坏性操作；前端应在调用前做二次确认。幂等：空 KB 调用返回全 0。
    """
    result = delete_all_kb_documents(model=config.DEFAULT_EMBEDDING_ALIAS)
    return KBClearAllResponse(
        docs_removed=result["docs_removed"],
        chunks_removed=result["chunks_removed"],
        files_removed=result["files_removed"],
    )
