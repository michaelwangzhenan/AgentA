"""一次性从 Chroma metadata 回填 KB 文档级索引。

用法：
    python -m src.rag.kb_doc_backfill
    python -m src.rag.kb_doc_backfill --model zh
    python -m src.rag.kb_doc_backfill --all
"""
from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

load_dotenv(override=True)

import src.config as config
from src.rag.ingest import backfill_kb_doc_index

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="从 Chroma 回填 KB 文档级索引")
    parser.add_argument(
        "-m", "--model",
        default=config.DEFAULT_EMBEDDING_ALIAS,
        help=f"embedding 别名（默认 {config.DEFAULT_EMBEDDING_ALIAS}）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="回填全部已定义 embedding 别名对应 collection",
    )
    args = parser.parse_args()

    aliases = list(config.EMBEDDING_MODELS) if args.all else [args.model]
    total = 0
    for alias in aliases:
        n = backfill_kb_doc_index(alias)
        logger.info("  %s → %d 文档", alias, n)
        total += n
    logger.info("回填完成，共 %d 文档", total)


if __name__ == "__main__":
    main()
