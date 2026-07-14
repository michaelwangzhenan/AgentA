"""
文档入库模块 —— 离线预处理阶段使用

执行完整的入库流程：扫描 datasets/ 目录 → 解析文本 → 分块 → 向量化 → 存入 ChromaDB。
支持重复运行（upsert），文档更新后重新运行即可，不会重复入库。
不同 embedding 模型使用独立的 ChromaDB collection，互不干扰。

使用方式（独立脚本入口，与 tools/cli/rag_cli.py ingest 等价的底层调用）：
    python -m src.rag.ingest                                              # 默认目录 + 默认模型
    python -m src.rag.ingest --model zh                                   # 使用中文模型
    python -m src.rag.ingest --docs-dir ./datasets/data_zh --model zh
    python -m src.rag.ingest -d ./datasets/data_en -m en

模型别名（详见 src/config.py EMBEDDING_MODELS）：
    en  →  all-MiniLM-L6-v2  （英文/多语言，collection: kb_en）
    zh  →  BAAI/bge-small-zh  （中文优化，  collection: kb_zh）
    m3  →  BAAI/bge-m3        （多语言单库，collection: kb_m3）
"""

# 必须在所有 huggingface/transformers 相关库 import 之前设置环境变量
# 因为这些库在 import 时就会读取 HF_ENDPOINT / TRANSFORMERS_OFFLINE
import os
from dotenv import load_dotenv
load_dotenv(override=True)
# 将 .env 中的 HF 相关配置提前注入 os.environ（load_dotenv 已完成，此处确认）
for _key in ("HF_ENDPOINT", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    _val = os.getenv(_key)
    if _val:
        os.environ[_key] = _val

import hashlib
import json
import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

# 分批 upsert 的批大小：把整文件的 N 块分批写入，便于在批与批之间上报"第 M/N 块"进度。
# 越小进度越细、嵌入调用次数越多；16 在体验与开销间折中。
_EMBED_BATCH_SIZE = 16

# 入库进度回调签名：cb(phase, done, total)
#   phase: "parse" 解析中 / "split" 切分完成 / "embed" 嵌入中
#   done/total: embed 阶段为已写入 / 总块数；parse 阶段为 0/0
ProgressCb = Callable[[str, int, int], None]

import chromadb

import src.config as config
from src.services.ingest_telemetry import IngestProbe, ingest_slot, probe_for_docx
from src.rag.parser import (
    SUPPORTED_EXTENSIONS,
    is_office_temp_file,
    parse_file,
    parsed_docx_temp,
)
from src.rag.splitter import iter_structured_lines, split_structured

logger = logging.getLogger(__name__)

# 入库时统一使用的相似度空间，与 retriever 必须保持一致。
# BGE / e5 / GTE / MiniLM 等 sentence-transformers 模型均按余弦相似度训练，
# 使用默认 squared L2 排序会与训练目标错位，命中率显著下降。
_HNSW_SPACE: str = "cosine"


def _doc_id_from_relpath(rel_path: str) -> str:
    """基于（POSIX 化的）相对路径生成稳定 doc_id（SHA1 前 16 位）。"""
    norm = rel_path.replace("\\", "/")
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _make_chunk_id(doc_id: str, chunk_index: int) -> str:
    """用 doc_id + 块序号生成稳定唯一 chunk ID。"""
    raw = f"{doc_id}::{chunk_index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _content_sha1(text: str) -> str:
    """计算正文内容 SHA1，用于幂等检测：内容未变则跳过 reembed。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _detect_lang(text: str) -> str:
    """
    极轻量启发式语言判断：按 CJK 字符占比阈值粗分 zh / en / mixed。
    不引入额外依赖；仅用于 metadata 标注以支持后续按语种过滤。
    """
    if not text:
        return "unknown"
    sample = text[:2000]
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    ratio = cjk / max(len(sample), 1)
    if ratio > 0.30:
        return "zh"
    if ratio > 0.05:
        return "mixed"
    return "en"


def _file_content_sha1(path: Path) -> str:
    """流式计算文本临时文件的内容哈希，保持 content_sha1 原有语义。"""
    digest = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ingest_docx_file(
    file_path: Path,
    rel_path: str,
    doc_id: str,
    collection,
    collection_name: str,
    progress_cb: ProgressCb | None,
    probe: IngestProbe,
    *,
    bm25_save: bool = True,
) -> dict:
    """隔离解析 DOCX，并按标题区段增量分块、分批写入。"""
    if progress_cb:
        progress_cb("parse", 0, 0)

    chunk_temp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="agenta-chunks-", suffix=".jsonl", delete=False
    )
    chunk_path = Path(chunk_temp.name)
    chunk_temp.close()
    existing_ids: list[str] = []
    total_chunks = 0
    heading_chunks = 0
    try:
        with parsed_docx_temp(file_path) as parsed_path:
            with probe.track("parse"):
                if parsed_path.stat().st_size == 0:
                    logger.warning("  跳过（内容为空）: %s", rel_path)
                    return {"doc_id": doc_id, "chunks": 0, "status": "empty"}

                content_hash = _file_content_sha1(parsed_path)
                with parsed_path.open("r", encoding="utf-8") as f:
                    lang = _detect_lang(f.read(2000))

                existing = collection.get(
                    where={"doc_id": doc_id},
                    include=["metadatas"],
                )
                existing_ids = existing.get("ids") or []
                existing_metas = existing.get("metadatas") or []
                if existing_ids and existing_metas:
                    prev_hash = existing_metas[0].get("content_sha1")
                    if prev_hash == content_hash:
                        logger.info(
                            "  跳过（内容未变化）: %s → %d 块", rel_path, len(existing_ids)
                        )
                        return {
                            "doc_id": doc_id,
                            "chunks": len(existing_ids),
                            "status": "skipped_unchanged",
                        }

            with probe.track("split"):
                with (
                    parsed_path.open("r", encoding="utf-8") as source,
                    chunk_path.open("w", encoding="utf-8") as spool,
                ):
                    for chunk in iter_structured_lines(
                        source, config.CHUNK_SIZE, config.CHUNK_OVERLAP
                    ):
                        record = {
                            "text": chunk.text,
                            "heading_path": chunk.heading_path,
                            "line_start": chunk.line_start,
                            "line_end": chunk.line_end,
                        }
                        spool.write(json.dumps(record, ensure_ascii=False) + "\n")
                        total_chunks += 1
                        if chunk.heading_path:
                            heading_chunks += 1

        if total_chunks == 0:
            logger.warning("  跳过（分块结果为空）: %s", rel_path)
            return {"doc_id": doc_id, "chunks": 0, "status": "empty"}

        if existing_ids:
            collection.delete(ids=existing_ids)
            logger.info("  清除旧数据: %s → 删除 %d 条", rel_path, len(existing_ids))

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        ingested_at = time.time()
        if progress_cb:
            progress_cb("split", 0, total_chunks)

        bm25 = None
        bm25_error: Exception | None = None
        if config.BM25_ENABLED:
            try:
                from src.rag.bm25_index import get_index

                bm25 = get_index(collection_name)
                bm25.delete_by_doc_id(doc_id)
            except Exception as exc:
                bm25_error = exc

        batch: list[dict] = []
        done = 0

        def flush_batch() -> None:
            nonlocal batch, done, bm25_error
            if not batch:
                return
            ids = [_make_chunk_id(doc_id, item["index"]) for item in batch]
            documents = [item["text"] for item in batch]
            metadatas: list[dict] = []
            for item in batch:
                md: dict = {
                    "doc_id": doc_id,
                    "source": rel_path,
                    "filename": file_path.name,
                    "ext": ".docx",
                    "lang": lang,
                    "mtime": mtime,
                    "ingested_at": ingested_at,
                    "content_sha1": content_hash,
                    "char_count": len(item["text"]),
                    "chunk_index": item["index"],
                    "chunk_total": total_chunks,
                    "line_start": item["line_start"],
                    "line_end": item["line_end"],
                }
                if item["heading_path"]:
                    md["heading_path"] = " > ".join(item["heading_path"])
                metadatas.append(md)
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
            if bm25 is not None and bm25_error is None:
                try:
                    bm25.upsert(ids=ids, documents=documents, metadatas=metadatas)
                except Exception as exc:
                    bm25_error = exc
            done += len(batch)
            if progress_cb:
                progress_cb("embed", done, total_chunks)
            batch = []

        with probe.track("embed"):
            with chunk_path.open("r", encoding="utf-8") as spool:
                for index, line in enumerate(spool):
                    item = json.loads(line)
                    item["index"] = index
                    batch.append(item)
                    if len(batch) >= _EMBED_BATCH_SIZE:
                        flush_batch()
                flush_batch()

        with probe.track("bm25"):
            if bm25_save and bm25 is not None and bm25_error is None:
                try:
                    from src.rag.bm25_index import commit_index

                    commit_index(collection_name)
                    logger.info("  BM25 索引已更新: %s → %d 块", rel_path, total_chunks)
                except Exception as exc:
                    bm25_error = exc
        if bm25_error is not None:
            logger.warning("  BM25 索引更新失败（已跳过）: %s — %s", rel_path, bm25_error)

        logger.info(
            "  入库: %s → %d 块 (lang=%s, headings=%d)",
            rel_path,
            total_chunks,
            lang,
            heading_chunks,
        )
        return {"doc_id": doc_id, "chunks": total_chunks, "status": "ingested"}
    finally:
        chunk_path.unlink(missing_ok=True)


def _open_collection(client, model_name: str, collection_name: str, use_api: bool | None = None):
    """
    获取（或创建）collection，并保证其 hnsw:space=cosine。

    use_api：入库来源（None=按全局；True/False=显式指定，见 `_get_embedding_fn`）。

    场景：
      - collection 不存在 → 直接创建，写入 cosine metadata。
      - collection 已存在但 metadata 不是 cosine（旧库用默认 L2 建的）→ 警告并 drop&recreate，
        因为 ingest 本来就要重写数据，这里顺便修正空间。用户保留的旧 chunks 会丢失，
        但下面会重新写入，整体语义不受损。
    """
    # 复用 retriever 的进程级缓存：同名模型只加载一次，ingest 与检索端共用同一实例
    from src.rag.retriever import _get_embedding_fn
    embedding_fn = _get_embedding_fn(model_name, use_api=use_api)

    existing = None
    try:
        existing = client.get_collection(
            name=collection_name,
            embedding_function=embedding_fn,  # type: ignore[arg-type]
        )
    except Exception:
        existing = None

    if existing is not None:
        meta = getattr(existing, "metadata", None) or {}
        space = meta.get("hnsw:space")
        if space == _HNSW_SPACE:
            return existing
        logger.warning(
            "collection '%s' 当前距离空间为 %r，与目标 %r 不一致；将重建以修正",
            collection_name, space, _HNSW_SPACE,
        )
        try:
            client.delete_collection(name=collection_name)
        except Exception as e:
            logger.error("删除旧 collection 失败，忽略并继续创建: %s", e)

    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,  # type: ignore[arg-type]
        metadata={"hnsw:space": _HNSW_SPACE},
    )


def _ingest_one_file(
    file_path: Path,
    docs_path: Path,
    collection,
    collection_name: str,
    progress_cb: ProgressCb | None = None,
    probe: IngestProbe | None = None,
    *,
    bm25_save: bool = True,
) -> dict:
    """处理单个文件入库；返回 {doc_id, chunks, status}。

    status:
      - 'ingested'          : 解析 + 分块 + upsert 成功
      - 'skipped_unchanged' : content_sha1 未变，跳过 parse 后续阶段
      - 'empty'             : 解析或分块结果为空

    progress_cb 可选：按 parse / split / embed 阶段回调上报进度（Web SSE 用）。

    被 `ingest_all`（批量）和 `ingest_one`（单文件公开入口）共用。
    """
    try:
        rel_path = file_path.resolve().relative_to(docs_path).as_posix()
    except ValueError:
        rel_path = file_path.name
    doc_id = _doc_id_from_relpath(rel_path)
    if probe is None:
        probe = probe_for_docx(file_path, rel_path)

    if file_path.suffix.lower() == ".docx":
        return _ingest_docx_file(
            file_path,
            rel_path,
            doc_id,
            collection,
            collection_name,
            progress_cb,
            probe,
            bm25_save=bm25_save,
        )

    if progress_cb:
        progress_cb("parse", 0, 0)
    with probe.track("parse"):
        text = parse_file(file_path)

    if not text.strip():
        logger.warning("  跳过（内容为空）: %s", rel_path)
        return {"doc_id": doc_id, "chunks": 0, "status": "empty"}

    content_hash = _content_sha1(text)

    # 幂等检测：若该 doc_id 已存在且 content_sha1 未变 → 跳过 reembed
    existing = collection.get(
        where={"doc_id": doc_id},
        include=["metadatas"],
    )
    existing_ids = existing.get("ids") or []
    existing_metas = existing.get("metadatas") or []
    if existing_ids and existing_metas:
        prev_hash = existing_metas[0].get("content_sha1")
        if prev_hash == content_hash:
            logger.info("  跳过（内容未变化）: %s → %d 块", rel_path, len(existing_ids))
            return {"doc_id": doc_id, "chunks": len(existing_ids), "status": "skipped_unchanged"}
        # 内容变了：先删旧 chunks
        collection.delete(ids=existing_ids)
        logger.info("  清除旧数据: %s → 删除 %d 条", rel_path, len(existing_ids))

    with probe.track("split"):
        structured = split_structured(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    if not structured:
        logger.warning("  跳过（分块结果为空）: %s", rel_path)
        return {"doc_id": doc_id, "chunks": 0, "status": "empty"}

    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    lang = _detect_lang(text)
    ext = file_path.suffix.lower()
    ingested_at = time.time()  # 整个文件的所有 chunks 共享一个入库时间

    ids: list[str] = [_make_chunk_id(doc_id, i) for i in range(len(structured))]
    documents: list[str] = [c.text for c in structured]
    metadatas: list[dict] = []
    for i, c in enumerate(structured):
        # ChromaDB metadata 不接受 None，缺失字段直接不写键
        md: dict = {
            "doc_id": doc_id,
            "source": rel_path,            # 完整相对路径（含子目录），不再用 filename 做去重键
            "filename": file_path.name,    # 兼容老字段，仅作展示
            "ext": ext,
            "lang": lang,
            "mtime": mtime,
            "ingested_at": ingested_at,
            "content_sha1": content_hash,
            "char_count": len(c.text),     # L2 列表聚合字数用，避免 collection.get(documents)
            "chunk_index": i,
            "chunk_total": len(structured),
            "line_start": int(c.line_start or 0),
            "line_end": int(c.line_end or 0),
        }
        if c.heading_path:
            # heading_path 用 " > " 拼接，便于在 LLM 工具结果里直接展示给用户
            md["heading_path"] = " > ".join(c.heading_path)
        if c.page_no is not None:
            md["page_no"] = int(c.page_no)
        metadatas.append(md)

    total_chunks = len(structured)
    if progress_cb:
        progress_cb("split", 0, total_chunks)

    with probe.track("embed"):
        for start in range(0, total_chunks, _EMBED_BATCH_SIZE):
            end = min(start + _EMBED_BATCH_SIZE, total_chunks)
            collection.upsert(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],  # type: ignore[arg-type]
            )
            if progress_cb:
                progress_cb("embed", end, total_chunks)

    with probe.track("bm25"):
        if config.BM25_ENABLED:
            try:
                from src.rag.bm25_index import commit_index, get_index

                bm25 = get_index(collection_name)
                bm25.delete_by_doc_id(doc_id)
                bm25.upsert(ids=ids, documents=documents, metadatas=metadatas)
                if bm25_save:
                    commit_index(collection_name)
                    logger.info("  BM25 索引已更新: %s → %d 块", rel_path, len(documents))
            except Exception as e:  # 失败不影响 dense 入库主流程
                logger.warning("  BM25 索引更新失败（已跳过）: %s — %s", rel_path, e)

    page_info = (
        f", pages={sum(1 for c in structured if c.page_no is not None)}"
        if any(c.page_no is not None for c in structured)
        else ""
    )
    heading_info = (
        f", headings={sum(1 for c in structured if c.heading_path)}"
        if any(c.heading_path for c in structured)
        else ""
    )
    logger.info(
        "  入库: %s → %d 块 (lang=%s%s%s)",
        rel_path, len(documents), lang, page_info, heading_info,
    )
    return {"doc_id": doc_id, "chunks": len(documents), "status": "ingested"}


def ingest_one(
    file_path: str | Path,
    docs_root: str | Path | None = None,
    model: str = config.DEFAULT_EMBEDDING_ALIAS,
    progress_cb: ProgressCb | None = None,
) -> dict:
    """单文件入库入口（Web 拖拽上传专用，不扫整个目录）。

    与 `ingest_all` 的关键差异：只处理传入的这一个文件，不会去 re-parse 同目录下
    其他文件。避免目录里有大文件时拖一个小文件也要全目录扫一遍的性能问题。

    Args:
        file_path: 待入库文件绝对/相对路径。
        docs_root: 用于计算 rel_path（doc_id 派生自 rel_path）。None 则用文件所在目录。
        model: embedding 别名（en / zh / m3）。
        progress_cb: 可选进度回调（parse / split / embed 阶段），Web SSE 用。

    Returns:
        dict: {doc_id, chunks, status}，status ∈ ingested / skipped_unchanged / empty
    """
    fp = Path(file_path).resolve()
    if not fp.exists():
        raise FileNotFoundError(f"文件不存在: {fp}")
    if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的格式: {fp.suffix}")
    if is_office_temp_file(fp):
        raise ValueError(f"跳过 Office 临时文件: {fp.name}")

    docs_path = Path(docs_root).resolve() if docs_root else fp.parent

    use_api = config.alias_is_api(model)
    model_name, collection_name = config.resolve_embedding(model)
    logger.info("Embedding 模型: %s  →  collection: %s (space=%s, 来源=%s)",
                model_name, collection_name, _HNSW_SPACE, "api" if use_api else "本地")

    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    collection = _open_collection(client, model_name, collection_name, use_api=use_api)
    try:
        rel_path = fp.relative_to(docs_path).as_posix()
    except ValueError:
        rel_path = fp.name
    probe = probe_for_docx(fp, rel_path)
    with ingest_slot():
        probe.file_start()
        try:
            result = _ingest_one_file(
                fp,
                docs_path,
                collection,
                collection_name,
                progress_cb,
                probe,
                bm25_save=False,
            )
            probe.file_done(result.get("status", "unknown"), int(result.get("chunks", 0)))
        except Exception as exc:
            probe.file_error(str(exc))
            raise
    if result.get("status") == "ingested":
        _commit_bm25_after_ingest(collection_name)
        _invalidate_semantic_cache()
        _invalidate_kb_stats(collection_name)
    return result


def _commit_bm25_after_ingest(collection_name: str) -> None:
    """入库结束后写盘 BM25 并释放进程内索引，避免大库长期占内存。"""
    if not config.BM25_ENABLED:
        return
    import gc

    from src.rag.bm25_index import commit_index
    from src.services.ingest_telemetry import (
        flush_log_handlers,
        process_rss_mb,
        system_avail_mb,
    )

    try:
        commit_index(collection_name, release=True)
        gc.collect()
        logger.info(
            "[ingest] BM25 已写盘并释放进程缓存 rss_mb=%d avail_mb=%d",
            process_rss_mb(),
            system_avail_mb(),
        )
        flush_log_handlers()
    except Exception as e:
        logger.warning("BM25 索引写入失败（已忽略）: %s", e)


def _invalidate_semantic_cache() -> None:
    """KB 变更后作废语义缓存；出错只记 log，不影响入库 / 删除主流程。"""
    try:
        from src.stores.semantic_cache import invalidate_all_soft

        invalidate_all_soft()
    except Exception as e:
        logger.warning("语义缓存作废失败（已忽略）: %s", e)


def ingest_all(
    docs_dir: str = config.DOCS_DIR,
    model: str = config.DEFAULT_EMBEDDING_ALIAS,
) -> None:
    """
    扫描 docs_dir，将支持格式的文档写入指定 embedding 对应的 ChromaDB collection。

    流程：逐文件解析 → 分块（split_structured）→ 按 doc_id/content_sha1 幂等
    （未变跳过，有变先删旧 chunk）→ Chroma upsert（内部调用 embedding function）；
    若 BM25_ENABLED，同步更新该 collection 的 BM25 索引。

    Args:
        docs_dir: 文档目录，默认 config.DOCS_DIR。
        model: embedding 别名（en / zh / m3）

    Web 单文件上传请用 `ingest_one`，不要用此函数（避免扫整个目录 re-parse 大文件）。
    """
    use_api = config.alias_is_api(model)
    model_name, collection_name = config.resolve_embedding(model)

    docs_path = Path(docs_dir).resolve()
    if not docs_path.exists():
        logger.error("文档目录不存在: %s", docs_path)
        return

    logger.info("Embedding 模型: %s  →  collection: %s (space=%s, 来源=%s)",
                model_name, collection_name, _HNSW_SPACE, "api" if use_api else "本地")

    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    collection = _open_collection(client, model_name, collection_name, use_api=use_api)

    all_files = [
        f for f in docs_path.rglob("*")
        if (
            f.is_file()
            and f.suffix.lower() in SUPPORTED_EXTENSIONS
            and not is_office_temp_file(f)
        )
    ]

    if not all_files:
        logger.warning("未在 %s 中找到任何支持格式的文档", docs_path)
        return

    logger.info("发现 %d 个文档，开始入库...", len(all_files))

    total_chunks = 0
    skipped_unchanged = 0
    for file_path in all_files:
        try:
            try:
                rel_path = file_path.resolve().relative_to(docs_path).as_posix()
            except ValueError:
                rel_path = file_path.name
            probe = probe_for_docx(file_path, rel_path)
            with ingest_slot():
                probe.file_start()
                try:
                    result = _ingest_one_file(
                        file_path,
                        docs_path,
                        collection,
                        collection_name,
                        probe=probe,
                        bm25_save=False,
                    )
                    probe.file_done(
                        result.get("status", "unknown"),
                        int(result.get("chunks", 0)),
                    )
                except Exception as exc:
                    probe.file_error(str(exc))
                    raise
            if result["status"] == "ingested":
                total_chunks += result["chunks"]
            elif result["status"] == "skipped_unchanged":
                skipped_unchanged += 1
        except Exception as e:
            logger.error("  失败: %s — %s", file_path.name, e)

    _commit_bm25_after_ingest(collection_name)

    logger.info(
        "入库完成：新增/更新 %d 块，跳过未变 %d 个文件，collection 当前总量: %d 块",
        total_chunks, skipped_unchanged, collection.count(),
    )


# ── Web UI 知识库管理辅助函数 ───────────────────────────────────────────────

def _char_count_from_metadata(md: dict) -> int:
    """从 chunk metadata 取字数；老数据无 char_count 时返回 0（不拉正文）。"""
    raw = md.get("char_count")
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


# KB 列表/统计：复用 Chroma 客户端 + 分批扫 metadata，避免单次 get 全库 OOM。
_KB_CHROMA_CLIENT: Any = None
_KB_CHROMA_LOCK = threading.Lock()
_KB_META_BATCH = 256


def _kb_chroma_client() -> Any:
    global _KB_CHROMA_CLIENT
    if _KB_CHROMA_CLIENT is None:
        with _KB_CHROMA_LOCK:
            if _KB_CHROMA_CLIENT is None:
                _KB_CHROMA_CLIENT = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    return _KB_CHROMA_CLIENT


def _iter_chunk_metadatas(collection) -> Iterator[dict]:
    """分批遍历 collection 的 chunk metadata（不取正文 / 向量）。"""
    total = collection.count()
    offset = 0
    while offset < total:
        got = collection.get(
            limit=_KB_META_BATCH,
            offset=offset,
            include=["metadatas"],
        )
        metas = got.get("metadatas") or []
        if not metas:
            break
        for md in metas:
            if md:
                yield md
        offset += len(metas)


def _merge_doc_row(grouped: dict[str, dict], md: dict) -> None:
    """把单条 chunk metadata 合并进按 doc_id 聚合的文档行。"""
    doc_id = md.get("doc_id")
    if not doc_id:
        return
    chars = _char_count_from_metadata(md)
    ingested_at = float(md.get("ingested_at") or 0.0)
    if doc_id in grouped:
        row = grouped[doc_id]
        row["chunks"] += 1
        row["total_chars"] += chars
        if ingested_at > row["ingested_at"]:
            row["ingested_at"] = ingested_at
        return
    grouped[doc_id] = {
        "doc_id": doc_id,
        "source": md.get("source") or md.get("filename") or "",
        "filename": md.get("filename") or "",
        "ext": md.get("ext") or "",
        "lang": md.get("lang") or "",
        "mtime": float(md.get("mtime") or 0.0),
        "ingested_at": ingested_at,
        "chunks": 1,
        "total_chars": chars,
    }


def _candidate_docs_dirs(model: str) -> list[Path]:
    """golden 出题时尝试定位 CLI 入库原文件的目录列表（env + ingest 历史）。"""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        if resolved.is_dir():
            roots.append(resolved)

    add(Path(config.DOCS_DIR))

    hist_path = Path(config.CHROMA_DB_PATH).resolve() / "ingest_history.json"
    if hist_path.is_file():
        try:
            import json

            data = json.loads(hist_path.read_text(encoding="utf-8"))
            _, collection_name = config.resolve_embedding(model)
            entry = (data.get("collections") or {}).get(collection_name) or {}
            for raw in entry.get("docs_dirs") or []:
                add(Path(raw))
        except Exception:
            pass

    return roots


def _safe_file_under_root(root: Path, relpath: str) -> Path | None:
    """把相对路径解析到 root 下；越界或不是文件则返回 None。"""
    root = root.resolve()
    target = (root / relpath).resolve()
    if root not in target.parents and target != root:
        return None
    return target if target.is_file() else None


def resolve_golden_input(
    model: str,
    source: str,
    doc_id: str = "",
) -> tuple[Path | None, str | None]:
    """为 golden 出题定位输入：优先物理文件，否则从 Chroma 分块拼正文。

    Returns:
        (file_path, text)：二者至多一个非空；都为空表示找不到可用内容。
    """
    if source:
        upload_root = Path(config.WEB_UPLOAD_DIR).resolve() / model
        web_file = _safe_file_under_root(upload_root, source)
        if web_file is not None:
            return web_file, None

        for docs_root in _candidate_docs_dirs(model):
            cli_file = _safe_file_under_root(docs_root, source)
            if cli_file is not None:
                return cli_file, None

    if doc_id:
        text = get_kb_document_text(model, doc_id)
        if text:
            return None, text

    return None, None


def get_kb_document_text(model: str, doc_id: str) -> str | None:
    """按 doc_id 从 Chroma 取全部分块正文，按 chunk_index 排序后拼接。"""
    if not doc_id:
        return None

    _, collection_name = config.resolve_embedding(model)
    client = _kb_chroma_client()
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        return None

    got = collection.get(
        where={"doc_id": doc_id},
        include=["documents", "metadatas"],
    )
    ids = got.get("ids") or []
    if not ids:
        return None

    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    pairs: list[tuple[int, str]] = []
    for md, doc in zip(metas, docs, strict=False):
        if not doc:
            continue
        idx = 0
        if md:
            try:
                idx = int(md.get("chunk_index") or 0)
            except (TypeError, ValueError):
                idx = 0
        pairs.append((idx, doc))
    if not pairs:
        return None

    pairs.sort(key=lambda item: item[0])
    return "\n\n".join(text for _, text in pairs)


def list_kb_documents(model: str = config.DEFAULT_EMBEDDING_ALIAS) -> list[dict]:
    """聚合指定 collection 内所有 chunks 的 metadata，按 doc_id 分组返回文档级清单。

    Args:
        model: embedding 别名（en / zh / m3 等）

    Returns:
        list of dict，每项含 doc_id / filename / source / ext / lang / mtime /
        ingested_at / chunks / total_chars。collection 不存在或为空时返回 []。
        老数据缺 ingested_at 时返回 0.0（前端显示 "-"，重传后更新）。
        total_chars 来自 metadata.char_count；缺该字段的老 chunk 计 0（避免拉全库正文 OOM）。
        扫描按 256 条一批拉 metadata，降低峰值内存。
    """
    _, collection_name = config.resolve_embedding(model)
    client = _kb_chroma_client()
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        return []

    grouped: dict[str, dict] = {}
    for md in _iter_chunk_metadatas(collection):
        _merge_doc_row(grouped, md)

    # 按 ingested_at 倒序（最近入库的在前）；缺失值（老数据）排在最后
    return sorted(grouped.values(), key=lambda x: x["ingested_at"], reverse=True)


# count_kb_documents 的进程内缓存：collection_name -> (doc_count, chunk_count)。
# L1 库列表频繁进出，每次重扫全部 metadatas 去重 doc_id 很慢；写操作（入库 / 删除 /
# 清空）会按库失效对应条目。CLI 在另一进程入库不会触发失效，前端可用 refresh 强制重算。
_KB_STATS_CACHE: dict[str, tuple[int, int]] = {}


def _invalidate_kb_stats(collection_name: str) -> None:
    """让某个 collection 的统计缓存失效（下次重算）。"""
    _KB_STATS_CACHE.pop(collection_name, None)


def count_kb_documents(
    model: str = config.DEFAULT_EMBEDDING_ALIAS, *, use_cache: bool = True
) -> tuple[int, int]:
    """轻量统计指定 collection 的 (文档数, chunk 数)，只读 metadatas 不读正文。

    给库列表（L1）用：chunk 数取 collection.count()，文档数按 doc_id 去重。
    命中进程内缓存直接返回；collection 不存在返回 (0, 0)。
    """
    _, collection_name = config.resolve_embedding(model)
    if use_cache and collection_name in _KB_STATS_CACHE:
        return _KB_STATS_CACHE[collection_name]

    client = _kb_chroma_client()
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        return 0, 0

    chunk_count = collection.count()
    doc_ids: set[str] = set()
    for md in _iter_chunk_metadatas(collection):
        did = md.get("doc_id")
        if did:
            doc_ids.add(str(did))
    stats = (len(doc_ids), chunk_count)
    _KB_STATS_CACHE[collection_name] = stats
    return stats


def delete_kb_document(
    doc_id: str,
    model: str = config.DEFAULT_EMBEDDING_ALIAS,
    web_upload_dir: str | None = None,
) -> tuple[bool, int]:
    """删除单个文档对应的所有 chunks（Chroma + BM25 + 物理文件）。

    Args:
        doc_id:         要删的文档 ID（list_kb_documents 返回的 doc_id）
        model:          embedding 别名
        web_upload_dir: web 上传落盘目录；删物理文件用。None 则取 config.WEB_UPLOAD_DIR

    Returns:
        (found, chunks_removed)：
            found 表示是否找到 doc_id；chunks_removed 是 Chroma 实际删除的块数。
    """
    if web_upload_dir is None:
        web_upload_dir = config.WEB_UPLOAD_DIR

    model_name, collection_name = config.resolve_embedding(model)
    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        return False, 0

    existing = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
    existing_ids = existing.get("ids") or []
    existing_metas = existing.get("metadatas") or []
    if not existing_ids:
        return False, 0

    # 删 Chroma chunks
    collection.delete(ids=existing_ids)
    chunks_removed = len(existing_ids)
    logger.info("KB 删除文档 doc_id=%s → 移除 %d 个 chunks", doc_id, chunks_removed)

    # 同步清 BM25 索引（不阻塞主流程）；走共享缓存实例，删完即对检索可见
    if config.BM25_ENABLED:
        try:
            from src.rag.bm25_index import commit_index, get_index

            bm25 = get_index(collection_name)
            if bm25.delete_by_doc_id(doc_id):
                commit_index(collection_name)
        except Exception as e:
            logger.warning("KB 删除文档 BM25 同步失败 doc_id=%s: %s", doc_id, e)

    # 删物理文件（仅清 web_uploads 目录下的；不动 data_*/ 等 git tracked 目录）
    try:
        source = (existing_metas[0] or {}).get("source") if existing_metas else None
        if source:
            web_root = Path(web_upload_dir).resolve()
            file_path = (web_root / source).resolve()
            # 安全检查：只删落在 web_upload_dir 内的文件，防恶意 source 跳出
            if web_root in file_path.parents and file_path.is_file():
                file_path.unlink()
                logger.info("KB 删除物理文件: %s", file_path)
    except Exception as e:
        logger.warning("KB 删除物理文件失败 doc_id=%s: %s", doc_id, e)

    _invalidate_semantic_cache()
    _invalidate_kb_stats(collection_name)
    return True, chunks_removed


def delete_all_kb_documents(
    model: str = config.DEFAULT_EMBEDDING_ALIAS,
    web_upload_dir: str | None = None,
) -> dict:
    """清空整个 KB（Chroma collection + BM25 索引 + web_uploads 物理文件）。

    与 `delete_kb_document` 的语义一致（chunks + BM25 + 物理文件三处一起清），
    只是范围扩到整个 collection。仅删除 web_uploads 目录内文件，不动 data_*/ 等
    git tracked 目录。

    Args:
        model:          embedding 别名
        web_upload_dir: web 上传落盘目录；None 则取 config.WEB_UPLOAD_DIR

    Returns:
        dict: {docs_removed, chunks_removed, files_removed}
              （都是统计量；任一子步骤失败会记 warning 但不中断后续清理）
    """
    if web_upload_dir is None:
        web_upload_dir = config.WEB_UPLOAD_DIR

    _, collection_name = config.resolve_embedding(model)
    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)

    docs_removed = 0
    chunks_removed = 0
    files_removed = 0

    # 1. 统计 + 删 Chroma collection（一次性 drop 比逐 doc 删快）
    try:
        collection = client.get_collection(name=collection_name)
        chunks_removed = collection.count()
        # 用 list_kb_documents 的逻辑数 doc_id 个数（统计用，不影响删除）
        docs_removed = len(list_kb_documents(model=model))
        client.delete_collection(name=collection_name)
        logger.info(
            "KB 清空 collection=%s → 移除 %d 个文档 / %d 个 chunks",
            collection_name, docs_removed, chunks_removed,
        )
    except Exception as e:
        logger.warning("KB 清空 Chroma collection 失败（已忽略继续）: %s", e)

    # 2. 删 BM25 索引文件 + 清进程缓存（否则 retriever 仍持有旧索引实例）
    if config.BM25_ENABLED:
        try:
            from src.rag.bm25_index import drop_index, get_index_path

            bm25_path = get_index_path(collection_name)
            if bm25_path.exists():
                bm25_path.unlink()
                logger.info("KB 清空 BM25 索引: %s", bm25_path)
            drop_index(collection_name)
        except Exception as e:
            logger.warning("KB 清空 BM25 索引失败: %s", e)

    # 3. 清该库 upload 目录（per-alias 子目录，递归）：整目录删掉，避免误伤其它库的文件
    try:
        web_root = Path(web_upload_dir).resolve()
        if web_root.is_dir():
            files_removed = sum(
                1
                for fp in web_root.rglob("*")
                if fp.is_file() and fp.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            shutil.rmtree(web_root, ignore_errors=True)
            logger.info("KB 清空物理文件: %d 个（%s）", files_removed, web_root)
    except Exception as e:
        logger.warning("KB 清空 web_uploads 目录失败: %s", e)

    _invalidate_semantic_cache()
    _invalidate_kb_stats(collection_name)
    return {
        "docs_removed": docs_removed,
        "chunks_removed": chunks_removed,
        "files_removed": files_removed,
    }


def _build_arg_parser() -> "argparse.ArgumentParser":
    """构造独立 CLI 入口的 argparse；与文件 docstring 中的示例保持一致。"""
    import argparse
    p = argparse.ArgumentParser(
        prog="python -m src.rag.ingest",
        description=(
            "扫描 datasets/ 目录并入库到 ChromaDB（tools/cli/rag_cli.py ingest 的底层调用）。"
            "支持 upsert，重复运行不会产生重复 chunk。"
        ),
    )
    p.add_argument(
        "-d", "--docs-dir",
        default=config.DOCS_DIR,
        help=f"文档目录路径（默认 {config.DOCS_DIR}）",
    )
    p.add_argument(
        "-m", "--model",
        default=config.DEFAULT_EMBEDDING_ALIAS,
        help=(
            "embedding 模型别名（en/zh/m3，或 api-m3=m3 走云端编码，详见 src/config.py EMBEDDING_MODELS）；"
            f"默认 {config.DEFAULT_EMBEDDING_ALIAS}（来自 .env EMBEDDING_MODEL）。"
            "选 m3=本地编码、api-m3=云端编码，与 .env 全局默认解耦"
        ),
    )
    return p


def _main(argv: "list[str] | None" = None) -> int:
    """脚本入口；解析参数后调 ingest_all。"""
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    ingest_all(docs_dir=args.docs_dir, model=args.model)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
