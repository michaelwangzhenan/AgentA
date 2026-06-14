"""Knowledge Base 端点 —— 文档列表 / 拖拽上传 + ingest / 删除文档

上传走 multipart/form-data；后端落盘到
`config.WEB_UPLOAD_DIR` 后调用 `ingest_all` 复用既有幂等增量入库链路。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

import src.config as config
from src.api.deps import get_current_user
from src.api.schemas.kb import (
    KBClearAllResponse,
    KBCollection,
    KBCollectionListResponse,
    KBDeleteResponse,
    KBDocument,
    KBDocumentListResponse,
)
from src.rag.ingest import (
    count_kb_documents,
    delete_all_kb_documents,
    delete_kb_document,
    ingest_one,
    list_kb_documents,
)
from src.rag.parser import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

router = APIRouter()

# 持有后台 golden 生成任务的强引用，避免 asyncio 在任务跑完前把它当垃圾回收
# （fire-and-forget 任务若无引用可能被 GC，见 asyncio.create_task 文档警示）。
_bg_tasks: set = set()


def _validate_alias(model: str) -> str:
    """校验 embedding 别名必须是已定义的（en/zh/m3），否则 400。"""
    if model not in config.EMBEDDING_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"未知库别名 {model!r}；可选：{', '.join(config.EMBEDDING_MODELS)}",
        )
    return model


def _alias_upload_root(model: str) -> Path:
    """每个库独立的上传子目录：web_uploads/<alias>/。各库文件隔离，清空互不影响。"""
    return (Path(config.WEB_UPLOAD_DIR).resolve() / model)


def _safe_rel_target(upload_root: Path, relpath: str, filename: str) -> Path:
    """把客户端给的相对路径（可能含子目录）安全地落到 upload_root 下，防 ../ 穿越。

    relpath 为空或非法时回退到 filename 的 basename。
    """
    raw = (relpath or "").strip().replace("\\", "/")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        parts = [Path(filename).name]
    target = upload_root.joinpath(*parts).resolve()
    root = upload_root.resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="非法文件路径")
    return target


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


@router.get("/kb/collections", response_model=KBCollectionListResponse)
def list_collections(
    refresh: bool = Query(False, description="true 则跳过进程内缓存重新统计"),
    _: dict = Depends(get_current_user),
) -> KBCollectionListResponse:
    """列出全部已定义的库（L1）：每个 embedding 别名 + 模型 + 文档数 + chunk 数。

    文档 / chunk 数有进程内缓存（写操作自动失效）；refresh=true 强制重算。
    高亮标识来自 is_default（= .env 配置的默认入库别名）。
    """
    items: list[KBCollection] = []
    for alias, (model_name, collection_name) in config.EMBEDDING_MODELS.items():
        doc_count, chunk_count = count_kb_documents(model=alias, use_cache=not refresh)
        items.append(KBCollection(
            alias=alias,
            model=model_name,
            collection=collection_name,
            doc_count=doc_count,
            chunk_count=chunk_count,
            is_default=alias == config.DEFAULT_EMBEDDING_ALIAS,
        ))
    return KBCollectionListResponse(collections=items)


@router.get("/kb/documents", response_model=KBDocumentListResponse)
def list_documents(
    model: str = Query(config.DEFAULT_EMBEDDING_ALIAS, description="库别名 en/zh/m3"),
    _: dict = Depends(get_current_user),
) -> KBDocumentListResponse:
    """列出指定库内已入库的所有文档（按上传时间倒序）。"""
    _validate_alias(model)
    docs = list_kb_documents(model=model)
    return KBDocumentListResponse(documents=[_md_to_kbdoc(d) for d in docs])


def _done_message(status: str, chunks: int) -> str:
    """根据入库结果给出人类友好提示。"""
    if status == "empty":
        return "解析失败或内容为空，未入库"
    if status == "skipped_unchanged":
        return f"内容未变化，跳过重新 embedding（{chunks} chunks）"
    return f"已入库 {chunks} 个 chunks"


def _trigger_golden(target_path: Path, safe_name: str, doc_id: str) -> None:
    """入库成功后台调 LLM 自动生成 golden 候选（不感知、软失败、不阻塞）。"""
    from src.rag.golden_gen import run_generation_for_file

    task = asyncio.create_task(
        asyncio.to_thread(
            run_generation_for_file,
            file_path=str(target_path),
            source=safe_name,
            doc_id=doc_id,
        )
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _ingest_event_stream(target_path: Path, upload_root: Path, model: str, safe_name: str):
    """把同步 ingest 的进度桥接成 SSE 事件流。

    progress_cb 在工作线程里被调用，用 call_soon_threadsafe 把事件投进 asyncio.Queue；
    生成器侧逐条取出转成 `data: {...}` 行。最终发 done / error 后收尾。
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def cb(phase: str, done: int, total: int) -> None:
        loop.call_soon_threadsafe(
            q.put_nowait,
            {"type": "progress", "phase": phase, "done": done, "total": total},
        )

    async def run() -> None:
        try:
            result = await asyncio.to_thread(
                ingest_one,
                file_path=target_path,
                docs_root=upload_root,
                model=model,
                progress_cb=cb,
            )
            status, chunks, doc_id = result["status"], result["chunks"], result["doc_id"]
            if status == "ingested" and config.EVAL_AUTO_GOLDEN_ENABLED:
                _trigger_golden(target_path, safe_name, doc_id)
            q.put_nowait({
                "type": "done",
                "doc_id": doc_id,
                "filename": safe_name,
                "chunks": chunks,
                "skipped_unchanged": status == "skipped_unchanged",
                "status": status,
                "message": _done_message(status, chunks),
            })
        except Exception as exc:  # noqa: BLE001 — 任何入库异常都作为 error 事件回传
            logger.exception("[KB] ingest 失败: %s", safe_name)
            q.put_nowait({"type": "error", "message": str(exc)})

    task = asyncio.create_task(run())
    try:
        while True:
            ev = await q.get()
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev["type"] in ("done", "error"):
                break
    finally:
        await task


@router.post("/kb/upload")
async def upload_document(
    file: UploadFile = File(...),
    model: str = Form(config.DEFAULT_EMBEDDING_ALIAS),
    relpath: str = Form(""),
    _: dict = Depends(get_current_user),
) -> StreamingResponse:
    """上传一个文件并以 SSE 流式回传入库进度 + 最终结果。

    落盘到 `web_uploads/<alias>/<relpath>`：按库分子目录隔离，relpath 保留文件夹
    上传时的相对路径，避免不同文件夹同名文件互相覆盖。relpath 为空则用文件名。

    校验（失败走普通 HTTP 错误）：
      - 库别名必须是已定义的（en/zh/m3），否则 400
      - 扩展名必须在 `SUPPORTED_EXTENSIONS`，否则 415
      - 文件大小 ≤ `config.WEB_MAX_UPLOAD_MB` MB，否则 413
    校验通过后返回 text/event-stream：
      - `{type:"progress", phase, done, total}`：parse / split / embed 阶段进度
      - `{type:"done", doc_id, chunks, status, skipped_unchanged, message}`
      - `{type:"error", message}`
    """
    _validate_alias(model)
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

    # 落盘到 web_uploads/<alias>/<relpath>（按库隔离 + 保留相对路径防同名互覆盖）
    upload_root = _alias_upload_root(model)
    target_path = _safe_rel_target(upload_root, relpath, file.filename)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(content)
    rel_name = target_path.relative_to(upload_root).as_posix()
    logger.info("[KB] 文件落盘: %s (%d bytes)", target_path, len(content))

    # 单文件入库走 SSE 流：不再设硬超时（ingest 跑在 thread，进度实时回传，
    # 大文件也能看到逐块进度，不会再出现"超时误报但其实入库成功"）。
    return StreamingResponse(
        _ingest_event_stream(target_path, upload_root, model, rel_name),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/kb/documents/{doc_id}", response_model=KBDeleteResponse)
def delete_document(
    doc_id: str,
    model: str = Query(config.DEFAULT_EMBEDDING_ALIAS, description="库别名 en/zh/m3"),
    _: dict = Depends(get_current_user),
) -> KBDeleteResponse:
    """从指定库删除单文档（Chroma + BM25 + web_uploads 物理文件，一并清）。

    幂等：doc_id 不存在返回 200 + deleted=False。
    """
    _validate_alias(model)
    found, chunks_removed = delete_kb_document(
        doc_id=doc_id, model=model, web_upload_dir=str(_alias_upload_root(model))
    )
    return KBDeleteResponse(deleted=found, chunks_removed=chunks_removed)


@router.delete("/kb/documents", response_model=KBClearAllResponse)
def clear_all_documents(
    model: str = Query(config.DEFAULT_EMBEDDING_ALIAS, description="库别名 en/zh/m3"),
    _: dict = Depends(get_current_user),
) -> KBClearAllResponse:
    """清空指定库（Chroma collection + BM25 + web_uploads 物理文件）。

    破坏性操作；前端应在调用前做二次确认。幂等：空 KB 调用返回全 0。
    """
    _validate_alias(model)
    result = delete_all_kb_documents(
        model=model, web_upload_dir=str(_alias_upload_root(model))
    )
    return KBClearAllResponse(
        docs_removed=result["docs_removed"],
        chunks_removed=result["chunks_removed"],
        files_removed=result["files_removed"],
    )
