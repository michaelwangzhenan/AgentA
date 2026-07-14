"""入库可观测性与并发保护。"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_semaphore: threading.Semaphore | None = None
_sem_lock = threading.Lock()
_sem_limit = 0


def flush_log_handlers() -> None:
    """关键入库日志后立即刷盘，降低死机时丢日志风险。"""
    root = logging.getLogger()
    for handler in root.handlers:
        handler.flush()
        stream = getattr(handler, "stream", None)
        if stream is not None and hasattr(stream, "flush"):
            stream.flush()


def process_rss_mb() -> int:
    """当前进程 RSS（MiB）；读不到返回 -1。"""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
            proc = ctypes.WinDLL("kernel32").GetCurrentProcess()
            if ctypes.WinDLL("psapi").GetProcessMemoryInfo(
                proc,
                ctypes.byref(counters),
                counters.cb,
            ):
                return int(counters.WorkingSetSize // (1024 * 1024))
            return -1
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, AttributeError, ValueError):
        pass
    return -1


def system_avail_mb() -> int:
    """系统可用内存 MemAvailable（MiB）；读不到返回 -1。"""
    try:
        if os.name == "nt":
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys // (1024 * 1024))
            return -1
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, AttributeError, ValueError):
        pass
    return -1


def _get_semaphore(limit: int) -> threading.Semaphore:
    global _semaphore, _sem_limit
    with _sem_lock:
        if _semaphore is None or _sem_limit != limit:
            _semaphore = threading.Semaphore(max(1, limit))
            _sem_limit = limit
        return _semaphore


@contextmanager
def ingest_slot() -> Iterator[None]:
    """限制同时执行的入库任务数。"""
    import src.config as cfg

    limit = max(1, int(cfg.INGEST_MAX_CONCURRENT))
    sem = _get_semaphore(limit)
    logger.info("[ingest] 请求入库槽位 max=%d", limit)
    flush_log_handlers()
    sem.acquire()
    try:
        logger.info("[ingest] 获得入库槽位")
        flush_log_handlers()
        yield
    finally:
        sem.release()


@dataclass
class IngestProbe:
    """单文件入库各阶段的可观测性上下文。"""

    file_path: Path
    rel_path: str
    file_bytes: int = 0
    unzip_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.file_bytes:
            try:
                self.file_bytes = self.file_path.stat().st_size
            except OSError:
                self.file_bytes = 0

    def set_unzip_bytes(self, value: int) -> None:
        self.unzip_bytes = value

    def _emit(
        self,
        phase: str,
        event: str,
        *,
        chunks: int = 0,
        duration_ms: float = 0,
        status: str = "",
        error: str = "",
    ) -> None:
        rss = process_rss_mb()
        avail = system_avail_mb()
        unzip = self.unzip_bytes if self.unzip_bytes is not None else -1
        parts = [
            f"[ingest] phase={phase}",
            f"event={event}",
            f"file={self.rel_path}",
            f"file_bytes={self.file_bytes}",
            f"unzip_bytes={unzip}",
            f"chunks={chunks}",
            f"duration_ms={duration_ms:.0f}",
            f"rss_mb={rss}",
            f"avail_mb={avail}",
        ]
        if status:
            parts.append(f"status={status}")
        if error:
            parts.append(f"error={error}")
        message = " ".join(parts)
        if event == "error":
            logger.error(message)
        else:
            logger.info(message)
        flush_log_handlers()

    def file_start(self) -> None:
        self._emit("file", "start")

    def file_done(self, status: str, chunks: int) -> None:
        self._emit("file", "done", chunks=chunks, status=status)

    def file_error(self, error: str) -> None:
        self._emit("file", "error", error=error)

    @contextmanager
    def track(self, phase: str) -> Iterator[None]:
        started = time.monotonic()
        self._emit(phase, "start")
        try:
            yield
            self._emit(phase, "done", duration_ms=(time.monotonic() - started) * 1000)
        except Exception as exc:
            self._emit(
                phase,
                "error",
                duration_ms=(time.monotonic() - started) * 1000,
                error=str(exc),
            )
            raise


def probe_for_docx(file_path: Path, rel_path: str) -> IngestProbe:
    """构造探针并尽量填入 DOCX 解压大小。"""
    probe = IngestProbe(file_path=file_path, rel_path=rel_path)
    if file_path.suffix.lower() == ".docx":
        try:
            from src.rag.parser import measure_docx_uncompressed_size

            probe.set_unzip_bytes(measure_docx_uncompressed_size(file_path))
        except Exception:
            pass
    return probe
