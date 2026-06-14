"""Phase 2.5 Harness 自检评估器（[§4.9.10 #8](../../../docs/iter_2_agent.md#4910-harness-自检-phase-25)）

判定一件事（对应 Step 0 验收 ⑤）：

- **critic 自身判得准不准** —— 给定 (case input, expected verdict)，跑 critic LLM 调用，看判得对不对
  - `quiz_critic` case：调 [`HarnessManager.review_grading`](../../../src/agent/core/harness_manager.py)，
    比对 `verdict.passed` vs `expected ∈ {"pass", "flag"}`
  - `rag_critic` case：直接走底层 `_RAG_BATCH_*_TEMPLATE` + `_call_chat_for_rag` + `_parse_rag_verdicts`，
    比对解析后的 list[float] vs dataset 的 `expected: list[int]`

主路径产出好坏（grade_quiz 总分 / search_knowledge 召回质量）由 [`tools/agent_eval/quiz/eval_quiz.py`](../quiz/eval_quiz.py)
和 [`tools/rag_eval/`](../../rag_eval/) 评估，本评估器**只评 critic 自身**。

常用命令：

    python -m tools.agent_eval.harness.eval_harness
    python -m tools.agent_eval.harness.eval_harness --case Q01-correct-grading-passes
    python -m tools.agent_eval.harness.eval_harness --no-report
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

# Windows 默认 GBK 控制台 codepage 输出 emoji 会 UnicodeEncodeError；
# 强制 stdout/stderr UTF-8（Python 3.7+ TextIOWrapper.reconfigure 支持）。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

import src.config as config  # noqa: E402
from src.agent.core import harness_manager as hm  # noqa: E402
from src.agent.core.harness_manager import HarnessManager  # noqa: E402


_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"
# 评估通过率阈值（Step 0 验收 ⑤ 要求 ≥ 80%）
_PASS_RATE: float = 0.80


# ── 数据加载 ─────────────────────────────────────────────────────────────────


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        sys.exit(f"❌ 找不到 dataset：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── 单 case 跑 ──────────────────────────────────────────────────────────────


def _run_quiz_critic_case(
    case: dict[str, Any], manager: HarnessManager,
) -> dict[str, Any]:
    """跑 quiz_critic case：调 review_grading，比对 verdict.passed vs expected。"""
    inp = case["input"]
    expected = case["expected"]  # "pass" | "flag"
    try:
        verdict = manager.review_grading(
            stem=inp["stem"],
            user_answer=inp["user_answer"],
            correct_answer=inp["correct_answer"],
            agent_score=float(inp["agent_score"]),
            agent_feedback=inp["agent_feedback"],
        )
    except Exception as e:  # noqa: BLE001
        return {
            "id": case["id"], "category": "quiz_critic", "pass": False,
            "expected": expected, "got": "error",
            "score": None, "reason": f"critic 异常：{e}",
            "note": case.get("note", ""), "error": str(e),
        }

    if verdict.failure:
        got = "failure"
    elif verdict.passed:
        got = "pass"
    else:
        got = "flag"
    case_pass = (got == expected)

    return {
        "id": case["id"], "category": "quiz_critic", "pass": case_pass,
        "expected": expected, "got": got,
        "score": verdict.score, "reason": verdict.reason,
        "note": case.get("note", ""),
    }


def _run_rag_critic_case(
    case: dict[str, Any], manager: HarnessManager,
) -> dict[str, Any]:
    """跑 rag_critic case：构造 K 条 chunks → batch chat → parse → 比 expected。"""
    inp = case["input"]
    query = inp["query"]
    chunks = inp["chunks"]
    expected: list[int] = case["expected"]
    k = len(chunks)
    chunks_text = "\n\n---\n\n".join(
        f"[{i + 1}] {manager._truncate_doc(c)}" for i, c in enumerate(chunks)
    )
    sys_prompt = hm._RAG_BATCH_SYS_TEMPLATE.format(criteria=manager._rag_criteria, k=k)
    user_msg = hm._RAG_BATCH_USER_TEMPLATE.format(
        query=query, k=k, chunks=chunks_text,
    )

    try:
        raw = manager._call_chat_for_rag(sys_prompt=sys_prompt, user_msg=user_msg)
    except Exception as e:  # noqa: BLE001
        return {
            "id": case["id"], "category": "rag_critic", "pass": False,
            "expected": expected, "got": "error",
            "scores": None, "raw": "",
            "note": case.get("note", ""), "error": str(e),
        }

    scores = manager._parse_rag_verdicts(raw, k)
    if scores is None:
        return {
            "id": case["id"], "category": "rag_critic", "pass": False,
            "expected": expected, "got": "parse_error",
            "scores": None, "raw": raw[:300],
            "note": case.get("note", ""),
        }

    got_int = [int(s) for s in scores]
    case_pass = (got_int == expected)
    return {
        "id": case["id"], "category": "rag_critic", "pass": case_pass,
        "expected": expected, "got": got_int,
        "scores": scores, "raw": raw[:300],
        "note": case.get("note", ""),
    }


def _run_case(case: dict[str, Any], manager: HarnessManager) -> dict[str, Any]:
    cat = case.get("category", "")
    if cat == "quiz_critic":
        return _run_quiz_critic_case(case, manager)
    if cat == "rag_critic":
        return _run_rag_critic_case(case, manager)
    return {
        "id": case["id"], "category": cat, "pass": False,
        "expected": case.get("expected"), "got": "unknown_category",
        "note": case.get("note", ""),
    }


# ── 环境信息 + 报告渲染 ─────────────────────────────────────────────────────


def _collect_env() -> dict[str, str]:
    git_part = "?"
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False, timeout=2,
        ).stdout.strip()
        dirty = "*" if subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=2,
        ).stdout.strip() else ""
        if sha:
            git_part = f"{sha}{dirty}"
    except Exception:  # noqa: BLE001
        pass
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git": git_part,
        "python": platform.python_version(),
        "provider": getattr(config, "ACTIVE_MODEL", "?"),
        "threshold": f"{config.HARNESS_GRADING_THRESHOLD:.2f}",
        "timeout": f"{config.HARNESS_LLM_TIMEOUT_SEC:.1f}s",
    }


def _truncate(text: str, n: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …(truncated)"


def _render_markdown(
    results: list[dict[str, Any]],
    passed: int,
    total: int,
    dataset_path: Path,
    env: dict[str, str],
) -> str:
    rate = passed / total if total else 0.0
    verdict = (
        f"✅ 合格 (≥ {_PASS_RATE:.0%})" if rate >= _PASS_RATE
        else f"⚠️ 未达 {_PASS_RATE:.0%} 判据"
    )
    quiz_results = [r for r in results if r.get("category") == "quiz_critic"]
    rag_results = [r for r in results if r.get("category") == "rag_critic"]
    quiz_passed = sum(1 for r in quiz_results if r["pass"])
    rag_passed = sum(1 for r in rag_results if r["pass"])

    lines: list[str] = [
        "# Harness 自检评估报告",
        "",
        f"- **时间**: {env['timestamp']}",
        f"- **Git**: {env['git']}",
        f"- **Python**: {env['python']}",
        f"- **Provider**: {env['provider']}",
        f"- **HARNESS_GRADING_THRESHOLD**: {env['threshold']}",
        f"- **HARNESS_LLM_TIMEOUT_SEC**: {env['timeout']}",
        f"- **Dataset**: `{dataset_path}`",
        "",
        "## 核心指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 样本数 | {total} |",
        f"| 通过数 | {passed} |",
        f"| 通过率 | {rate:.1%} |",
        f"| 判据 (≥ {_PASS_RATE:.0%}) | {verdict} |",
        "",
        "> 评估的是 **critic 自身判得准不准**，主路径产出好坏由 quiz / rag_eval 评估。",
        "",
    ]

    if quiz_results or rag_results:
        lines += ["## 分组指标", "", "| 组 | 通过 / 总数 |", "|---|---|"]
        if quiz_results:
            lines.append(f"| quiz_critic | {quiz_passed} / {len(quiz_results)} |")
        if rag_results:
            lines.append(f"| rag_critic | {rag_passed} / {len(rag_results)} |")
        lines.append("")

    lines += [
        "## 全 case 总览",
        "",
        "| id | category | expected | got | pass |",
        "|---|---|---|---|:-:|",
    ]
    for r in results:
        flag = "✅" if r["pass"] else "❌"
        exp = repr(r.get("expected"))
        got = repr(r.get("got"))
        lines.append(
            f"| `{r['id']}` | {r.get('category', '?')} | {exp} | {got} | {flag} |"
        )
    lines.append("")

    fails = [r for r in results if not r["pass"]]
    lines.append(f"## Fail 用例（{len(fails)}）")
    lines.append("")
    if not fails:
        lines += ["_全部通过，无 fail。_", ""]
    else:
        for idx, r in enumerate(fails, start=1):
            lines.append(f"### {idx}. `{r['id']}` [{r.get('category', '?')}]")
            lines.append("")
            if r.get("note"):
                lines.append(f"- **维度**: {r['note']}")
            lines.append(f"- **expected**: `{r.get('expected')}`")
            lines.append(f"- **got**: `{r.get('got')}`")
            if r.get("score") is not None:
                lines.append(f"- **critic score**: {r['score']:.2f}")
            if r.get("reason"):
                lines.append(f"- **critic reason**: {r['reason']}")
            if r.get("raw"):
                lines += ["", "<details><summary>critic raw 输出</summary>", "",
                          "```", _truncate(r["raw"], 800), "```", "", "</details>"]
            if r.get("error"):
                lines.append(f"- **error**: {r['error']}")
            lines.append("")
    return "\n".join(lines)


def _dump_report(
    results: list[dict[str, Any]],
    passed: int,
    total: int,
    dataset_path: Path,
    env: dict[str, str],
) -> Path:
    from tools.eval_common.report_paths import reports_dir as eval_reports_dir
    reports_dir = eval_reports_dir("harness")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"harness-eval-{ts}.md"
    out.write_text(
        _render_markdown(results, passed, total, dataset_path, env),
        encoding="utf-8",
    )
    return out


# ── 主入口 ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "\n常用命令：\n"
            "  python -m tools.agent_eval.harness.eval_harness                  # 跑全部\n"
            "  python -m tools.agent_eval.harness.eval_harness --case Q01-...   # 单 case\n"
            "  python -m tools.agent_eval.harness.eval_harness --no-report      # 不落盘\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset", type=Path, default=_DEFAULT_DATASET,
        help=f"golden 数据集 JSON 路径（默认: {_DEFAULT_DATASET}）",
    )
    parser.add_argument("--case", type=str, default="", help="只跑指定 id（精确匹配）")
    parser.add_argument("--no-report", action="store_true", help="不落盘 Markdown 报告")
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    if args.case:
        dataset = [c for c in dataset if c["id"] == args.case]
        if not dataset:
            sys.exit(f"❌ 没有 id={args.case} 的 case")

    print(f"\n🧪 Harness 自检评估（{len(dataset)} case）\n")
    print(
        f"   threshold={config.HARNESS_GRADING_THRESHOLD:.2f}, "
        f"timeout={config.HARNESS_LLM_TIMEOUT_SEC:.1f}s, "
        f"provider={config.ACTIVE_MODEL}\n"
    )

    manager = HarnessManager()
    results: list[dict[str, Any]] = []
    for i, case in enumerate(dataset, 1):
        print(f"  [{i:>2}/{len(dataset)}] {case['id']:<40} ... ", end="", flush=True)
        r = _run_case(case, manager)
        results.append(r)
        flag = "✅" if r["pass"] else "❌"
        print(f"{flag}  expected={r.get('expected')!r}  got={r.get('got')!r}")
        if not r["pass"] and r.get("reason"):
            print(f"        · {r['reason']}")

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    rate = passed / total if total else 0.0

    print(
        f"\n📊 通过 {passed}/{total} ({rate:.0%})  "
        f"{'✅ 合格' if rate >= _PASS_RATE else '⚠️  未达判据'}"
    )
    print("")

    if not args.no_report:
        env = _collect_env()
        report = _dump_report(results, passed, total, args.dataset, env)
        print(f"📁 报告已存储：{report}\n")

    sys.exit(0 if rate >= _PASS_RATE else 1)


if __name__ == "__main__":
    main()
