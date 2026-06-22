#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
只读巡检：Chroma / SQLite / BM25 落盘位置与规模统计。

用法：
    python tools/cli/db_cli.py -h
    python tools/cli/db_cli.py summary
    python tools/cli/db_cli.py chroma [--sample N] [--collection NAME]
    python tools/cli/db_cli.py sqlite
    python tools/cli/db_cli.py bm25

读逻辑统一在 src/services/db_inspect.py（CLI 与 /admin/db API 共用），本文件只负责终端排版。
说明见 docs/v_1_0/interation/iter_17_db_tool.md。
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # tools/cli/x.py → 仓库根
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

for _key in ("HF_ENDPOINT", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    _val = os.getenv(_key)
    if _val:
        os.environ[_key] = _val

import src.services.db_inspect as inspect  # noqa: E402

# 复用公共模块的读逻辑；保留下划线别名供单测引用。
_truncate = inspect.truncate
_sqlite_table_row_counts = inspect.sqlite_table_row_counts

_META_KEYS_MAX = 12


def _metadata_preview(md: dict | None) -> str:
    if not md:
        return "{}"
    items = list(md.items())
    if len(items) > _META_KEYS_MAX:
        items = items[:_META_KEYS_MAX]
        tail = f", …(+{len(md) - _META_KEYS_MAX} 键)"
    else:
        tail = ""
    inner = ", ".join(f"{k}={repr(v)[:80]}" for k, v in items)
    return "{" + inner + tail + "}"


def cmd_sqlite() -> None:
    print("=== SQLite ===")
    for db in inspect.sqlite_databases()["databases"]:
        print(f"\n[{db['label']}] {db['path']}")
        if not db["exists"]:
            print("  (文件不存在，跳过)")
            continue
        if db.get("error"):
            print(f"  读取失败: {db['error']}")
            continue
        for t in db["tables"]:
            print(f"  {t['name']}: {t['rows']} 行")


def cmd_chroma(sample: int, collection: str | None) -> None:
    data = inspect.chroma_collections()
    cols = data["collections"]
    if collection:
        cols = [c for c in cols if c["name"] == collection]
        if not cols:
            print(f"未找到 collection: {collection!r}")
            sys.exit(1)

    print(f"=== Chroma ===\n[CHROMA_DB_PATH] {data['root']}\n")
    for col in cols:
        name = col["name"]
        n = col.get("count")
        print(f"--- {name} ---\n条数: {n}")
        if sample <= 0 or not n:
            print()
            continue
        page = inspect.chroma_items(name, limit=min(sample, n), offset=0)
        if page.get("error"):
            print(f"(抽样失败，跳过：{page['error']})\n")
            continue
        for i, item in enumerate(page["items"]):
            print(f"  [{i + 1}] id={item['id']}")
            print(textwrap.indent(f"正文: {item['preview']}", "      "))
            print(textwrap.indent(f"metadata: {_metadata_preview(item['metadata'])}", "      "))
        print()


def cmd_chroma_summary() -> None:
    print("=== Chroma（摘要）===")
    data = inspect.chroma_collections()
    print(f"[CHROMA_DB_PATH] {data['root']}")
    for col in data["collections"]:
        print(f"  {col['name']}: {col.get('count')} 条")


def cmd_bm25() -> None:
    print("=== BM25 ===")
    data = inspect.bm25_indexes()
    print(f"[BM25_INDEX_DIR] {data['dir']}\n")
    if not data["indexes"]:
        print("(未找到 bm25_*.pkl)")
        return
    for idx in data["indexes"]:
        print(f"--- {idx['file']} ---")
        print(f"  字节: {idx['bytes']}")
        if idx.get("error"):
            print(f"  加载失败: {idx['error']}")
            print()
            continue
        print(f"  collection: {idx['collection']}")
        print(f"  文档块数: {idx['docs']}")
        print(f"  k1={idx['k1']}, b={idx['b']}")
        print()


def cmd_bm25_summary() -> None:
    data = inspect.bm25_indexes()
    print(f"[BM25_INDEX_DIR] {data['dir']}")
    if not data["indexes"]:
        print("  (无 pkl 文件)")
        return
    for idx in data["indexes"]:
        if idx.get("error"):
            print(f"  {idx['file']}: 无法加载 ({idx['error']})")
            continue
        print(f"  {idx['file']}: {idx['docs']} 块, {idx['bytes']} 字节")


def cmd_summary() -> None:
    cmd_chroma_summary()
    print()
    cmd_sqlite()
    print()
    print("=== BM25（摘要）===")
    cmd_bm25_summary()


def _configure_stdio_utf8() -> None:
    """Windows 默认代码页下避免中文帮助与输出乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> None:
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        prog="db_cli",
        description="只读查看 Chroma / SQLite / BM25 落盘与规模。",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary", help="Chroma + SQLite + BM25 统计摘要（无正文抽样）")

    p_chroma = sub.add_parser("chroma", help="Chroma 条数；可选正文抽样（无需 embedding）")
    p_chroma.add_argument(
        "--sample", type=int, default=3, metavar="N",
        help="每个 collection 抽样条数，默认 3；0 表示仅统计",
    )
    p_chroma.add_argument(
        "--collection", type=str, default=None, metavar="NAME", help="只处理该 collection",
    )

    sub.add_parser("sqlite", help="SQLite：配置路径 + db/sqlite/*.db，表级行数")
    sub.add_parser("bm25", help="BM25：每个 bm25_*.pkl 的规模与加载结果")

    args = parser.parse_args(argv)

    if args.cmd == "summary":
        cmd_summary()
    elif args.cmd == "chroma":
        cmd_chroma(args.sample, args.collection)
    elif args.cmd == "sqlite":
        cmd_sqlite()
    elif args.cmd == "bm25":
        cmd_bm25()
    else:
        parser.error(f"未知子命令: {args.cmd}")


if __name__ == "__main__":
    main()
