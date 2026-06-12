#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG 知识库入库工具

启动 AgentA（CLI / WebUI）前，用本脚本把 ./datasets/ 下的私有文档一次性灌入向量库 +
BM25 倒排索引。底层复用 src.rag.ingest.ingest_all，
额外补齐了"清空旧库"和"列出当前库状态"两个工程化操作。

注：原 CLI `/ingest` 交互命令已废弃，RAG 运维全部走本工具。

CLI 用法（三个原语：读 / 写 / 抹）：
    python tools/rag_cli.py -h                    查看帮助
    python tools/rag_cli.py status                只读：查看每个 collection 的真实状态
    python tools/rag_cli.py ingest                写：幂等增量入库（默认 datasets/data_en + 默认模型）
    python tools/rag_cli.py ingest -d ./datasets/data_zh -m zh
    python tools/rag_cli.py clear                 抹：一键清空全部 collection + BM25（需 yes 确认）
    python tools/rag_cli.py clear -m m3           抹：只清空指定 alias（与 ingest -m 对齐）

模型别名（详见 src/config.py EMBEDDING_MODELS）：
    en  →  all-MiniLM-L6-v2     collection=kb_en   英文/多语言
    zh  →  BAAI/bge-small-zh    collection=kb_zh   中文优化
    m3  →  BAAI/bge-m3          collection=kb_m3   多语言单库

子命令对照：
    status    只读：chunks / bm25 来自 chroma + bm25 文件；model / docs_dirs 来自本脚本
              维护的 sidecar JSON（CHROMA_DB_PATH 下 ingest_history.json），未记录显示 unknown。
    ingest    底层调 src.rag.ingest.ingest_all，本身就是幂等增量：内容未变（content_sha1
              一致）的文件自动跳过；新增 / 修改的文件重 embed；删除的文件不会回收孤儿 chunks。
              成功后把 (alias, model, docs_dir, 时间) 写进 sidecar 历史。
    clear     不带 -m 时直接 rm -rf 整个 Chroma 持久化根目录（含历史孤儿 segment），并清掉 BM25 / sidecar；
              带 -m alias 时只 drop 该 collection，并自动清理磁盘上不再被任何 collection 引用的
              孤儿 UUID 目录（包括刚 drop 掉的那个 vector segment）。均需输入 yes 二次确认。
              想做"全量重建"就：clear → ingest，两步显式。

设计说明：
    - sidecar JSON 仅在本脚本读写，src/rag 产品代码不感知。换言之 main.py 跑起来
      不依赖也不读这份元数据，纯运维巡检用，删掉也不影响 RAG 工作。
    - 不同 embedding 维度不可混用同一 collection；切换模型只需换 alias，自动落不同 collection。
    - HuggingFace 模型未本地缓存时，ingest 会触发首次下载（建议先跑 download_models.py）。
    - clear 是不可恢复操作，仅清向量 / 索引，不会动 ./datasets/ 下的原始文件。
    - 改了 CHUNK_SIZE / CHUNK_OVERLAP 等切分参数后，由于 content_sha1 没变 ingest 会跳过旧文件，
      此时需要先 clear 再 ingest 才能让新切分生效。
    - 孤儿清理原理：Chroma 的 client.delete_collection() 只动 catalog（chroma.sqlite3 内的 collections /
      segments 表），不会 unlink 已落盘的持久化根下 <uuid>/ vector segment 目录。本脚本 clear 时会反查
      sqlite 的 segments 表，把磁盘上不在活跃 VECTOR 段集合里的 UUID 目录全部物理删除，避免越攒越多。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# Chroma 把每个 segment 落成持久化根下 <uuid>/ 这种目录；用正则识别 UUID 形态，
# 避免误删 bm25_*.pkl / chroma.sqlite3 / ingest_history.json 等其它 sibling 文件。
_UUID_DIR_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# ── 让脚本能在仓库任意位置被调起：把工程根加进 sys.path ────────────────────────
# 必须先于 `from src...` 的任何 import；否则在 `python tools/rag_cli.py` 这种
# 直接调用方式下，src 不在 sys.path 里，会立刻 ImportError。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# .env 必须早于 `import src.config` 加载，否则 EMBEDDING_MODEL / RAG_ACTIVE_EMBEDDINGS
# 等环境变量来不及生效（src.config 在 import 时就读 os.getenv）
from dotenv import load_dotenv
load_dotenv(override=True)

# 透传 HF 镜像 / 离线开关；与 src/rag/ingest.py 顶部行为一致
for _key in ("HF_ENDPOINT", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    _val = os.getenv(_key)
    if _val:
        os.environ[_key] = _val

import src.config as config  # noqa: E402

logger = logging.getLogger("ingestion")


# ── sidecar：本脚本独家维护的入库历史 ─────────────────────────────────────────
# 设计意图：把"上次入库用的 model_name + docs_dir + 时间戳"落到一个独立 JSON 文件，
# 避免侵入 src/rag/ingest.py（产品代码保持纯净）。文件落在 CHROMA_DB_PATH 目录下，与向量库
# 同生命周期：整库被删 → 历史也理应失效；clear 命令也会一并抹掉。
#
# 文件格式 v1：
#   {
#     "version": 1,
#     "collections": {
#        "<collection_name>": {
#            "alias": "m3",
#            "model": "BAAI/bge-m3",
#            "docs_dirs": ["C:/.../datasets/data_en", "C:/.../datasets/data_zh"],   # 去重后按最近优先
#            "last_ingested_at": "2026-05-20T15:41:23"
#        }
#     }
#   }
_HISTORY_VERSION = 1
# 单 collection 最多保留多少条历史 docs_dir（去重后），多了截断防膨胀
_HISTORY_MAX_DIRS_PER_COLL = 5


def _history_path() -> Path:
    """sidecar 文件位置：CHROMA_DB_PATH/ingest_history.json。"""
    return Path(config.CHROMA_DB_PATH).resolve() / "ingest_history.json"


def _load_history() -> dict:
    """读取 sidecar；文件缺失 / 损坏一律退回空骨架，绝不抛异常打断业务。"""
    path = _history_path()
    if not path.exists():
        return {"version": _HISTORY_VERSION, "collections": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "collections" not in data:
            raise ValueError("sidecar 结构不合法")
        return data
    except Exception as e:
        logger.warning("读取 ingest_history.json 失败（忽略，按空算）：%s", e)
        return {"version": _HISTORY_VERSION, "collections": {}}


def _save_history(data: dict) -> None:
    """原子写：先写 .tmp 再 rename，避免半写状态。"""
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e:
        logger.warning("写 ingest_history.json 失败（忽略）：%s", e)


def _record_ingest(alias: str, model: str, coll: str, docs_dir: Path) -> None:
    """ingest 成功后追加一条历史，按 collection 维度合并去重。"""
    data = _load_history()
    colls: dict = data.setdefault("collections", {})
    entry = colls.get(coll) or {}
    docs_dirs: list[str] = list(entry.get("docs_dirs") or [])
    docs_str = str(docs_dir)
    # 最近优先 + 去重：把当前路径放头部，老路径若重复就先剔除
    docs_dirs = [docs_str] + [d for d in docs_dirs if d != docs_str]
    if len(docs_dirs) > _HISTORY_MAX_DIRS_PER_COLL:
        docs_dirs = docs_dirs[:_HISTORY_MAX_DIRS_PER_COLL]
    colls[coll] = {
        "alias": alias,
        "model": model,
        "docs_dirs": docs_dirs,
        "last_ingested_at": datetime.now().isoformat(timespec="seconds"),
    }
    data["version"] = _HISTORY_VERSION
    _save_history(data)


def _drop_history() -> bool:
    """清空整份 sidecar 文件（无参 clear 调用）。返回是否真的删掉。"""
    path = _history_path()
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError as e:
        logger.warning("删除 ingest_history.json 失败（忽略）：%s", e)
        return False


def _remove_history_entry(coll: str) -> bool:
    """
    从 sidecar 里只删掉某个 collection 的条目（clear -m 调用）。

    若 sidecar 删完后空了，整份文件也一并删掉，避免留个空 stub 让人误以为有数据。
    返回是否真的修改了文件。
    """
    data = _load_history()
    colls: dict = data.get("collections") or {}
    if coll not in colls:
        return False
    colls.pop(coll, None)
    if not colls:
        # 全空了：连文件一起干掉
        return _drop_history()
    data["collections"] = colls
    _save_history(data)
    return True


# ── 公共：打开 chroma 客户端 / 解析模型 ───────────────────────────────────────
def _make_chroma_client():
    """惰性 import，让 -h / status 即使没装 chromadb 也能给出更友好的报错。"""
    import chromadb
    return chromadb.PersistentClient(path=config.CHROMA_DB_PATH)


def _resolve_alias_or_die(alias: str) -> tuple[str, str, str]:
    """
    把用户传入的 alias 解析成 (alias, model_name, collection_name)。

    严格校验：未知 alias 直接 SystemExit，避免 typo 静默落进自定义 collection。
    EMBEDDING_MODELS 里没有的别名（例如自定义 hf 模型路径）也允许直通，
    与 ingest_all 保持一致语义。
    """
    if alias in config.EMBEDDING_MODELS:
        model_name, coll = config.EMBEDDING_MODELS[alias]
        return alias, model_name, coll
    if "/" in alias:
        # 直通模式：自定义 sentence-transformers 模型名
        model_name, coll = config.resolve_embedding(alias)
        return alias, model_name, coll
    valid = ", ".join(config.EMBEDDING_MODELS.keys())
    print(
        f"❌ 未知模型别名：{alias!r}（可选：{valid}，或传完整 hf 模型路径如 'BAAI/bge-m3'）",
        file=sys.stderr,
    )
    raise SystemExit(2)


# ── status ────────────────────────────────────────────────────────────────────
def _cmd_status(_args: argparse.Namespace) -> int:
    """
    打印所有内置 alias 当前的入库状态。

    数据来源分两类：
      - chunks / bm25：实时从 ChromaDB collection.count() 与 BM25 pickle 文件读
      - model / docs_dirs：本脚本维护的 sidecar JSON（CHROMA_DB_PATH/ingest_history.json）；
        从未通过本脚本 ingest 过的 collection 显示 unknown
    全程只读，不加载 embedding 模型、不触发任何下载。
    """
    chroma_path = Path(config.CHROMA_DB_PATH).resolve()
    bm25_dir = Path(config.BM25_INDEX_DIR or config.CHROMA_DB_PATH).resolve()

    # 把 alias 渲染成 "alias (model_name)"，让"模型"标签名字与显示值对得上；
    # alias 不在 EMBEDDING_MODELS 里时（罕见，比如自定义 hf 路径）就只显示 alias。
    def _render_alias(a: str) -> str:
        if a in config.EMBEDDING_MODELS:
            return f"{a} ({config.EMBEDDING_MODELS[a][0]})"
        return a

    default_alias_disp = _render_alias(config.DEFAULT_EMBEDDING_ALIAS)
    retriever_alias_disp = ", ".join(_render_alias(a) for a in config.RAG_ACTIVE_EMBEDDINGS)

    print(f"ChromaDB 路径      : {chroma_path}")
    print(f"BM25 索引目录      : {bm25_dir}")
    print(f"Embedding 默认模型 : {default_alias_disp}")
    print(f"retriever 启用模型 : {retriever_alias_disp}")
    print()

    try:
        client = _make_chroma_client()
    except Exception as e:
        print(f"❌ 打开 ChromaDB 失败: {e}", file=sys.stderr)
        return 1

    try:
        existing_names = {c.name for c in client.list_collections()}
    except Exception as e:
        logger.warning("list_collections 失败，回退为逐个尝试：%s", e)
        existing_names = set()

    from src.rag.bm25_index import get_index_path
    history_colls: dict = (_load_history().get("collections") or {})

    # 行内 docs_dir 用 list 承载：单条原样，多条按多行展开。
    # collection 不存在 / chunks=0 视为"空"，model 与 docs_dir 都打 '-'。
    rows: list[tuple[str, str, str, str, str, list[str]]] = []
    for alias, (_cfg_model, coll_name) in config.EMBEDDING_MODELS.items():
        chunks_int: int | None = None
        chunks_str = "-"
        if coll_name in existing_names or not existing_names:
            try:
                col = client.get_collection(name=coll_name)
                chunks_int = col.count()
                chunks_str = str(chunks_int)
            except Exception:
                pass
        bm25_path = get_index_path(coll_name)
        bm25_str = f"{bm25_path.stat().st_size / 1024:.0f}KB" if bm25_path.exists() else "-"

        if chunks_int is None or chunks_int <= 0:
            recorded_model = "-"
            docs_dirs_disp: list[str] = ["-"]
        else:
            entry = history_colls.get(coll_name) or {}
            recorded_model = entry.get("model") or "unknown"
            docs_dirs: list[str] = list(entry.get("docs_dirs") or [])
            docs_dirs_disp = docs_dirs if docs_dirs else ["unknown"]

        rows.append((alias, coll_name, chunks_str, bm25_str, recorded_model, docs_dirs_disp))

    headers = ("alias", "collection", "chunks", "bm25", "model", "docs_dir")
    # 列宽：前 5 列按表头/单值取 max；docs_dir 列要遍历每一行的全部目录字符串
    widths = [
        max(len(headers[0]), max(len(r[0]) for r in rows)),
        max(len(headers[1]), max(len(r[1]) for r in rows)),
        max(len(headers[2]), max(len(r[2]) for r in rows)),
        max(len(headers[3]), max(len(r[3]) for r in rows)),
        max(len(headers[4]), max(len(r[4]) for r in rows)),
        max(len(headers[5]), max((len(d) for r in rows for d in r[5]), default=0)),
    ]
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("-" * w for w in widths))
    # 多目录展开成多行：第一行带全部列，续行只在 docs_dir 列填值，其余空白对齐
    for alias, coll, chunks_s, bm25_s, model_s, dirs in rows:
        print(fmt.format(alias, coll, chunks_s, bm25_s, model_s, dirs[0]))
        for d in dirs[1:]:
            print(fmt.format("", "", "", "", "", d))
    print()
    return 0


# ── ingest ────────────────────────────────────────────────────────────────────
def _cmd_ingest(args: argparse.Namespace) -> int:
    """
    幂等增量入库（底层就是 src.rag.ingest.ingest_all）：

      - 新文件 → 解析 / 分块 / embed / upsert
      - 内容变化（content_sha1 变了）→ 删旧 chunks → 重新 embed
      - 内容未变 → 直接跳过
      - datasets/ 中已删除的文件 → 不回收孤儿 chunks（如需彻底清理请 clear 后重入）
    """
    alias, model_name, coll_name = _resolve_alias_or_die(args.model)
    docs_dir = Path(args.docs_dir).resolve()
    if not docs_dir.exists():
        print(f"❌ 文档目录不存在: {docs_dir}", file=sys.stderr)
        return 1
    if not docs_dir.is_dir():
        print(f"❌ 路径不是目录: {docs_dir}", file=sys.stderr)
        return 1

    print("⏳ 模式: ingest (幂等增量；content_sha1 一致则跳过)")
    print(f"   docs_dir   : {docs_dir}")
    print(f"   alias      : {alias}")
    print(f"   model      : {model_name}")
    print(f"   collection : {coll_name}")
    print()

    # 此处才真正 import，触发 sentence-transformers 加载，前面 status/clear 不受影响
    from src.rag.ingest import ingest_all
    try:
        ingest_all(docs_dir=str(docs_dir), model=alias)
    except Exception as e:
        print(f"❌ 入库失败: {e}", file=sys.stderr)
        return 1

    # ingest 成功后落 sidecar；失败抛错时不写，保证历史只反映成功入库的目录
    _record_ingest(alias=alias, model=model_name, coll=coll_name, docs_dir=docs_dir)
    print(f"📝 已更新 sidecar: {_history_path()}")
    return 0


# ── clear ─────────────────────────────────────────────────────────────────────
def _drop_collection_and_bm25(client, coll_name: str) -> tuple[bool, bool]:
    """
    清空单个 collection：drop chroma collection + 删 BM25 pickle。

    返回 (chroma_dropped, bm25_dropped)，便于上层汇总打印。
    任一步骤失败仅记日志，不抛异常——清空本来就是尽力而为。
    """
    from src.rag.bm25_index import get_index_path

    chroma_dropped = False
    try:
        client.delete_collection(name=coll_name)
        chroma_dropped = True
    except Exception as e:
        # collection 不存在时 chroma 也会抛，视为已清
        msg = str(e).lower()
        if "does not exist" in msg or "not found" in msg:
            logger.info("collection %r 本来就不存在，跳过", coll_name)
        else:
            logger.warning("删除 collection %r 失败: %s", coll_name, e)

    bm25_dropped = False
    bm25_path = get_index_path(coll_name)
    if bm25_path.exists():
        try:
            bm25_path.unlink()
            bm25_dropped = True
        except OSError as e:
            logger.warning("删除 BM25 索引 %s 失败: %s", bm25_path, e)
    return chroma_dropped, bm25_dropped


# ── 孤儿 segment 清理 ────────────────────────────────────────────────────────
# Chroma 的 PersistentClient.delete_collection() 只改 chroma.sqlite3 里的 catalog
# （collections / segments 两张表），不会 unlink 已落盘的 vector segment 目录。
# 每次 clear → ingest 循环都会在磁盘上多留几个 UUID 目录，越攒越多。下面这组工具
# 就是把"sqlite 里已经不在的 vector 段"对应的持久化根下 <uuid>/ 目录物理删掉。
def _list_uuid_dirs(chroma_root: Path) -> list[Path]:
    """列出 Chroma 持久化根下所有 UUID 命名的子目录（即 Chroma 的 segment 目录）。

    严格走 `_UUID_DIR_RE` 匹配，避免误删 bm25_*.pkl / chroma.sqlite3 等 sibling 文件。
    """
    if not chroma_root.is_dir():
        return []
    return [p for p in chroma_root.iterdir() if p.is_dir() and _UUID_DIR_RE.match(p.name)]


def _get_live_vector_segment_ids(chroma_root: Path) -> set[str] | None:
    """从 chroma.sqlite3 反查所有活跃 VECTOR 段的 UUID。

    Chroma 的 segments 表里每个 collection 通常有 2 条记录：
      - scope=VECTOR    → HNSW 段，对应持久化根下 <uuid>/ 目录（要落盘）
      - scope=METADATA  → 直接存在 sqlite 内部表，不占目录
    判定"磁盘 UUID 目录是否孤儿"，比较 VECTOR 段集合即可。

    安全策略：sqlite 不存在或读失败返回 None，调用方据此放弃清理，绝不"找不到 →
    全删"。这样即使 sqlite 路径配错也不会误伤。
    """
    import sqlite3
    sqlite_path = chroma_root / "chroma.sqlite3"
    if not sqlite_path.exists():
        return None
    try:
        # uri=file:...?mode=ro 只读打开，避免与同进程 chroma client 写冲突
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM segments WHERE scope = 'VECTOR'")
            return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        logger.warning("读取 chroma.sqlite3 反查活跃 segment 失败（跳过孤儿清理）: %s", e)
        return None


def _cleanup_orphan_segments(chroma_root: Path) -> list[str]:
    """把持久化根下不再被任何 collection 引用的 VECTOR 段目录物理删除。

    返回被删的目录名列表（短 UUID 字符串）。

    调用时机：单 alias clear 成功之后。整库 clear 用 `_nuke_chroma_root` 直接 rm -rf，
    无需走这条路径。
    """
    live = _get_live_vector_segment_ids(chroma_root)
    if live is None:
        # 读不到 sqlite → 保守起见放弃清理，宁可留残留也别误删
        return []
    removed: list[str] = []
    for d in _list_uuid_dirs(chroma_root):
        if d.name in live:
            continue
        try:
            shutil.rmtree(d)
            removed.append(d.name)
        except OSError as e:
            # Windows 上若有进程还持着 HNSW mmap 句柄会失败；记 warning 不抛
            logger.warning("删除孤儿 segment 目录 %s 失败（可能被占用）: %s", d, e)
    return removed


def _nuke_chroma_root(chroma_root: Path | None = None) -> tuple[bool, Path]:
    """全量 clear 用：物理删除整个 Chroma 持久化根目录。

    刻意不通过 chroma client API 走 delete_collection 再一个个清——直接 rm -rf
    最干净，能一次抹掉 catalog / 当前 segments / 历史孤儿 / sidecar / WAL 等全部文件，
    Windows 上也省去文件锁问题（前提：调用前没开 chroma client）。

    chroma_root 可显式传入（便于测试 / 自定义路径）；不传则取 config.CHROMA_DB_PATH。
    返回 (是否真的删了, chroma_root 路径)。目录不存在视为已删（True）。
    """
    if chroma_root is None:
        chroma_root = Path(config.CHROMA_DB_PATH).resolve()
    if not chroma_root.exists():
        return True, chroma_root
    try:
        shutil.rmtree(chroma_root)
        return True, chroma_root
    except OSError as e:
        logger.warning(
            "rm -rf %s 失败: %s（如有 main.py 等进程占用，请先关闭再重试）",
            chroma_root, e,
        )
        return False, chroma_root


def _collection_chunks(client, coll_name: str) -> int | None:
    """安静读 collection.count()；collection 不存在或读失败返回 None。

    单 alias clear 完毕后用它确认"是否真的清空了"——为 None 或 0 才触发孤儿清理。
    """
    try:
        col = client.get_collection(name=coll_name)
        return col.count()
    except Exception:
        return None


def _cmd_clear(args: argparse.Namespace) -> int:
    """
    清空 collection + BM25 索引 + sidecar 历史，并清理磁盘上孤儿 segment 目录。

    两种模式：
      - 不带 -m（全量）：直接 rm -rf 整个 Chroma 持久化根目录，等价"出厂重置"。
                        额外清掉 BM25 索引文件（可能落在 BM25_INDEX_DIR 而非持久化根）
                        与 sidecar，不会再留任何残留。
      - 带 -m alias（单库）：通过 chroma client 走 delete_collection；删完后用 sqlite
                            反查"剩余活跃 VECTOR 段集合"，把磁盘上不在集合里的 UUID 目录
                            全部 rm -rf——这恰好包含刚被 drop 的那个 segment 目录，以及
                            历史遗留的孤儿。sidecar 只移除对应条目。

    强制策略：始终列出待清单 + 一次 yes 二次确认，无 `--yes` 跳过——清空是高破坏性
    操作，多打几个字符换一份心安。
    """
    target_alias: str | None = getattr(args, "model", None)
    chroma_root = Path(config.CHROMA_DB_PATH).resolve()

    # ── 1. 组装待删清单 + 二次确认 ─────────────────────────────────────────────
    if target_alias:
        alias, _model, coll = _resolve_alias_or_die(target_alias)
        targets: list[tuple[str, str]] = [(alias, coll)]
        scope_label = f"指定 alias={alias} (collection={coll})"
    else:
        targets = [(a, c) for a, (_m, c) in config.EMBEDDING_MODELS.items()]
        scope_label = f"全部 {len(targets)} 个 collection（rm -rf 整个向量库目录）"

    print(f"⚠️  即将清空 {scope_label}（向量 + BM25 索引 + sidecar 历史，不可恢复）：")
    for alias, coll in targets:
        print(f"    - alias={alias:<4}  collection={coll}")
    if not target_alias:
        print(f"    + 物理删除目录 {chroma_root}（含历史孤儿 segment）")
    confirm = input("\n请输入 yes 确认清空（其他任何输入都取消）: ").strip().lower()
    if confirm != "yes":
        print("已取消。")
        return 0

    # ── 2a. 全量分支：不开 chroma client，直接 rm -rf 整库 ─────────────────────
    # 整库 rm 比一个个 delete_collection 干净得多：catalog / 当前 / 历史 segments /
    # WAL / sidecar 一锅端，Windows 上也没有"client 还没释放 sqlite 句柄"的烦恼。
    if not target_alias:
        # BM25 索引可能不在持久化根里（BM25_INDEX_DIR 独立目录），先单独干掉
        from src.rag.bm25_index import get_index_path
        for alias, coll in targets:
            p = get_index_path(coll)
            if p.exists():
                try:
                    p.unlink()
                    print(f"  [✓bm25] {alias:<4} → {coll}  ({p})")
                except OSError as e:
                    print(f"  [·bm25] {alias:<4} → {coll}  失败: {e}")
            else:
                print(f"  [·bm25] {alias:<4} → {coll}  不存在")

        nuked, root = _nuke_chroma_root(chroma_root)
        flag = "✓" if nuked else "·"
        print(f"  [{flag}chroma] rm -rf {root}")

        # sidecar 默认就落在持久化根内，整库 rm 已经一并清掉；
        # 若用户改过路径或目录还在，再兜底删一次条目（_drop_history 自己幂等）
        if _history_path().exists():
            _drop_history()
        print(f"  [✓sidecar] 已随向量库整库清除")

        if nuked:
            print(f"✅ 清空完成（{root} 已物理删除，无任何残留）。")
            return 0
        print(f"⚠️  rm -rf 部分失败；请关闭占用进程后重跑，或手动删除 {root}。")
        return 1

    # ── 2b. 单 alias 分支：API drop + 孤儿 segment 清理 ───────────────────────
    try:
        client = _make_chroma_client()
    except Exception as e:
        print(f"❌ 打开 ChromaDB 失败: {e}", file=sys.stderr)
        return 1

    alias, coll = targets[0]
    chroma_ok, bm25_ok = _drop_collection_and_bm25(client, coll)
    flag_chroma = "✓" if chroma_ok else "·"
    flag_bm25 = "✓" if bm25_ok else "·"
    print(f"  [{flag_chroma}chroma {flag_bm25}bm25] {alias} → {coll}")

    # 验证：collection 真的没了 / 或 chunks=0 才触发孤儿目录清理（按用户需求）。
    # 残留判定故意"宽松"：只要不剩 chunks，就把磁盘上所有不被引用的 UUID 目录扫一遍
    # 顺手清掉——这天然包含刚 drop 掉的那个 vector segment，以及历史遗留。
    remaining = _collection_chunks(client, coll)
    if remaining is None or remaining == 0:
        orphans = _cleanup_orphan_segments(chroma_root)
        if orphans:
            print(f"  [✓segments] 清理 {len(orphans)} 个孤儿目录:")
            for d in orphans:
                print(f"      - {d}")
        else:
            print("  [·segments] 无孤儿目录可清理（磁盘已干净）")
    else:
        print(
            f"  [⚠segments] {coll} 仍剩 {remaining} chunks（drop 未生效？），"
            f"跳过孤儿清理以防误删"
        )

    sidecar_changed = _remove_history_entry(coll)
    print(f"  [{'✓' if sidecar_changed else '·'}sidecar] 移除条目 {coll}")
    print("✅ 清空完成。")
    return 0


# ── argparse 装配 ─────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    epilog = textwrap.dedent(
        """\
        示例：
          python tools/rag_cli.py status                                查看当前各 collection 状态
          python tools/rag_cli.py ingest                                幂等增量入库（默认 datasets/data_en + 模型）
          python tools/rag_cli.py ingest -d ./datasets/data_zh -m zh    中文库
          python tools/rag_cli.py ingest -m m3                          多语言单库
          python tools/rag_cli.py clear                                 一键清空全部 collection（需输入 yes）
          python tools/rag_cli.py clear -m m3                           只清空 m3 库（与 ingest -m 含义对齐）

        典型流程：
          首次启动 AgentA 前：
            1) python tools/download_models.py 3          # 拉 bge-m3
            2) python tools/rag_cli.py ingest -m m3       # 把 ./datasets/data_en 灌进 kb_m3
            3) python tools/rag_cli.py status             # 验证 chunks > 0、model/docs_dir 已记录
            4) python main.py                             # 启动 CLI

          单 alias 重建（改了 chunk_size 等切分参数后）：
            1) python tools/rag_cli.py clear -m m3        # 只抹 m3 库
            2) python tools/rag_cli.py ingest -m m3       # 重新灌
        """
    )
    parser = argparse.ArgumentParser(
        prog="rag_cli",
        description="RAG 知识库入库工具：status（读）/ ingest（写）/ clear（抹）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    sub = parser.add_subparsers(dest="cmd", metavar="{status,ingest,clear}")

    # status
    p_status = sub.add_parser(
        "status",
        help="只读：打印每个 alias 的真实入库状态（model / docs_dir 来自 collection.metadata）。",
        description=(
            "只读：从 ChromaDB collection.metadata 读取上次 ingest 时记录的"
            "真实 model_name 与 docs_dir。未记录的旧库显示 unknown，重跑 ingest 即可补全。"
        ),
    )
    p_status.set_defaults(func=_cmd_status)

    # ingest
    p_ing = sub.add_parser(
        "ingest",
        help="幂等增量入库（content_sha1 一致的文件自动跳过；新增/变更则重 embed）。",
        description=(
            "扫描 datasets/ 目录并 upsert 到对应 alias 的 collection。"
            "本身已是幂等增量，无需 append/rebuild 的概念区分。"
        ),
    )
    p_ing.add_argument(
        "-d", "--docs-dir",
        default=config.DOCS_DIR,
        help=f"文档目录路径（默认 {config.DOCS_DIR}，来自 .env DOCS_DIR）",
    )
    p_ing.add_argument(
        "-m", "--model",
        default=config.DEFAULT_EMBEDDING_ALIAS,
        help=(
            "Embedding 模型别名（en/zh/m3 或自定义 hf 路径），"
            f"默认 {config.DEFAULT_EMBEDDING_ALIAS}（来自 .env EMBEDDING_MODEL）。"
            "不同 alias 写入不同 collection，互不干扰。"
        ),
    )
    p_ing.set_defaults(func=_cmd_ingest)

    # clear
    p_clr = sub.add_parser(
        "clear",
        help="清空 collection + BM25 + sidecar + 磁盘残留；不带 -m 全清，带 -m 只清指定 alias（均需 yes 确认）。",
        description=(
            "清空 ChromaDB collection、对应 bm25_<collection>.pkl 索引以及 sidecar 历史条目，"
            "并同步清理磁盘上 Chroma 不会自己回收的 vector segment 目录残留。\n"
            "  - 不带 -m：直接 rm -rf 整个 Chroma 持久化根目录（最干净，含历史孤儿、WAL、catalog 全部清零），"
            "BM25 索引若不在持久化根内则单独删除，sidecar 一并清除。\n"
            "  - 带 -m alias：通过 chroma 客户端 drop 单个 collection；若 collection 确认已空，"
            "用 sqlite 反查活跃 VECTOR 段集合，把磁盘上不在集合里的 UUID 目录全部 rm -rf"
            "（含刚 drop 的那个，以及历史遗留的孤儿）。sidecar 只移除对应条目。\n"
            "执行前都会先列出待删清单，要求输入 yes 后才真正删除，不可恢复。"
            "若想全量重建，clear 之后跑 ingest 即可。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_clr.add_argument(
        "-m", "--model",
        default=None,
        help=(
            "可选；要清空的 alias（en/zh/m3 或自定义 hf 路径）。"
            "不传则清空全部 alias。与 ingest 的 -m 含义对齐。"
        ),
    )
    p_clr.set_defaults(func=_cmd_clear)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，遇到 ⚠️ ✓ ✅ 等字符会抛 UnicodeEncodeError。
    # 与 tools/rag_eval/runner.py 同源修复：把 stdout/stderr 强制 reconfigure 成 utf-8。
    # Python 3.7+ 才有 reconfigure；老解释器忽略即可（不影响主流程）。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # 静音第三方库的冗余日志，与 main.py 一致
    for _noisy in ("httpx", "httpcore", "openai", "chromadb", "sentence_transformers"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
