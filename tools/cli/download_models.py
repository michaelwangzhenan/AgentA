"""
HuggingFace 模型下载工具（多镜像自动 fallback）

把项目用到的所有 SentenceTransformer / CrossEncoder 模型按数字编号一键拉到本地缓存。
解决场景：新机器初始化、HF 镜像被墙、`TRANSFORMERS_OFFLINE=1` 下提示模型缺失时的补齐。

CLI 用法：
    python tools/cli/download_models.py           # 下载全部 5 个（已缓存自动跳过）
    python tools/cli/download_models.py 3 4       # 仅下载编号 3 和 4
    python tools/cli/download_models.py -l        # 列出清单 + 本地缓存状态，不下载
    python tools/cli/download_models.py 4 --force # 强制重新下载（即使已缓存）
    python tools/cli/download_models.py 4 \\
        --mirror https://hf-mirror.com https://huggingface.co   # 自定义镜像顺序
    python tools/cli/download_models.py -h        # 查看帮助

模型编号：
    1  Embedding-en  sentence-transformers/all-MiniLM-L6-v2  ~90  MB
    2  Embedding-zh  BAAI/bge-small-zh                       ~96  MB
    3  Embedding-m3  BAAI/bge-m3                             ~568 MB
    4  Reranker      BAAI/bge-reranker-base                  ~1.1 GB    ← 中英双语首选
    5  Reranker      cross-encoder/ms-marco-MiniLM-L-6-v2    ~23  MB    ← 轻量备选

行为约定：
    - 每个模型按 --mirror 列表顺序逐个尝试，命中第一个成功的就停。所有镜像
      都失败才标记该模型为 FAIL，最终聚合返回非 0 退出码。
    - 镜像切换通过子进程隔离：huggingface_hub.constants.ENDPOINT 在 import
      时就被冻结成模块常量，运行时改 os.environ['HF_ENDPOINT'] 不会生效，
      只能 fork 一个新 python 解释器、子进程启动时 huggingface_hub 重新读 env。
    - 缓存目录共用 ~/.cache/huggingface/hub/，所以镜像 1 已下载的部分文件
      镜像 2 可以续传（断点续传由 huggingface_hub 自动处理）。
    - 子进程不 capture stdout，sentence-transformers 的 tqdm 进度条直接显
      示到当前 console；判定成功/失败仅靠 returncode + 缓存检测。
    - 已缓存自动跳过；--force 可强制重新下载（修复破损缓存）。

默认镜像顺序（可被 --mirror 覆盖）：
    1. https://hf-mirror.com   国内社区镜像，最稳
    2. https://huggingface.co  原站，需代理或墙外
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from typing import Literal


# ── 默认镜像清单（按可达性优先级排序） ───────────────────────────────────────
# 顺序很重要：先试国内最稳的，失败才走原站（原站对国内用户多半要代理）。
# 用户可通过 --mirror URL1 URL2 ... 覆盖；--mirror 只传一个就退化为单镜像。
DEFAULT_MIRRORS: list[str] = [
    "https://hf-mirror.com",
    "https://huggingface.co",
]


# ── 模型清单 ──────────────────────────────────────────────────────────────────
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


# ── 子进程下载执行（每次镜像切换 fork 新进程以重置 huggingface_hub.ENDPOINT） ─
_WORKER_TEMPLATE = textwrap.dedent(
    """
    import os, sys
    kind = {kind!r}
    repo = {repo!r}
    print(f'[child] HF_ENDPOINT={{os.environ.get("HF_ENDPOINT")}}', flush=True)
    if kind == 'embedding':
        from sentence_transformers import SentenceTransformer
        SentenceTransformer(repo)
    else:
        from sentence_transformers import CrossEncoder
        CrossEncoder(repo)
    sys.exit(0)
    """
)


def _spawn_download(spec: ModelSpec, endpoint: str, timeout_s: int) -> int:
    """
    在子进程中以指定 endpoint 下载模型，returncode 透传。

    必须用子进程而非 importlib.reload，原因：huggingface_hub.constants.ENDPOINT
    是 module 顶层常量，被 sentence_transformers / transformers 等多处 import 后
    各自缓存引用，主进程内单点 reload 无法可靠传播；子进程从头 import，env 注入
    才能彻底切换。
    """
    env = os.environ.copy()
    env["HF_ENDPOINT"] = endpoint
    env["TRANSFORMERS_OFFLINE"] = "0"
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    # 限制单次 HTTP 调用超时，避免 DNS / connection 不可达的镜像把 5 次内置 retry
    # 全部跑完（默认每个请求最坏 ~30s）。10s 足够正常握手 + TLS，异常时快速 fail-fast
    # 让外层尽快切到下一镜像。
    env.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "10")

    code = _WORKER_TEMPLATE.format(kind=spec.kind, repo=spec.repo_id)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            timeout=timeout_s,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return -9  # 用 -9 区分超时与一般失败


def _download_one(spec: ModelSpec, mirrors: list[str], force: bool, timeout_s: int) -> bool:
    """
    下载单个模型，按 mirrors 顺序逐个尝试；返回 True 表示成功（或跳过）。

    判定成功需同时满足：子进程返回 0 + 缓存确实存在。前者防漏报、后者防误报
    （某些版本 sentence-transformers 即使下载失败也可能 returncode=0）。
    """
    label = f"[{spec.idx}] {spec.role:38s} {spec.repo_id}"

    if not force and _is_cached(spec.repo_id):
        size = _cache_size_mb(spec.repo_id)
        size_str = f"{size:6.1f} MB" if size else "  -"
        print(f"  SKIP  {label}  (已缓存 {size_str})")
        return True

    print(f"  WANT  {label}  (~{spec.size_mb} MB)")
    last_err = "no mirror tried"
    for endpoint in mirrors:
        print(f"        -> TRY   {endpoint}", flush=True)
        t0 = time.time()
        rc = _spawn_download(spec, endpoint, timeout_s)
        elapsed = time.time() - t0

        if rc == 0 and _is_cached(spec.repo_id):
            actual = _cache_size_mb(spec.repo_id)
            print(f"        -> OK    via {endpoint}  ({actual:.1f} MB, {elapsed:.1f}s)")
            return True

        if rc == -9:
            last_err = f"timeout({timeout_s}s) via {endpoint}"
        elif rc == 0:
            last_err = f"rc=0 但缓存仍缺失 via {endpoint}（可能下载已写入但校验失败）"
        else:
            last_err = f"rc={rc} via {endpoint} ({elapsed:.1f}s)"
        print(f"        -> FAIL  {last_err}")

    print(f"  FAIL  {label}  — 所有 {len(mirrors)} 个镜像都失败，最后一次：{last_err}")
    return False


# ── 列表打印 ──────────────────────────────────────────────────────────────────
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
        "  python scripts/download_models.py                         下载全部缺失模型\n"
        "  python scripts/download_models.py 3 4                     仅下载编号 3 和 4\n"
        "  python scripts/download_models.py -l                      列出清单 + 缓存状态\n"
        "  python scripts/download_models.py 4 --force               强制重新下载\n"
        "  python scripts/download_models.py 4 --mirror https://huggingface.co\n"
        "                                                            自定义镜像\n"
        "\n"
        "默认镜像顺序（按可达性）：\n"
        "  1. https://hf-mirror.com   (国内社区镜像，首选)\n"
        "  2. https://huggingface.co  (原站，需代理或墙外)\n"
    )
    p = argparse.ArgumentParser(
        prog="download_models",
        description="一键下载本工程使用的 HuggingFace 模型，多镜像自动 fallback。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument(
        "ids",
        nargs="*",
        type=int,
        help="要下载的模型编号（1~5），可多选；不指定则下载全部。编号详见 -l/--list。",
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
        "--mirror",
        nargs="+",
        default=DEFAULT_MIRRORS,
        metavar="URL",
        help=(
            "HF 镜像端点列表，按顺序尝试。可传多个 URL 用空格分隔。"
            f" 默认 {' '.join(DEFAULT_MIRRORS)}"
        ),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=1800,
        metavar="SEC",
        help="单次下载尝试的超时时间（秒），默认 1800（30 分钟）。",
    )
    return p


# ── main ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.list_only:
        _print_list()
        return 0

    invalid = [i for i in args.ids if i not in _BY_IDX]
    if invalid:
        print(f"未知模型编号: {invalid}，有效范围 1..{len(MODELS)}（用 -l 查看清单）", file=sys.stderr)
        return 2

    targets = [_BY_IDX[i] for i in args.ids] if args.ids else list(MODELS)
    print(f"待处理模型数 : {len(targets)}")
    print(f"镜像顺序     : {' -> '.join(args.mirror)}")
    print(f"单次超时     : {args.timeout}s\n")

    failures: list[ModelSpec] = []
    for spec in targets:
        ok = _download_one(spec, mirrors=args.mirror, force=args.force, timeout_s=args.timeout)
        if not ok:
            failures.append(spec)

    print()
    if failures:
        print(f"完成，失败 {len(failures)}/{len(targets)} 个：")
        for s in failures:
            print(f"  - [{s.idx}] {s.repo_id}")
        print(
            "\n排错提示：\n"
            "  1. 公司代理环境：先 set HTTPS_PROXY=http://ip:port 再重试；\n"
            "  2. 添加更多镜像：--mirror https://hf-mirror.com https://huggingface.co ...\n"
            "  3. 单镜像超时：--timeout 3600（大模型 + 慢速链路）；\n"
            "  4. 缓存可能损坏：--force 强制重下；\n"
            "  5. 实在拉不动：手动 hf-mirror 网页版下载 → 放到 ~/.cache/huggingface/hub/。"
        )
        return 1

    print(f"完成，全部 {len(targets)} 个模型就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
