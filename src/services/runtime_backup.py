#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时数据备份 / 还原的公共逻辑。

CLI（`tools/backup.py`）与 API（`/admin/backup/*`）共用本模块，保证两边口径一致。

备份范围见 docs/v_1_0/interation/iter_18_runtime.md §3.3（A B C E F K）：
    A 敏感配置  B 运行期 DB  C 向量库 / 索引  E 黄金集  F 评估报告  K 编辑器配置
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import src.config as config

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

MANIFEST_NAME = "backup-manifest.json"

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

# 全部可备份类别（顺序即 UI / manifest 展示顺序）
ALL_CATEGORIES: tuple[str, ...] = ("A", "B", "C", "E", "F", "K")


def _abs(root: Path, raw: str) -> Path:
    """把 config 里的相对路径（如 ./db/chroma）解析为相对项目根的绝对路径。"""
    p = Path(raw)
    return p if p.is_absolute() else (root / p)


def build_plan(
    root: Path, cfg, categories: "set[str] | None" = None
) -> list[tuple[str, str, Path]]:
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
        raw = getattr(cfg, attr, None)
        if raw:
            plan.append(("B", "sqlite", _abs(root, raw)))

    # C 向量库 / 索引
    for attr in _VECTOR_CONFIG_ATTRS:
        raw = getattr(cfg, attr, None)
        if raw:
            plan.append(("C", "tree", _abs(root, raw)))

    # E 黄金集
    plan.append(("E", "file", root / "tools" / "rag_eval" / "golden.json"))

    # F 评估报告（统一在 tools/reports/<eval>/，旧的 agent_eval/reports、rag_eval/reports 已迁此）
    plan.append(("F", "tree", root / "tools" / "reports"))

    # K 编辑器 / IDE
    plan.append(("K", "file", root / ".vscode" / "settings.json"))
    for ws in sorted(root.glob("*.code-workspace")):
        plan.append(("K", "file", ws))

    # 只保留用户勾选的类别（categories=None → 全选）
    if categories is not None:
        plan = [e for e in plan if e[0] in categories]
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
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

    return zip_path


def read_manifest(zip_path: Path) -> dict:
    """读取 zip 内的 backup-manifest.json。"""
    with ZipFile(zip_path, "r") as zf:
        return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))


def validate_restore_targets(manifest: dict, root: Path) -> list[str]:
    """校验 manifest 各还原目标是否安全（仅限项目根内的相对路径）。

    返回不安全条目的 arc 列表（空列表表示全部安全）。拒绝：external 条目、
    绝对路径、规范化后逃出项目根（`..` 穿越）的路径 —— 防止 Web 上传的恶意 zip
    把文件写到项目外。
    """
    root_resolved = root.resolve()
    bad: list[str] = []
    for e in manifest.get("files", []):
        if e.get("external"):
            bad.append(e.get("arc", "?"))
            continue
        rel = e.get("restore", "")
        if Path(rel).is_absolute():
            bad.append(e.get("arc", "?"))
            continue
        target = (root_resolved / rel).resolve()
        if target != root_resolved and root_resolved not in target.parents:
            bad.append(e.get("arc", "?"))
    return bad


def restore_backup(zip_path: Path, root: Path, manifest: dict | None = None) -> int:
    """按 manifest 把 zip 内文件写回还原目标，返回还原文件数。

    调用前应先用 validate_restore_targets 校验；本函数对 external 条目仍按绝对路径写，
    供 CLI 在可信环境使用。Web 入口务必先校验再调本函数。
    """
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
                "name": zp.name,
                "timestamp": m.get("timestamp", "?"),
                "created_at": m.get("created_at", ""),
                "include_vectors": m.get("include_vectors"),
                "file_count": len(m.get("files", [])),
                "zip_bytes": zp.stat().st_size,
                "category_stats": m.get("category_stats", {}),
            }
        )
    return out


def make_backup(
    out_dir: Path, *, categories: "set[str] | None" = None, timestamp: str | None = None
) -> Path:
    """便捷入口：用项目根 + 全局 config 构建清单并生成备份，返回 zip 路径。

    categories=None → 全类别；否则只备份勾选的类别（{A,B,C,E,F,K} 子集）。
    """
    cats = set(categories) if categories is not None else set(ALL_CATEGORIES)
    plan = build_plan(_PROJECT_ROOT, config, categories=cats)
    return create_backup(
        plan, _PROJECT_ROOT, out_dir, include_vectors="C" in cats, timestamp=timestamp
    )
