#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按用户名导出对话记录到 history/<用户名>_chat.md。

用法：
    python tools/cli/getchat.py <用户名>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

from src.services.getchat_export import UserNotFoundError, write_user_chat  # noqa: E402


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> None:
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        prog="getchat",
        description="从数据库导出指定用户的全部对话到 history/<用户名>_chat.md。",
    )
    parser.add_argument("username", help="用户名（大小写不敏感）")
    args = parser.parse_args(argv)

    try:
        path = write_user_chat(args.username)
    except UserNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"已写入 {path.resolve()}")


if __name__ == "__main__":
    main()
