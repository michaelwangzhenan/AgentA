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
load_dotenv()
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

import config
from rag.parser import SUPPORTED_EXTENSIONS, parse_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def chunk_text(text: str, size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    """
    将文档文本按 size 字符分块，相邻块之间有 overlap 字符重叠。

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


def _make_chunk_id(file_path: str, chunk_index: int) -> str:
    """用文件路径 + 块序号生成稳定唯一 ID（MD5 前 16 位）。"""
    raw = f"{file_path}::{chunk_index}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def ingest_all(
    docs_dir: str = config.DOCS_DIR,
    model: str = config._default_model_env,
) -> None:
    """
    扫描 docs_dir 目录，将所有支持格式的文档入库到 ChromaDB。

    流程：逐文件解析 → 分块 → 向量化（由 ChromaDB 内部调用 embedding function） → upsert。

    Args:
        docs_dir: 文档目录路径，默认读取 config.DOCS_DIR。
        model: embedding 模型别名（en/zh）或模型名称，决定使用哪个 collection。
               默认使用 config._default_model_env（读取 .env EMBEDDING_MODEL）。
    """
    model_name, collection_name = config.resolve_embedding(model)

    docs_path = Path(docs_dir)
    if not docs_path.exists():
        logger.error(f"文档目录不存在: {docs_path.resolve()}")
        return

    logger.info(f"Embedding 模型: {model_name}  →  collection: {collection_name}")

    # 初始化 ChromaDB 客户端和 embedding 函数
    client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    embedding_fn = SentenceTransformerEmbeddingFunction(
        model_name=model_name
    )
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,  # type: ignore[arg-type]
    )

    # 扫描文档目录（递归）
    all_files = [
        f for f in docs_path.rglob("*")
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not all_files:
        logger.warning(f"未在 {docs_path} 中找到任何支持格式的文档")
        return

    logger.info(f"发现 {len(all_files)} 个文档，开始入库...")

    total_chunks = 0
    for file_path in all_files:
        try:
            logger.info(f"  解析: {file_path.name}")
            text = parse_file(file_path)

            if not text.strip():
                logger.warning(f"  跳过（内容为空）: {file_path.name}")
                continue

            chunks = chunk_text(text)
            if not chunks:
                logger.warning(f"  跳过（分块结果为空）: {file_path.name}")
                continue

            # 删除该文件在 ChromaDB 中的所有旧 chunks（防止文件缩短后残留过时数据）
            existing = collection.get(
                where={"source": file_path.name},
                include=[],
            )
            if existing["ids"]:
                collection.delete(ids=existing["ids"])
                logger.debug(f"  清除旧数据: {file_path.name} → 删除 {len(existing['ids'])} 条")

            # 写入新 chunks
            ids = [_make_chunk_id(str(file_path), i) for i in range(len(chunks))]
            metadatas = [
                {"source": file_path.name, "chunk_index": i}
                for i in range(len(chunks))
            ]

            collection.upsert(
                ids=ids,
                documents=chunks,
                metadatas=metadatas,  # type: ignore[arg-type]
            )

            logger.info(f"  入库: {file_path.name} → {len(chunks)} 块")
            total_chunks += len(chunks)

        except Exception as e:
            logger.error(f"  失败: {file_path.name} — {e}")

    logger.info(f"入库完成，共写入 {total_chunks} 个文本块，"
                f"collection 当前总量: {collection.count()} 块")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="私有知识库文档入库工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
模型别名:
  en  →  all-MiniLM-L6-v2   英文/多语言（默认）
  zh  →  BAAI/bge-small-zh   中文优化

示例:
  python -m rag.ingest
  python -m rag.ingest --model zh
  python -m rag.ingest -d ./docs_zh -m zh
  python -m rag.ingest -d ./docs_en -m en
""",
    )
    parser.add_argument(
        "-d", "--docs-dir",
        default=config.DOCS_DIR,
        help=f"文档目录路径（默认: {config.DOCS_DIR}）",
    )
    parser.add_argument(
        "-m", "--model",
        default=config._default_model_env,
        help="embedding 模型别名：en / zh，或完整模型名（默认: %(default)s）",
    )
    args = parser.parse_args()
    ingest_all(docs_dir=args.docs_dir, model=args.model)
