"""
统一日志配置：
CLI（main.py）和 UI 后端（src/api/main.py + src/api/run.py）两个入口共用的格式/级别/上下文注入

- TaggedFormatter：业务日志加 [APP] 前缀、uvicorn 访问日志加 [ACCESS] 前缀，统一带日期
- ContextFilter + contextvar：把 session_id / request_id 注入每条日志
- setup_cli_logging() / build_uvicorn_log_config()：两个入口各自的配置出口
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar

# 业务日志格式：日期时间 + [APP] + 级别 + 上下文(session/request) + 文件:行号 + 消息
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
APP_FORMAT = (
    "%(asctime)s [APP] [%(levelname)s] [s:%(session_id)s r:%(request_id)s] "
    "%(filename)s:%(lineno)d - %(message)s"
)
# 访问日志：uvicorn.access 的 record.getMessage() 已经是完整一行，直接套前缀即可
ACCESS_FORMAT = '%(asctime)s [ACCESS] [r:%(request_id)s] %(message)s'

# 第三方库的冗余日志统一压到 WARNING，避免淹没业务日志
_NOISY_LOGGERS = ("httpx", "httpcore", "openai", "chromadb", "sentence_transformers")

_session_id: ContextVar[str] = ContextVar("session_id", default="-")
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_session_id(value: str | None) -> None:
    _session_id.set(value or "-")


def set_request_id(value: str | None) -> None:
    _request_id.set(value or "-")


def get_session_id() -> str:
    return _session_id.get()


class ContextFilter(logging.Filter):
    """给每条日志补 session_id / request_id 字段（缺省 '-'），供 formatter 使用。

    attach 到 handler 上即可，所有经过该 handler 的记录（含 uvicorn 自身的）都会拿到字段，
    避免 formatter 引用未定义字段时抛 KeyError。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "session_id"):
            record.session_id = _session_id.get()
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return True


class TaggedFormatter(logging.Formatter):
    """按来源选格式：uvicorn.access 走 [ACCESS]，其余走 [APP]。

    一个 formatter 同时覆盖两类，使它们能共用同一个文件 handler（避免两个 handler
    写同一文件时滚动改名互相打架）。
    """

    def __init__(self) -> None:
        super().__init__(datefmt=DATE_FORMAT)
        self._app = logging.Formatter(APP_FORMAT, DATE_FORMAT)
        self._access = logging.Formatter(ACCESS_FORMAT, DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "uvicorn.access":
            return self._access.format(record)
        return self._app.format(record)


def resolve_level(name: str | None = None) -> tuple[int, str, bool]:
    """解析 LOG_LEVEL：返回 (级别值, 规范化名, 是否合法)。非法时级别给 INFO。"""
    resolved = (name or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, resolved, None)
    if not isinstance(level, int):
        return logging.INFO, resolved, False
    return level, resolved, True


def quiet_noisy_loggers() -> None:
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def setup_cli_logging(level_name: str | None = None) -> None:
    """配置 CLI 进程的 root logger。

    需在 sys.stderr 被 _Tee 包装之后调用，这样 handler 绑定到 tee、日志才能进文件。
    """
    level, _, ok = resolve_level(level_name)
    if not ok:
        print(
            f"[WARN] 未知 LOG_LEVEL '{level_name}'，降级使用 INFO"
            "（可选值：DEBUG / INFO / WARNING / ERROR / CRITICAL）"
        )
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()  # 构造时绑定当前 sys.stderr（已是 _Tee）
    handler.setFormatter(TaggedFormatter())
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    quiet_noisy_loggers()


def build_uvicorn_log_config(
    log_file: str,
    level: str,
    max_bytes: int,
    backup_count: int,
) -> dict:
    """构造传给 uvicorn.run(log_config=...) 的 dictConfig。

    uvicorn 自身 / 访问日志 / root（含 src.* 业务日志）全挂到同一个
    RotatingFileHandler：按大小滚动、保留备份、统一带前缀与时间
    """
    resolved, _, _ = resolve_level(level)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "ctx": {"()": "src.services.log_setup.ContextFilter"},
        },
        "formatters": {
            "tagged": {"()": "src.services.log_setup.TaggedFormatter"},
        },
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": log_file,
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "formatter": "tagged",
                "filters": ["ctx"],
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": resolved, "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": resolved, "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": resolved, "propagate": False},
            **{name: {"level": "WARNING"} for name in _NOISY_LOGGERS},
        },
        "root": {"handlers": ["file"], "level": resolved},
    }
