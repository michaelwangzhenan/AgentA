"""
文档入库模块 —— 离线预处理阶段使用

执行完整的入库流程：扫描 docs/ 目录 → 解析文本 → 分块 → 向量化 → 存入 ChromaDB。
支持重复运行（upsert），文档更新后重新运行即可，不会重复入库。
不同 embedding 模型使用独立的 ChromaDB collection，互不干扰。

使用方式：
    python -m rag.ingest                          # 默认目录 + 默认模型（en）
    python -m rag.ingest --model zh               # 使用中文模型
    python -m rag.ingest --docs-dir ./docs_zh --model zh
    python -m rag.ingest -d ./docs_en -m en

模型别名：
    en  →  all-MiniLM-L6-v2  （英文/多语言，collection: kb_en）
    zh  →  BAAI/bge-small-zh  （中文优化，  collection: kb_zh）
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
import logging
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

import src.config as config
from src.rag.parser import SUPPORTED_EXTENSIONS, parse_file
from src.rag.splitter import split_structured

logger = logging.getLogger(__name__)

# 入库时统一使用的相似度空间，与 retriever 必须保持一致。
# BGE / e5 / GTE / MiniLM 等 sentence-transformers 模型均按余弦相似度训练，
# 使用默认 squared L2 排序会与训练目标错位，命中率显著下降。
_HNSW_SPACE: str = "cosine"


def chunk_text(text: str, size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    """
    [向后兼容] 将文档文本按 size 字符分块，相邻块之间有 overlap 字符重叠。

    本函数保留了"按字符等步长滑动"的语义，仅供老调用方与现有单元测试使用。
    新代码（含 ingest_all 自身）请使用 src.rag.splitter.split_structured，
    它能识别 [[PAGE:N]] 与 Markdown 标题，产出带 heading_path / page_no 的 Chunk。

    Args:
        text: 待分块的原始文本。
        size: 每块最大字符数，默认 600。
        overlap: 相邻块重叠字符数，默认 100。

    Returns:
        分块后的字符串列表，每块长度不超过 size。
    """
    if not text.strip():
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        # 若已到末尾则退出
        if end >= text_len:
            break
        start += size - overlap

    return chunks


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


def _open_collection(client, model_name: str, collection_name: str):
    """
    获取（或创建）collection，并保证其 hnsw:space=cosine。

    场景：
      - collection 不存在 → 直接创建，写入 cosine metadata。
      - collection 已存在但 metadata 不是 cosine（旧库用默认 L2 建的）→ 警告并 drop&recreate，
        因为 ingest 本来就要重写数据，这里顺便修正空间。用户保留的旧 chunks 会丢失，
        但下面会重新写入，整体语义不受损。
    """
    embedding_fn = SentenceTransformerEmbeddingFunction(model_name=model_name)

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


def ingest_all(
    docs_dir: str = config.DOCS_DIR,
    model: str = config.DEFAULT_EMBEDDING_ALIAS,
) -> None:
    """
    扫描 docs_dir 目录，将所有支持格式的文档入库到 ChromaDB。

    流程：逐文件解析 → 分块 → 向量化（由 ChromaDB 内部调用 embedding function） → upsert。

    Args:
        docs_dir: 文档目录路径，默认读取 config.DOCS_DIR。
        model: embedding 模型别名（en/zh）或模型名称，决定使用哪个 collection。
               默认使用 config.DEFAULT_EMBEDDING_ALIAS（读取 .env EMBEDDING_MODEL）。
    """
    model_name, collection_name = config.resolve_embedding(model)

    docs_path = Path(docs_dir).resolve()
    if not docs_path.exists():
        logger.error("文档目录不存在: %s", docs_path)
        return

    logger.info("Embedding 模型: %s  →  collection: %s (space=%s)",
                model_name, collection_name, _HNSW_SPACE)

    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    collection = _open_collection(client, model_name, collection_name)

    all_files = [
        f for f in docs_path.rglob("*")
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
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
            doc_id = _doc_id_from_relpath(rel_path)

            logger.info("  解析: %s", rel_path)
            text = parse_file(file_path)

            if not text.strip():
                logger.warning("  跳过（内容为空）: %s", rel_path)
                continue

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
                    skipped_unchanged += 1
                    continue
                # 内容变了：先删旧 chunks
                collection.delete(ids=existing_ids)
                logger.info("  清除旧数据: %s → 删除 %d 条", rel_path, len(existing_ids))

            structured = split_structured(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
            if not structured:
                logger.warning("  跳过（分块结果为空）: %s", rel_path)
                continue

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            lang = _detect_lang(text)
            ext = file_path.suffix.lower()

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
                    "content_sha1": content_hash,
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

            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,  # type: ignore[arg-type]
            )

            # 同步写入 BM25 倒排索引（如启用）；与 Chroma 共享 ids 保证融合时可对齐
            if config.BM25_ENABLED:
                try:
                    from src.rag.bm25_index import (
                        BM25Index,
                        get_index_path,
                        save_index,
                    )

                    bm25_path = get_index_path(collection_name)
                    bm25 = BM25Index.load_or_new(collection_name, bm25_path)
                    # 替换该 doc_id 下所有旧 chunk（先删后写）
                    bm25.delete_by_doc_id(doc_id)
                    bm25.upsert(ids=ids, documents=documents, metadatas=metadatas)
                    save_index(bm25, bm25_path)
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
            total_chunks += len(documents)

        except Exception as e:
            logger.error("  失败: %s — %s", file_path.name, e)

    logger.info(
        "入库完成：新增/更新 %d 块，跳过未变 %d 个文件，collection 当前总量: %d 块",
        total_chunks, skipped_unchanged, collection.count(),
    )
