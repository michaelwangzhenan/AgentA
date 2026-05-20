"""
RAG 评估自动化脚本（Iter-5）

把以前要敲一长串的"清库 → 双语种 ingest → 评估 → 出 JSON"打包成一条命令。
设计成"单次评估"——baseline 多 commit / 消融多配置由调用方多次调本脚本即可。

固定流程：
    1. （可选）清空 chroma_db / bm25_index，避免不同分块/索引格式互相污染；
    2. 用 en 模型把 --en-dir 灌进 kb_en；
    3. 用 zh 模型把 --zh-dir 灌进 kb_zh；
    4. 调 tools.rag_eval.eval 评估；
    5. 把结果保存为 tools/rag_eval/reports/<label>-<时间戳>.json，并打印 1 行汇总。

默认 ingest 路径（可被 --en-dir / --zh-dir 覆盖）：
    en  →  ../pursue          # 用户工作笔记，英文/通用为主
    zh  →  ../pursue/resume   # 简历相关，中文为主

使用示例：
    # 当前 HEAD 跑一次完整评估
    python -m tools.rag_eval.run_eval

    # baseline 多 commit 对比（在外层用 PowerShell / bash 串）
    git stash
    git checkout 1fe5582; python -m tools.rag_eval.run_eval --label iter0
    git checkout 50f19b1; python -m tools.rag_eval.run_eval --label iter1
    git checkout 54103d8; python -m tools.rag_eval.run_eval --label iter5
    git checkout main; git stash pop

    # 消融实验（不重灌库，复用上一轮 ingest）
    python -m tools.rag_eval.run_eval --skip-ingest --no-rewriter --label ablation-no-rewriter
    python -m tools.rag_eval.run_eval --skip-ingest --no-rerank   --label ablation-no-rerank

    # 评估 m3 单库（依赖 RAG_ACTIVE_EMBEDDINGS env 切换，Iter-5 引入）
    $env:RAG_ACTIVE_EMBEDDINGS="m3"
    python -m tools.rag_eval.run_eval --en-dir ../pursue --zh-dir ../pursue/resume `
        --en-model m3 --zh-model m3 --label m3-single
    $env:RAG_ACTIVE_EMBEDDINGS="en,zh"     # 跑完恢复

注意：
    1. golden.json 不存在时 tools.rag_eval.eval 会回退到 golden.example.json，
       但例子是泛用占位、与 ../pursue 真实内容无关，所有 case 必然 miss。
       第一次跑前请基于 ../pursue 内容写一份 tools/rag_eval/golden.json。
    2. ../pursue/resume 是 ../pursue 的子目录；同一份文档会同时进 kb_en 与
       kb_zh，是预期行为（双库各按自己模型嵌入，retriever 跨库 round-robin
       合并）。如要严格分离，请用 --en-dir 指向不含 resume 的目录。
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("run_eval")

# 项目根目录 = 本文件向上两级（tools/rag_eval/run_eval.py → AgentA/）
ROOT = Path(__file__).resolve().parents[2]

DEFAULT_EN_DIR = "../pursue"
DEFAULT_ZH_DIR = "../pursue/resume"
# reports 默认与本脚本同级（tools/rag_eval/reports），跟 golden.json 一起归属
# rag_eval 域；用户传 --reports-dir 时仍会按"相对 ROOT"语义解析（见 main()）。
DEFAULT_REPORTS_DIR = "tools/rag_eval/reports"

# 入库时清掉的目录（务必与 src/config.py / src/rag/bm25_index.py 默认一致）
PURGE_DIRS: tuple[str, ...] = ("chroma_db", "bm25_index")

# 默认 golden 路径（与 tools/rag_eval/eval.py 保持一致）
DEFAULT_GOLDEN = ROOT / "tools" / "rag_eval" / "golden.json"
EXAMPLE_GOLDEN = ROOT / "tools" / "rag_eval" / "golden.example.json"


def _run(cmd: list[str], step: str) -> None:
    """跑一条子命令；失败立即退出，避免链路中段产出半成品报告。"""
    logger.info("[step] %s", step)
    logger.info("$ %s", " ".join(cmd))
    t0 = time.monotonic()
    res = subprocess.run(cmd, cwd=ROOT, check=False)
    dt = time.monotonic() - t0
    if res.returncode != 0:
        logger.error("✗ [%s] 失败 (exit=%d, %.1fs)", step, res.returncode, dt)
        sys.exit(res.returncode)
    logger.info("✓ [%s] 用时 %.1fs", step, dt)


def _purge_db(dry_run: bool = False) -> None:
    """删除旧 chroma_db / bm25_index；切 commit / 换分块策略时必做。"""
    for d in PURGE_DIRS:
        p = ROOT / d
        if not p.exists():
            continue
        if dry_run:
            logger.info("[purge] would remove %s", p)
            continue
        logger.info("[purge] removing %s", p)
        shutil.rmtree(p)


def _check_golden(use_default: bool) -> None:
    """评估前检查 golden.json，缺失时给出明确警告（不阻断）。"""
    if use_default and not DEFAULT_GOLDEN.exists() and EXAMPLE_GOLDEN.exists():
        logger.warning(
            "⚠ %s 不存在，eval 将回退到 %s（占位示例，与 ../pursue 真实内容无关，"
            "所有 case 大概率 miss）。请尽快编写自己的 golden.json。",
            DEFAULT_GOLDEN.name, EXAMPLE_GOLDEN.name,
        )


def _summarize(report_path: Path) -> str:
    """
    从 eval 输出 JSON 抽 4 项关键指标 + 关键 metadata 摘要拼成一行。

    输出格式适合 grep / 粘进表格做跨实验对比；metadata 摘要回答"这次跑用的是
    哪个 commit / 哪些 collection / 关了哪些功能"，不必再点开 JSON 看头部。
    """
    if not report_path.exists():
        return f"<no report at {report_path}>"
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"<parse error: {e}>"

    def _pct(v: Any) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):.2%}"
        except (TypeError, ValueError):
            return str(v)

    metric_line = (
        f"items={data.get('items')} k={data.get('k')} "
        f"hit_source@k={_pct(data.get('hit_source_at_k'))} "
        f"hit_keyword@k={_pct(data.get('hit_keyword_at_k'))} "
        f"hit_either@k={_pct(data.get('hit_either_at_k'))} "
        f"MRR={float(data.get('mrr', 0.0)):.4f}"
    )

    md = data.get("metadata") or {}
    if not md:
        return metric_line

    git = md.get("env", {}).get("git", {})
    git_part = f"{git.get('commit', '?')}{'*' if git.get('dirty') else ''}"
    aliases = md.get("embeddings", {}).get("active_aliases", [])
    rr = md.get("reranker", {})
    qr = md.get("query_rewrite", {})

    ctx = (
        f"git={git_part} "
        f"emb={','.join(aliases) or '?'} "
        f"rr={'ON' if rr.get('enabled') else 'OFF'} "
        f"rewrite={'ON' if qr.get('enabled') else 'OFF'}"
    )
    return f"{metric_line} | {ctx}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.rag_eval.run_eval",
        description="RAG 评估自动化（清库 + 双语种 ingest + eval 一条龙）",
    )
    ap.add_argument("--label", default="run",
                    help="报告 label，影响输出文件名（默认 run）")
    ap.add_argument("--en-dir", default=DEFAULT_EN_DIR,
                    help=f"英文模型 ingest 目录（默认 {DEFAULT_EN_DIR}）")
    ap.add_argument("--zh-dir", default=DEFAULT_ZH_DIR,
                    help=f"中文模型 ingest 目录（默认 {DEFAULT_ZH_DIR}）")
    ap.add_argument("--en-model", default="en",
                    help="英文模型 alias（默认 en）；切 m3 单库时改 m3")
    ap.add_argument("--zh-model", default="zh",
                    help="中文模型 alias（默认 zh）；切 m3 单库时改 m3")
    ap.add_argument("--no-clean", action="store_true",
                    help="不删除 chroma_db / bm25_index；适合追加 ingest")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="完全跳过 ingest，直接评估当前库（消融实验用）")
    ap.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR,
                    help=f"报告目录（默认 {DEFAULT_REPORTS_DIR}）")
    # 透传给 tools.rag_eval.eval 的常用开关
    ap.add_argument("--k", type=int, default=None,
                    help="评估 top-K（默认沿用 RAG_TOP_K）")
    ap.add_argument("--golden", default=None,
                    help="黄金集 JSON 路径（默认 tools/rag_eval/golden.json）")
    ap.add_argument("--no-rewriter", action="store_true",
                    help="评估时禁用 query 改写（基线对比）")
    ap.add_argument("--no-rerank", action="store_true",
                    help="评估时禁用 reranker（基线对比）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将执行的命令，不实际跑")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    reports_dir = ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{args.label}-{ts}.json"

    logger.info("=" * 60)
    logger.info("RAG 自动化评估  label=%s  ts=%s", args.label, ts)
    logger.info("  英文目录 / 模型 : %s  /  %s", args.en_dir, args.en_model)
    logger.info("  中文目录 / 模型 : %s  /  %s", args.zh_dir, args.zh_model)
    logger.info("  清库            : %s", not args.no_clean)
    logger.info("  跳过 ingest     : %s", args.skip_ingest)
    logger.info("  报告             : %s", report_path)
    logger.info("=" * 60)

    # 1. 清库
    if not args.skip_ingest and not args.no_clean:
        _purge_db(dry_run=args.dry_run)

    # 2&3. 双语种 ingest
    if not args.skip_ingest:
        for docs_dir, model, label in (
            (args.en_dir, args.en_model, "en"),
            (args.zh_dir, args.zh_model, "zh"),
        ):
            cmd = [
                sys.executable, "-m", "src.rag.ingest",
                "--docs-dir", docs_dir, "--model", model,
            ]
            if args.dry_run:
                logger.info("$ (dry-run) %s", " ".join(cmd))
                continue
            _run(cmd, step=f"ingest [{label}] {docs_dir} → {model}")

    # 4. 评估
    _check_golden(use_default=args.golden is None)
    eval_cmd: list[str] = [
        sys.executable, "-m", "tools.rag_eval.eval",
        "--json", str(report_path),
    ]
    if args.k is not None:
        eval_cmd += ["--k", str(args.k)]
    if args.golden:
        eval_cmd += ["--golden", args.golden]
    if args.no_rewriter:
        eval_cmd.append("--no-rewriter")
    if args.no_rerank:
        eval_cmd.append("--no-rerank")
    if args.dry_run:
        logger.info("$ (dry-run) %s", " ".join(eval_cmd))
        logger.info("✓ dry-run 完成，未实际执行任何命令")
        return 0
    _run(eval_cmd, step="eval")

    # 5. 汇总
    summary = _summarize(report_path)
    logger.info("=" * 60)
    logger.info("✓ 评估完成: %s", report_path)
    logger.info("  %s", summary)
    logger.info("=" * 60)
    print(f"\nRESULT[{args.label}] {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
