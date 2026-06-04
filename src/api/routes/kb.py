"""Knowledge Base 端点 —— 文档列表 / 拖拽上传 + ingest / 删除文档

设计参考 docs/iter_4_UI.md §6.4.5。上传走 multipart/form-data；后端落盘到
`config.WEB_UPLOAD_DIR` 后调用 `ingest_all` 复用既有幂等增量入库链路。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

import src.config as config
from src.api.schemas.kb import (
    KBDeleteResponse,
    KBDocument,
    KBDocumentListResponse,
    KBUploadResponse,
)
from src.rag.ingest import (
    _content_sha1,
    _doc_id_from_relpath,
    delete_kb_document,
    ingest_all,
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
        chunks=int(md.get("chunks", 0)),
        total_chars=int(md.get("total_chars", 0)),
    )


@router.get("/kb/documents", response_model=KBDocumentListResponse)
def list_documents() -> KBDocumentListResponse:
    """列出默认 collection 内已入库的所有文档（按上传时间倒序）。"""
    docs = list_kb_documents(model=config.DEFAULT_EMBEDDING_ALIAS)
    return KBDocumentListResponse(documents=[_md_to_kbdoc(d) for d in docs])


@router.post("/kb/upload", response_model=KBUploadResponse)
async def upload_document(file: UploadFile = File(...)) -> KBUploadResponse:
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

    # 预先算 doc_id + content_sha1，方便上层判断 "是否跳过未变"
    rel_path = safe_name
    doc_id = _doc_id_from_relpath(rel_path)

    # 调用 ingest_all 扫描整个 web_uploads 目录（幂等：其他文件 content_sha1 没变会跳过）
    try:
        ingest_all(docs_dir=str(upload_root), model=config.DEFAULT_EMBEDDING_ALIAS)
    except Exception as exc:
        logger.exception("[KB] ingest 失败: %s", safe_name)
        raise HTTPException(status_code=500, detail=f"ingest error: {exc}") from exc

    # 重新查 chunks 数 + 判断跳过/新增
    docs = list_kb_documents(model=config.DEFAULT_EMBEDDING_ALIAS)
    this_doc = next((d for d in docs if d["doc_id"] == doc_id), None)

    if this_doc is None:
        # ingest 后还查不到：parse 阶段产出空 → ingest_all 跳过了
        return KBUploadResponse(
            doc_id=doc_id,
            filename=safe_name,
            chunks=0,
            skipped_unchanged=False,
            message="解析失败或内容为空，未入库",
        )

    return KBUploadResponse(
        doc_id=doc_id,
        filename=safe_name,
        chunks=this_doc["chunks"],
        skipped_unchanged=False,  # ingest_all 内部决定；这里前端不区分（chunks 数对得上就行）
        message=f"已入库 {this_doc['chunks']} 个 chunks",
    )


@router.delete("/kb/documents/{doc_id}", response_model=KBDeleteResponse)
def delete_document(doc_id: str) -> KBDeleteResponse:
    """删除单文档（Chroma + BM25 + web_uploads 物理文件，一并清）。

    幂等：doc_id 不存在返回 200 + deleted=False。
    """
    found, chunks_removed = delete_kb_document(
        doc_id=doc_id,
        model=config.DEFAULT_EMBEDDING_ALIAS,
    )
    return KBDeleteResponse(deleted=found, chunks_removed=chunks_removed)
