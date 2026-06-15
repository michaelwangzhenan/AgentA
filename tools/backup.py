#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行时数据备份 / 还原 CLI：把可重用数据打成单个带时间戳的 zip，支持还原回项目。

备份范围（见 docs/v_1_0/interation/iter_18_runtime.md §3.3，最终选择 A B C E F K）：
    A 敏感配置  B 运行期 DB  C 向量库 / 索引  E 黄金集  F 评估报告  K 编辑器配置

用法：
    python tools/backup.py backup  --out <dir> [--exclude C,F]
    python tools/backup.py restore --zip <path> [--force]
    python tools/backup.py list    --out <dir>

读写逻辑统一在 src/runtime_backup.py（CLI 与 /admin/backup API 共用），本文件只负责
CLI 接线与终端排版。说明见 docs/v_1_0/interation/iter_18_runtime.md。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

import src.runtime_backup as rb  # noqa: E402


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f}{u}"
        v /= 1024
    return f"{n}B"


def cmd_backup(args: argparse.Namespace) -> int:
    out_dir = Path(args.out).expanduser().resolve()
    exclude = {c.strip().upper() for c in (args.exclude or "").split(",") if c.strip()}
    bad = exclude - set(rb.ALL_CATEGORIES)
    if bad:
        print(f"非法类别：{sorted(bad)}；可选 {','.join(rb.ALL_CATEGORIES)}")
        return 1
    cats = set(rb.ALL_CATEGORIES) - exclude
    if not cats:
        print("至少保留一个备份类别。")
        return 1
    print(f"备份范围：{' '.join(c for c in rb.ALL_CATEGORIES if c in cats)}"
          + (f"（已排除 {','.join(sorted(exclude))}）" if exclude else ""))
    zip_path = rb.make_backup(out_dir, categories=cats)
    manifest = rb.read_manifest(zip_path)
    for cat in ("A", "B", "C", "E", "F", "K"):
        s = manifest["category_stats"].get(cat)
        if s:
            print(f"  {cat}: {s['files']} 个文件, {_fmt_size(s['bytes'])}")
    print(f"已生成：{zip_path}（{_fmt_size(zip_path.stat().st_size)}）")
    print("[注意] 备份含明文密钥，请妥善保管，勿提交 git / 上传公共网盘。")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    zip_path = Path(args.zip).expanduser().resolve()
    if not zip_path.is_file():
        print(f"找不到备份文件：{zip_path}")
        return 1
    manifest = rb.read_manifest(zip_path)
    n = len(manifest.get("files", []))
    print(f"将从 {zip_path.name} 还原 {n} 个文件到 {_PROJECT_ROOT}")
    print("[注意] 这会覆盖现有 .env / db/ 等文件。")
    if not args.force:
        ans = input("确认还原？(y/N) ").strip().lower()
        if ans != "y":
            print("已取消。")
            return 1
    done = rb.restore_backup(zip_path, _PROJECT_ROOT, manifest)
    print(f"已还原 {done} 个文件。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    out_dir = Path(args.out).expanduser().resolve()
    snaps = rb.list_snapshots(out_dir)
    if not snaps:
        print(f"{out_dir} 下没有备份快照。")
        return 0
    print(f"{out_dir} 下的备份快照（共 {len(snaps)} 份）：")
    for s in snaps:
        vec = "含向量库" if s["include_vectors"] else "无向量库"
        print(
            f"  {s['timestamp']}  {s['file_count']} 文件  {_fmt_size(s['zip_bytes'])}  "
            f"{vec}  {s['name']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AgentA 运行时数据备份 / 还原工具（范围见 iter_18_runtime.md §3.3）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    pb = sub.add_parser("backup", help="收集 A/B/C/E/F/K 六类数据打成 zip")
    pb.add_argument("--out", required=True, help="快照输出目录")
    pb.add_argument(
        "--exclude", default="",
        help="逗号分隔要排除的类别（A=配置 B=DB C=向量库 E=黄金集 F=报告 K=编辑器），默认全备",
    )
    pb.set_defaults(func=cmd_backup)

    pr = sub.add_parser("restore", help="从 zip 还原回项目根")
    pr.add_argument("--zip", required=True, help="备份 zip 路径")
    pr.add_argument("--force", action="store_true", help="跳过覆盖确认")
    pr.set_defaults(func=cmd_restore)

    pl = sub.add_parser("list", help="列出目标目录下的快照")
    pl.add_argument("--out", required=True, help="快照所在目录")
    pl.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
