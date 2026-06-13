#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行时数据备份 / 还原工具：把可重用数据打成单个带时间戳的 zip，支持还原回项目。

备份范围（见 docs/v_1_0/interation/iter_18_runtime.md §3.3，最终选择 A B C E F K）：
    A 敏感配置  B 运行期 DB  C 向量库 / 索引  E 黄金集  F 评估报告  K 编辑器配置

用法：
    python tools/backup.py backup  --out <dir> [--skip-vectors]
    python tools/backup.py restore --zip <path> [--force]
    python tools/backup.py list    --out <dir>

产物：<dir>/agenta-backup-<YYYYMMDD-HHMMSS>.zip，内含按项目根相对路径存放的文件 +
backup-manifest.json（记录时间戳 / 各类命中清单 / 是否含向量库），restore / list 据此工作。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

_MANIFEST_NAME = "backup-manifest.json"

# B 类：运行期 SQLite 的 config 属性名（各自路径可经 .env 覆盖，故从 config 读）
_DB_CONFIG_ATTRS = (
    "MEMORY_DB_PATH",
    "AUTH_DB_PATH",
    "USAGE_DB_PATH",
    "RAG_GOLDEN_DB_PATH",
    "USER_MEMORY_DB_PATH",
    "LEARNING_PLAN_DB_PATH",
    "QUIZ_DB_PATH",
    "SRS_DB_PATH",
)
# C 类：向量库 / 检索索引的 config 属性名
_VECTOR_CONFIG_ATTRS = ("CHROMA_DB_PATH", "BM25_INDEX_DIR")


def _abs(root: Path, raw: str) -> Path:
    """把 config 里的相对路径（如 ./db/chroma）解析为相对项目根的绝对路径。"""
    p = Path(raw)
    return p if p.is_absolute() else (root / p)


def build_plan(root: Path, config, skip_vectors: bool) -> list[tuple[str, str, Path]]:
    """构建备份清单，返回 (类别, 收集方式, 绝对路径) 列表。

    收集方式：file=单文件拷贝 / tree=目录树拷贝 / sqlite=在线一致备份。
    路径不存在的条目保留在清单里，由收集阶段静默跳过并计数。
    """
    plan: list[tuple[str, str, Path]] = []

    # A 敏感配置
    for rel in (
        ".env",
        ".agenta/config_overrides.json",
        ".agenta/routing_pool.json",
        ".agenta/api_keys.json",
        ".agenta/skills/disabled.json",
        ".agenta/mcp/disabled.json",
    ):
        plan.append(("A", "file", root / rel))

    # B 运行期 DB（在线备份，避免拷到半写状态）
    for attr in _DB_CONFIG_ATTRS:
        raw = getattr(config, attr, None)
        if raw:
            plan.append(("B", "sqlite", _abs(root, raw)))

    # C 向量库 / 索引
    if not skip_vectors:
        for attr in _VECTOR_CONFIG_ATTRS:
            raw = getattr(config, attr, None)
            if raw:
                plan.append(("C", "tree", _abs(root, raw)))

    # E 黄金集
    plan.append(("E", "file", root / "tools" / "rag_eval" / "golden.json"))

    # F 评估报告
    plan.append(("F", "tree", root / "tools" / "agent_eval" / "reports"))
    plan.append(("F", "tree", root / "tools" / "rag_eval" / "reports"))

    # K 编辑器 / IDE
    plan.append(("K", "file", root / ".vscode" / "settings.json"))
    for ws in sorted(root.glob("*.code-workspace")):
        plan.append(("K", "file", ws))

    return plan


def _arc_and_target(path: Path, root: Path) -> tuple[str, str, bool]:
    """计算 zip 内归档名与还原目标；项目根内用相对路径，根外用绝对路径还原。"""
    try:
        rel = path.relative_to(root).as_posix()
        return rel, rel, False
    except ValueError:
        safe = path.as_posix().lstrip("/").replace(":", "")
        return f"_external/{safe}", path.as_posix(), True


def _sqlite_online_backup(src: Path, dst: Path) -> None:
    """用 sqlite backup API 导出一致副本，服务运行中也安全。"""
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def create_backup(
    plan: list[tuple[str, str, Path]],
    root: Path,
    out_dir: Path,
    *,
    include_vectors: bool,
    timestamp: str | None = None,
) -> Path:
    """按清单收集文件打成单个 zip，附 backup-manifest.json，返回 zip 路径。"""
    ts = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"agenta-backup-{ts}.zip"

    files_meta: list[dict] = []
    cat_stats: dict[str, dict[str, int]] = {}

    def _record(cat: str, arc: str, target: str, external: bool, size: int) -> None:
        files_meta.append(
            {"category": cat, "arc": arc, "restore": target, "external": external, "bytes": size}
        )
        s = cat_stats.setdefault(cat, {"files": 0, "bytes": 0})
        s["files"] += 1
        s["bytes"] += size

    with tempfile.TemporaryDirectory() as tmp, ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        tmp_dir = Path(tmp)
        for cat, kind, path in plan:
            if kind == "file":
                if not path.is_file():
                    continue
                arc, target, ext = _arc_and_target(path, root)
                zf.write(path, arc)
                _record(cat, arc, target, ext, path.stat().st_size)
            elif kind == "tree":
                if not path.is_dir():
                    continue
                for f in sorted(path.rglob("*")):
                    if not f.is_file() or "__pycache__" in f.parts:
                        continue
                    arc, target, ext = _arc_and_target(f, root)
                    zf.write(f, arc)
                    _record(cat, arc, target, ext, f.stat().st_size)
            elif kind == "sqlite":
                if not path.is_file():
                    continue
                tmp_db = tmp_dir / f"{cat}_{path.name}"
                _sqlite_online_backup(path, tmp_db)
                arc, target, ext = _arc_and_target(path, root)
                zf.write(tmp_db, arc)
                _record(cat, arc, target, ext, tmp_db.stat().st_size)

        manifest = {
            "timestamp": ts,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "include_vectors": include_vectors,
            "category_stats": cat_stats,
            "files": files_meta,
        }
        zf.writestr(_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

    return zip_path


def read_manifest(zip_path: Path) -> dict:
    """读取 zip 内的 backup-manifest.json。"""
    with ZipFile(zip_path, "r") as zf:
        return json.loads(zf.read(_MANIFEST_NAME).decode("utf-8"))


def restore_backup(zip_path: Path, root: Path, manifest: dict | None = None) -> int:
    """按 manifest 把 zip 内文件写回还原目标，返回还原文件数。"""
    if manifest is None:
        manifest = read_manifest(zip_path)
    n = 0
    with ZipFile(zip_path, "r") as zf:
        for e in manifest["files"]:
            data = zf.read(e["arc"])
            target = Path(e["restore"]) if e.get("external") else (root / e["restore"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            n += 1
    return n


def list_snapshots(out_dir: Path) -> list[dict]:
    """扫描目录下的 agenta-backup-*.zip，返回各快照的 manifest 摘要（按时间戳倒序）。"""
    out: list[dict] = []
    if not out_dir.is_dir():
        return out
    for zp in sorted(out_dir.glob("agenta-backup-*.zip"), reverse=True):
        try:
            m = read_manifest(zp)
        except (KeyError, OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "path": zp,
                "timestamp": m.get("timestamp", "?"),
                "include_vectors": m.get("include_vectors"),
                "file_count": len(m.get("files", [])),
                "zip_bytes": zp.stat().st_size,
            }
        )
    return out


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f}{u}"
        v /= 1024
    return f"{n}B"


def cmd_backup(args: argparse.Namespace) -> int:
    import src.config as config  # noqa: E402

    out_dir = Path(args.out).expanduser().resolve()
    plan = build_plan(_PROJECT_ROOT, config, skip_vectors=args.skip_vectors)
    print(f"备份范围：A B C E F K{'（已跳过 C 向量库）' if args.skip_vectors else ''}")
    zip_path = create_backup(
        plan, _PROJECT_ROOT, out_dir, include_vectors=not args.skip_vectors
    )
    manifest = read_manifest(zip_path)
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
    manifest = read_manifest(zip_path)
    n = len(manifest.get("files", []))
    print(f"将从 {zip_path.name} 还原 {n} 个文件到 {_PROJECT_ROOT}")
    print("[注意] 这会覆盖现有 .env / db/ 等文件。")
    if not args.force:
        ans = input("确认还原？(y/N) ").strip().lower()
        if ans != "y":
            print("已取消。")
            return 1
    done = restore_backup(zip_path, _PROJECT_ROOT, manifest)
    print(f"已还原 {done} 个文件。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    out_dir = Path(args.out).expanduser().resolve()
    snaps = list_snapshots(out_dir)
    if not snaps:
        print(f"{out_dir} 下没有备份快照。")
        return 0
    print(f"{out_dir} 下的备份快照（共 {len(snaps)} 份）：")
    for s in snaps:
        vec = "含向量库" if s["include_vectors"] else "无向量库"
        print(
            f"  {s['timestamp']}  {s['file_count']} 文件  {_fmt_size(s['zip_bytes'])}  "
            f"{vec}  {s['path'].name}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="AgentA 运行时数据备份 / 还原工具（范围见 iter_18_runtime.md §3.3）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    pb = sub.add_parser("backup", help="收集 A/B/C/E/F/K 七类数据打成 zip")
    pb.add_argument("--out", required=True, help="快照输出目录")
    pb.add_argument("--skip-vectors", action="store_true", help="跳过 C 类向量库 / 索引（体积大）")
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
