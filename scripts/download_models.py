"""
HuggingFace 模型下载工具

把项目用到的所有 SentenceTransformer / CrossEncoder 模型按数字编号一键拉到本地缓存。
解决场景：新机器初始化、HF 镜像切换、`TRANSFORMERS_OFFLINE=1` 下提示模型缺失时的补齐。

CLI 用法：
    python scripts/download_models.py           # 下载全部 5 个（已缓存自动跳过）
    python scripts/download_models.py 3 4       # 仅下载编号 3 和 4
    python scripts/download_models.py -l        # 列出清单 + 本地缓存状态，不下载
    python scripts/download_models.py 4 --force # 强制重新下载（即使已缓存）
    python scripts/download_models.py -h        # 查看帮助

模型编号：
    1  Embedding-en  sentence-transformers/all-MiniLM-L6-v2  ~90  MB
    2  Embedding-zh  BAAI/bge-small-zh                       ~96  MB
    3  Embedding-m3  BAAI/bge-m3                             ~568 MB
    4  Reranker      BAAI/bge-reranker-base                  ~1.1 GB    ← 中英双语首选
    5  Reranker      cross-encoder/ms-marco-MiniLM-L-6-v2    ~23  MB    ← 轻量备选

行为约定：
    - 启动时强制设置 HF_ENDPOINT=https://hf-mirror.com（国内镜像）+ TRANSFORMERS_OFFLINE=0，
      避免 .env 里的 OFFLINE=1 把下载阻断。本进程退出后不影响其他程序。
    - 已存在则跳过（通过 huggingface_hub.try_to_load_from_cache 探测 config.json）。
    - 任意一个模型下载失败 → 退出码 1，方便上游脚本判断。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Literal


# ── 模型清单 ──────────────────────────────────────────────────────────────────
# 编号 / repo_id / 类型 / 中文角色描述 / 标称大小（MB，仅用于显示）
@dataclass(frozen=True)
class ModelSpec:
    idx: int
    repo_id: str
    kind: Literal["embedding", "reranker"]
    role: str
    size_mb: int


MODELS: list[ModelSpec] = [
    ModelSpec(1, "sentence-transformers/all-MiniLM-L6-v2", "embedding", "Embedding-en  (英文/多语言, 384d)", 90),
    ModelSpec(2, "BAAI/bge-small-zh",                       "embedding", "Embedding-zh  (中文优化, 512d)",     96),
    ModelSpec(3, "BAAI/bge-m3",                             "embedding", "Embedding-m3  (多语言, 1024d)",      568),
    ModelSpec(4, "BAAI/bge-reranker-base",                  "reranker",  "Reranker      (中英双语, 默认)",     1100),
    ModelSpec(5, "cross-encoder/ms-marco-MiniLM-L-6-v2",    "reranker",  "Reranker      (轻量, 英文偏好)",     23),
]
_BY_IDX: dict[int, ModelSpec] = {m.idx: m for m in MODELS}


# ── 缓存状态探测（轻量，不实际加载模型） ─────────────────────────────────────
def _is_cached(repo_id: str) -> bool:
    """通过查 config.json 是否在 hub cache 来判断模型是否已下载。"""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    p = try_to_load_from_cache(repo_id, filename="config.json")
    return bool(p) and p is not False  # None / _CACHED_NO_EXIST 都视为未缓存


def _cache_size_mb(repo_id: str) -> float:
    """从 hub 缓存扫描中查指定 repo 的实际占用，返回 MB；查不到返回 0。"""
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return 0.0
    try:
        info = scan_cache_dir()
    except Exception:
        return 0.0
    for repo in info.repos:
        if repo.repo_id == repo_id:
            return repo.size_on_disk / 1024 / 1024
    return 0.0


# ── 下载执行 ──────────────────────────────────────────────────────────────────
def _download_one(spec: ModelSpec, force: bool) -> bool:
    """
    下载单个模型；返回 True 表示成功（或跳过），False 表示失败。

    embedding → SentenceTransformer，reranker → CrossEncoder，类型不能搞错否则会
    在加载阶段报"missing pooling config"等怪异错误。
    """
    label = f"[{spec.idx}] {spec.role:38s} {spec.repo_id}"
    if not force and _is_cached(spec.repo_id):
        size = _cache_size_mb(spec.repo_id)
        size_str = f"{size:6.1f} MB" if size else "  -"
        print(f"  SKIP  {label}  (已缓存 {size_str})")
        return True

    print(f"  PULL  {label}  (~{spec.size_mb} MB)", flush=True)
    t0 = time.time()
    try:
        if spec.kind == "embedding":
            from sentence_transformers import SentenceTransformer
            SentenceTransformer(spec.repo_id)
        else:
            from sentence_transformers import CrossEncoder
            CrossEncoder(spec.repo_id)
    except Exception as e:  # noqa: BLE001 — HF / 网络 / 文件系统异常种类繁多，统一兜底
        print(f"  FAIL  {label}\n        {type(e).__name__}: {e}")
        return False
    elapsed = time.time() - t0
    actual = _cache_size_mb(spec.repo_id)
    print(f"  OK    {label}  ({actual:.1f} MB, {elapsed:.1f}s)")
    return True


def _print_list() -> None:
    print("可下载模型清单：\n")
    print(f"  {'#':>2}  {'role':<40} {'repo_id':<48} {'size':>8}  cached")
    print("  " + "-" * 110)
    for m in MODELS:
        cached = "YES" if _is_cached(m.repo_id) else "NO"
        actual = _cache_size_mb(m.repo_id)
        size_show = f"{actual:5.1f}MB" if actual else f"~{m.size_mb}MB"
        print(f"  {m.idx:>2}  {m.role:<40} {m.repo_id:<48} {size_show:>8}  {cached}")
    print()


# ── argparse ──────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    epilog = (
        "示例：\n"
        "  python scripts/download_models.py            下载全部缺失模型\n"
        "  python scripts/download_models.py 3 4        仅下载编号 3 和 4\n"
        "  python scripts/download_models.py -l         列出清单 + 缓存状态\n"
        "  python scripts/download_models.py 4 --force  强制重新下载\n"
    )
    p = argparse.ArgumentParser(
        prog="download_models",
        description="一键下载本工程使用的 HuggingFace 模型。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument(
        "ids",
        nargs="*",
        type=int,
        help=(
            "要下载的模型编号（1~5），可多选；不指定则下载全部。"
            " 编号详见 -l/--list。"
        ),
    )
    p.add_argument(
        "-l", "--list",
        action="store_true",
        dest="list_only",
        help="列出可下载模型清单及本地缓存状态，不执行下载。",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="即使已缓存也重新下载（用于修复破损的本地缓存）。",
    )
    p.add_argument(
        "--endpoint",
        default="https://hf-mirror.com",
        help="HF 镜像端点，默认 https://hf-mirror.com（国内镜像）。",
    )
    return p


# ── main ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # 强制使用镜像 + 允许联网，仅对本进程生效
    os.environ["HF_ENDPOINT"] = args.endpoint
    os.environ["TRANSFORMERS_OFFLINE"] = "0"
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    if args.list_only:
        _print_list()
        return 0

    invalid = [i for i in args.ids if i not in _BY_IDX]
    if invalid:
        print(f"未知模型编号: {invalid}，有效范围 1..{len(MODELS)}（用 -l 查看清单）", file=sys.stderr)
        return 2

    targets = [_BY_IDX[i] for i in args.ids] if args.ids else list(MODELS)
    print(f"HF_ENDPOINT         = {os.environ['HF_ENDPOINT']}")
    print(f"TRANSFORMERS_OFFLINE= {os.environ['TRANSFORMERS_OFFLINE']}")
    print(f"待处理模型数: {len(targets)}\n")

    failures: list[str] = []
    for spec in targets:
        ok = _download_one(spec, force=args.force)
        if not ok:
            failures.append(spec.repo_id)

    print()
    if failures:
        print(f"完成，失败 {len(failures)}/{len(targets)} 个：")
        for r in failures:
            print(f"  - {r}")
        print(
            "\n排错提示：\n"
            "  1. 检查网络是否能访问 HF 镜像（默认 https://hf-mirror.com）；\n"
            "  2. 公司代理环境可设置 HTTPS_PROXY 后重试；\n"
            "  3. 用 --force 强制重新下载；\n"
            "  4. 切换到原站：--endpoint https://huggingface.co"
        )
        return 1

    print(f"完成，全部 {len(targets)} 个模型就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
