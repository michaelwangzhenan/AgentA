"""UI 后端启动入口：用 `uvicorn.run` + 统一 log_config 拉起 FastAPI app。

`ui.ps1` 调 `python -m src.api.run` 启动。相比直接 `uvicorn src.api.main:app`，好处是
日志由 RotatingFileHandler 直接写 `logs/uvicorn.log`（带 `[APP]`/`[ACCESS]` 前缀、按大小
滚动、保留备份），不再依赖 shell 重定向，也让 uvicorn 自身日志和业务日志格式一致。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# .env 必须在 import src.config 之前加载
load_dotenv(override=True)

import uvicorn  # noqa: E402

import src.config as config  # noqa: E402
from src.services.log_setup import build_uvicorn_log_config  # noqa: E402

# 与原 ui.ps1 行为对齐：监听 127.0.0.1:8000，--reload 监视 src/
_HOST = "127.0.0.1"
_PORT = 8000
_LOG_FILE = "./logs/uvicorn.log"


def main() -> None:
    Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    log_config = build_uvicorn_log_config(
        log_file=_LOG_FILE,
        level=config.LOG_LEVEL,
        max_bytes=config.LOG_MAX_BYTES,
        backup_count=config.LOG_BACKUP_COUNT,
    )
    uvicorn.run(
        "src.api.main:app",
        host=_HOST,
        port=_PORT,
        reload=True,
        reload_dirs=["src"],
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
