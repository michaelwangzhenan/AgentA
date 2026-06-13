#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
只读巡检：Chroma / SQLite / BM25 落盘位置与规模统计。

用法：
    python tools/db_show.py -h
    python tools/db_show.py summary
    python tools/db_show.py chroma [--sample N] [--collection NAME]
    python tools/db_show.py sqlite
    python tools/db_show.py bm25

说明见 docs/v_1_0/interation/iter_17_db_tool.md。
"""
from __future__ import annotations

import argparse
import os
import pickle
import sqlite3
import sys
import textwrap
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

for _key in ("HF_ENDPOINT", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    _val = os.getenv(_key)
    if _val:
        os.environ[_key] = _val

import src.config as config  # noqa: E402

_DOC_PREVIEW_MAX = 400
_META_KEYS_MAX = 12


def _truncate(s: str, max_len: int = _DOC_PREVIEW_MAX) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if len(s) <= max_len:
        return s
    # max_len < 3 时省略号本身放不下，退化为纯截断，避免负索引切片
    if max_len < 3:
        return s[:max_len]
    return s[: max_len - 3] + "..."


def _sqlite_paths_from_config() -> list[tuple[str, Path]]:
    """（配置项名, 路径）列表，路径未解析。"""
    pairs: list[tuple[str, Path]] = []
    for key in (
        "MEMORY_DB_PATH",
        "AUTH_DB_PATH",
        "USAGE_DB_PATH",
        "RAG_GOLDEN_DB_PATH",
        "USER_MEMORY_DB_PATH",
        "LEARNING_PLAN_DB_PATH",
        "QUIZ_DB_PATH",
        "SRS_DB_PATH",
    ):
        raw = getattr(config, key, None)
        if raw:
            pairs.append((key, Path(str(raw))))
    return pairs


def _collect_sqlite_db_files() -> list[tuple[str, Path]]:
    """
    配置中的 *_DB_PATH + db/sqlite/*.db，按 resolve 去重。
    每项为 (来源标签, 路径)；同一文件多次出现只保留首次标签。
    """
    seen: set[str] = set()
    out: list[tuple[str, Path]] = []

    for label, p in _sqlite_paths_from_config():
        rp = (_PROJECT_ROOT / p).resolve() if not p.is_absolute() else p.resolve()
        k = str(rp).lower()
        if k in seen:
            continue
        seen.add(k)
        out.append((label, rp))

    glob_dir = (_PROJECT_ROOT / "db" / "sqlite").resolve()
    if glob_dir.is_dir():
        for f in sorted(glob_dir.glob("*.db")):
            rp = f.resolve()
            k = str(rp).lower()
            if k in seen:
                continue
            seen.add(k)
            out.append((f"glob:{glob_dir.name}", rp))

    return out


def _sqlite_table_row_counts(db_path: Path) -> list[tuple[str, int]]:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        rows: list[tuple[str, int]] = []
        for t in tables:
            q = f'SELECT COUNT(*) FROM "{t.replace(chr(34), chr(34)+chr(34))}"'
            n = conn.execute(q).fetchone()[0]
            rows.append((t, int(n)))
        return rows
    finally:
        conn.close()


def _bm25_dir() -> Path:
    base = config.BM25_INDEX_DIR or config.CHROMA_DB_PATH
    p = Path(base)
    return p.resolve() if p.is_absolute() else (_PROJECT_ROOT / p).resolve()


def _chroma_root() -> Path:
    p = Path(config.CHROMA_DB_PATH)
    return p.resolve() if p.is_absolute() else (_PROJECT_ROOT / p).resolve()


def _chroma_client():
    import chromadb

    root = _chroma_root()
    return chromadb.PersistentClient(path=str(root))


def _list_chroma_collections(client, name_filter: str | None) -> list:
    cols = client.list_collections()
    if name_filter:
        cols = [c for c in cols if c.name == name_filter]
    return sorted(cols, key=lambda c: c.name)


def cmd_sqlite() -> None:
    print("=== SQLite ===")
    for label, db_path in _collect_sqlite_db_files():
        print(f"\n[{label}] {db_path}")
        if not db_path.exists():
            print("  (文件不存在，跳过)")
            continue
        try:
            for t, n in _sqlite_table_row_counts(db_path):
                print(f"  {t}: {n} 行")
        except sqlite3.Error as e:
            print(f"  读取失败: {e}")


def _print_chroma_counts_only(client) -> None:
    print(f"[CHROMA_DB_PATH] {_chroma_root()}")
    for col in sorted(client.list_collections(), key=lambda c: c.name):
        c = client.get_collection(col.name)
        print(f"  {col.name}: {c.count()} 条")


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


def cmd_chroma(sample: int, collection: str | None) -> None:
    client = _chroma_client()
    cols_meta = _list_chroma_collections(client, collection)
    if collection and not cols_meta:
        print(f"未找到 collection: {collection!r}")
        sys.exit(1)

    print(f"=== Chroma ===\n[CHROMA_DB_PATH] {_chroma_root()}\n")
    for col in cols_meta:
        c = client.get_collection(col.name)
        n = c.count()
        print(f"--- {col.name} ---\n条数: {n}")
        if sample <= 0:
            print()
            continue
        lim = min(sample, max(n, 0))
        if lim == 0:
            print("(库为空)\n")
            continue
        # 不用 peek：peek 默认连 embeddings 一起取，遇到向量段异常的 collection 会整体崩；
        # 这里只取正文 + metadata，既避开该坑又更快。单库失败降级跳过，不中断其余 collection。
        try:
            peeked = c.get(limit=lim, include=["documents", "metadatas"])
        except Exception as e:
            print(f"(抽样失败，跳过：{type(e).__name__}: {e})\n")
            continue
        ids = peeked.get("ids") or []
        docs = peeked.get("documents") or []
        metas = peeked.get("metadatas") or []
        for i, doc_id in enumerate(ids):
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else None
            body = _truncate(doc or "")
            print(f"  [{i + 1}] id={doc_id}")
            print(textwrap.indent(f"正文: {body}", "      "))
            print(textwrap.indent(f"metadata: {_metadata_preview(meta)}", "      "))
        print()


def cmd_chroma_summary() -> None:
    print("=== Chroma ===")
    client = _chroma_client()
    _print_chroma_counts_only(client)


def cmd_bm25() -> None:
    print("=== BM25 ===")
    base = _bm25_dir()
    print(f"[BM25_INDEX_DIR] {base}\n")
    if not base.is_dir():
        print("(目录不存在)")
        return

    from src.rag.bm25_index import BM25Index

    files = sorted(base.glob("bm25_*.pkl"))
    if not files:
        print("(未找到 bm25_*.pkl)")
        return

    for path in files:
        stem = path.stem
        if not stem.startswith("bm25_"):
            continue
        coll = stem[len("bm25_") :]
        size = path.stat().st_size if path.exists() else 0
        print(f"--- {path.name} ---")
        print(f"  路径: {path}")
        print(f"  字节: {size}")
        try:
            with open(path, "rb") as f:
                pickle.load(f)
        except Exception as e:
            print(f"  pickle 校验失败: {type(e).__name__}: {e}")
            print()
            continue
        idx = BM25Index.load_or_new(coll, path)
        print(f"  collection: {idx.collection_name}")
        print(f"  文档块数: {len(idx.docs)}")
        print(f"  k1={idx.k1}, b={idx.b}")
        print()


def cmd_bm25_summary() -> None:
    """仅一行规模信息，供 summary 用。"""
    base = _bm25_dir()
    print(f"[BM25_INDEX_DIR] {base}")
    if not base.is_dir():
        print("  (目录不存在)")
        return
    from src.rag.bm25_index import BM25Index

    files = sorted(base.glob("bm25_*.pkl"))
    if not files:
        print("  (无 pkl 文件)")
        return
    for path in files:
        stem = path.stem
        coll = stem[len("bm25_") :] if stem.startswith("bm25_") else stem
        try:
            with open(path, "rb") as f:
                pickle.load(f)
        except Exception as e:
            print(f"  {path.name}: 无法加载 ({e})")
            continue
        idx = BM25Index.load_or_new(coll, path)
        print(f"  {path.name}: {len(idx.docs)} 块, {path.stat().st_size} 字节")


def cmd_summary() -> None:
    cmd_chroma_summary()
    print()
    cmd_sqlite()
    print()
    print("=== BM25===")
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
        prog="db_show",
        description="只读查看 Chroma / SQLite / BM25 落盘与规模。",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary", help="Chroma + SQLite + BM25 统计摘要（无正文抽样）")

    p_chroma = sub.add_parser("chroma", help="Chroma 条数；可选正文抽样（peek，无需 embedding）")
    p_chroma.add_argument(
        "--sample",
        type=int,
        default=3,
        metavar="N",
        help="每个 collection 抽样条数，默认 3；0 表示仅统计",
    )
    p_chroma.add_argument("--collection", type=str, default=None, metavar="NAME", help="只处理该 collection")

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
