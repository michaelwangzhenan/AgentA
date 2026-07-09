"""UI 后端启动入口：用 `uvicorn.run` + 统一 log_config 拉起 FastAPI app。

`tools/dev_server.ps1` 调 `python -m src.api.run` 启动。相比直接 `uvicorn src.api.main:app`，好处是
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

# 与原 dev_server.ps1 行为对齐：监听 127.0.0.1:8000，--reload 监视 src/
_HOST = "127.0.0.1"
_PORT = 8000
_LOG_FILE = "./logs/uvicorn.log"
# 本地开发默认开热重载（改代码立即生效）；生产部署（VPS systemd）在 .env 里设
# UVICORN_RELOAD=false 关掉——reload 会多起一个文件监视子进程，且线上代码只在
# 手动 restart 时才应该变，不需要监视磁盘变化。不进 src/config.py 统一配置注册表
# / UI：这是进程启动参数，不是运行期可调的业务配置。
_RELOAD = os.getenv("UVICORN_RELOAD", "true").strip().lower() not in ("0", "false", "no")


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
        reload=_RELOAD,
        reload_dirs=["src"] if _RELOAD else None,
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
