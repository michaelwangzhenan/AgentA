"""进程级 Chroma PersistentClient 单例 —— ingest / retriever / 管理巡检共用。

避免同一 CHROMA_DB_PATH 被多处各自 `PersistentClient()`，重复持有 SQLite 连接与内部缓存。
"""
from __future__ import annotations

import gc
import logging
import threading
from pathlib import Path
from typing import Any

import src.config as config

try:
    import chromadb
except ImportError:  # pragma: no cover — 未装 chromadb 时 get_chroma_client 再报错
    chromadb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_client: Any = None
_client_lock = threading.Lock()
_client_path: str | None = None


def chroma_db_path() -> str:
    """解析 ``CHROMA_DB_PATH`` 为绝对路径（与 ``db_inspect.chroma_root`` 口径一致）。"""
    p = Path(config.CHROMA_DB_PATH)
    if p.is_absolute():
        return str(p.resolve())
    return str((_PROJECT_ROOT / p).resolve())


def get_chroma_client() -> Any:
    """懒加载并复用进程级 ``PersistentClient``（双检锁）。"""
    global _client, _client_path
    path = chroma_db_path()
    if _client is not None and _client_path == path:
        return _client
    with _client_lock:
        if _client is not None and _client_path == path:
            return _client
        if chromadb is None:
            raise ImportError("chromadb 未安装，无法创建 PersistentClient")
        if _client is not None:
            _client = None
            _client_path = None
        _client = chromadb.PersistentClient(path=path)
        _client_path = path
        logger.debug("[Chroma] PersistentClient 已创建 path=%s", path)
        return _client


def close_chroma_client() -> None:
    """释放单例引用；应用 shutdown 或测试 teardown 时调用。"""
    global _client, _client_path
    with _client_lock:
        _client = None
        _client_path = None
    gc.collect()
