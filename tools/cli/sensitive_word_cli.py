#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
敏感词库构建工具 — 从 houbb/sensitive-word-data 裁剪生成 resources/sensitive_words/

开发环境人工执行，审核后随代码发布；线上不联网更新。

CLI 用法：
    python tools/cli/sensitive_word_cli.py -h
    python tools/cli/sensitive_word_cli.py status
    python tools/cli/sensitive_word_cli.py build
    python tools/cli/sensitive_word_cli.py build --max-words 5000
    python tools/cli/sensitive_word_cli.py build --tags 0,4          # 仅政治+违法
    python tools/cli/sensitive_word_cli.py build --extra resources/sensitive_words/extra.tsv
    python tools/cli/sensitive_word_cli.py build --source-dir ./tmp/upstream   # 离线
    python tools/cli/sensitive_word_cli.py build --proxy http://127.0.0.1:7890
    python tools/cli/sensitive_word_cli.py build --dry-run

上游标签（houbb 内置）：
    0 politics   政治
    1 drugs      毒品
    2 porn       色情
    3 gambling   赌博
    4 illegal    违法

输出文件（写入 --out-dir，默认 resources/sensitive_words/）：
    deny.tsv      词条\\t分类
    allow.txt     白名单（来自上游 sensitive_word_allow.txt）
    metadata.json 来源版本与生成时间
    LICENSE       Apache-2.0（来自上游）

extra.tsv 中人工补充的词条始终保留，不占上游配额优先级；上游词条在剩余配额内按分类优先级裁剪。
trad_simp.tsv 不在本脚本改写，需单独维护。

代理：--proxy 优先；未指定时读取 HTTPS_PROXY / HTTP_PROXY 环境变量。
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.services.sensitive_word_build import (  # noqa: E402
    TAG_NAMES,
    build_word_pack,
    configure_http_proxy,
    load_current_stats,
    proxy_port_warning,
)

_DEFAULT_OUT = _PROJECT_ROOT / "resources" / "sensitive_words"
_DEFAULT_EXTRA = _DEFAULT_OUT / "extra.tsv"


def _cmd_status(args: argparse.Namespace) -> int:
    info = load_current_stats(Path(args.out_dir))
    meta = info.get("metadata") or {}
    print(f"目录: {info['dir']}")
    print(f"deny.tsv: {'有' if info['deny_exists'] else '无'}，词条数 {info['word_count']}")
    if meta:
        print(f"版本: {meta.get('version', '?')}")
        print(f"上游: {meta.get('upstream', '?')} @ {meta.get('upstream_commit', '?')}")
        print(f"生成: {meta.get('generated_at', '?')}")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    include_tags: set[str] | None = None
    if args.tags:
        include_tags = {t.strip() for t in args.tags.split(",") if t.strip()}
        unknown = include_tags - set(TAG_NAMES)
        if unknown:
            print(f"未知标签编号: {', '.join(sorted(unknown))}，合法值: {', '.join(TAG_NAMES)}")
            return 2

    extra = Path(args.extra) if args.extra else None
    source_dir = Path(args.source_dir) if args.source_dir else None
    out_dir = Path(args.out_dir)

    print(f"输出目录: {out_dir}")
    if source_dir:
        print(f"本地源: {source_dir}")
    else:
        print("从 GitHub 拉取 houbb/sensitive-word-data …")
    proxy_cfg = configure_http_proxy(getattr(args, "proxy", None))
    if proxy_cfg and not source_dir:
        print(f"代理: {proxy_cfg['https']}")
        warn = proxy_port_warning(proxy_cfg["https"])
        if warn:
            print(f"提示: {warn}")
    if extra and extra.is_file():
        print(f"人工补充: {extra}")
    if include_tags:
        names = [f"{t}={TAG_NAMES[t]}" for t in sorted(include_tags, key=int)]
        print(f"标签筛选: {', '.join(names)}")
    print(f"上限: {args.max_words} 词")

    stats = build_word_pack(
        out_dir,
        source_dir=source_dir,
        extra_path=extra,
        max_words=args.max_words,
        include_tags=include_tags,
        include_dict_orphans=args.include_dict_orphans,
        dry_run=args.dry_run,
        proxy=args.proxy,
    )

    if args.dry_run:
        print(f"[dry-run] 将写入 {stats.total} 词（extra {stats.extra_count}，上游 {stats.upstream_selected}）")
        return 0

    print(f"完成：共 {stats.total} 词（人工 {stats.extra_count}，上游 {stats.upstream_selected}）")
    if stats.by_category:
        print("分类分布:")
        for cat, n in sorted(stats.by_category.items(), key=lambda x: (-x[1], x[0])):
            print(f"  {cat}: {n}")
    print("重启后端后词库生效。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sensitive_word_cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="从 houbb/sensitive-word-data 裁剪生成本地敏感词库",
        epilog=textwrap.dedent(
            """
            示例:
              python tools/cli/sensitive_word_cli.py build
              python tools/cli/sensitive_word_cli.py build --tags 0,4 --max-words 3000
              python tools/cli/sensitive_word_cli.py build --proxy http://127.0.0.1:7890
              set HTTPS_PROXY=http://127.0.0.1:7890 && python tools/cli/sensitive_word_cli.py build
            """
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="查看当前词库状态")
    p_status.add_argument("--out-dir", default=str(_DEFAULT_OUT))
    p_status.set_defaults(func=_cmd_status)

    p_build = sub.add_parser("build", help="拉取上游并生成 deny.tsv / allow.txt / metadata.json")
    p_build.add_argument("--out-dir", default=str(_DEFAULT_OUT), help=f"输出目录（默认 {_DEFAULT_OUT}）")
    p_build.add_argument(
        "--source-dir",
        default=None,
        help="本地克隆的 houbb/sensitive-word-data 根目录（离线模式）",
    )
    p_build.add_argument(
        "--extra",
        default=str(_DEFAULT_EXTRA),
        help=f"人工补充词条 TSV（默认 {_DEFAULT_EXTRA}，不存在则跳过）",
    )
    p_build.add_argument("--max-words", type=int, default=5000, help="deny.tsv 总词条上限（默认 5000）")
    p_build.add_argument(
        "--tags",
        default=None,
        help="仅保留指定标签，逗号分隔，如 0,4（0政治 1毒品 2色情 3赌博 4违法）",
    )
    p_build.add_argument(
        "--include-dict-orphans",
        action="store_true",
        help="把 dict 中无标签的词条也纳入候选（分类 misc，仍受 --max-words 限制）",
    )
    p_build.add_argument(
        "--proxy",
        default=None,
        help="HTTP(S) 代理，如 http://127.0.0.1:7890 或 127.0.0.1:7890；未指定时读 HTTPS_PROXY",
    )
    p_build.add_argument("--dry-run", action="store_true", help="只统计不写入")
    p_build.set_defaults(func=_cmd_build)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
