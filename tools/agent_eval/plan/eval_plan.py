"""
Plan Recall + Structure 评估 (Phase 2.1)

回答两个问题：

1. **Plan 识别准确率**：LLM 在面对复杂任务时是否**主动调** `make_plan`（positive 判定）；
   面对简单任务是否**不调** make_plan（negative 判定）。
2. **Plan 结构质量**：positive 已通过的 case，进一步由 LLM-judge 对生成的 plan steps
   评 0-5 分（按粒度 / 顺序 / 覆盖度 / 业务对齐度）。

对应 Step 0 验收（[iter_2.md §4.9.6](../../../docs/iter_2.md#496-agent-循环升级-phase-21)）：
    ① 复杂任务可见 plan；
    ② 简单任务不被强行 plan；
    ③ 失败步可标记/跳过（本评估覆盖识别+结构，失败语义由 e2e UT 覆盖）。

判据：
- 综合通过率 ≥ 80%（识别层面）
- positive 通过 case 的平均 plan 结构得分 ≥ 3.5/5

为什么不直接跑 Agent.run()：
    本评估只关心"第一轮 LLM 是否调 make_plan + 拿到的 plan 结构合不合理"。run() 会跑完整
    plan 循环，噪音、耗时、API 成本都高；单步 chat() + 解析 make_plan tool_call 已足够覆盖
    Step 0 验收 ①②。失败语义由 `tests/test_agent.py::TestAgentPlanExecuteE2E` 三个用例覆盖。

为什么不复用 src.agent.agent.SYSTEM_PROMPT（同 recall_skill）：
    eval 用独立 `_BASE_PROMPT`，跟 SYSTEM_PROMPT 后续改动解耦，避免行为漂移。但本评估需要
    包含完整的「何时使用 make_plan」教学段（不然 LLM 不知道有这工具），所以 _BASE_PROMPT
    内嵌该段；改 SYSTEM_PROMPT 时若改动 plan 教学逻辑，需同步更新本文件 `_BASE_PROMPT`。

使用：
    python -m tools.agent_eval.plan.eval_plan                              # 跑全部
    python -m tools.agent_eval.plan.eval_plan --case P01-positive-...      # 单 case
    python -m tools.agent_eval.plan.eval_plan --dataset path/to.json       # 自定义 golden
    python -m tools.agent_eval.plan.eval_plan --no-report                  # 不落盘
    python -m tools.agent_eval.plan.eval_plan --no-judge                   # 不调 LLM-judge（只看识别）
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

import src.config as config  # noqa: E402
from src.agent.tools import get_tools  # noqa: E402
from src.llm.provider import chat  # noqa: E402


_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"
# plan 结构得分阈值（positive 通过 case 的平均分必须 ≥ 此值）
_PLAN_STRUCTURE_PASS_SCORE: float = 3.5
# 综合通过率阈值（识别层面）
_RECALL_PASS_RATE: float = 0.80

# Eval 用 base prompt — 内嵌「何时使用 make_plan」教学段，独立于 SYSTEM_PROMPT
# 改动 SYSTEM_PROMPT 中相同主题段时需同步更新此处，避免 eval 与运行时分歧。
_BASE_PROMPT = """你是一个善于使用工具的 AI 助手。可用工具包含：search_knowledge / web_search / fetch_url
等业务 tool，以及 plan-execute 三件套 make_plan / update_step / abort_plan。

## 何时使用 make_plan

当任务符合下列**任一**复杂任务特征时，**先调用** `make_plan(steps=[...])` 列出 3-6 步计划，再分步执行：

1. 多文档对比 / 多源资料综合
2. 学习计划 / 目标规划
3. 目标 + 多步骤型（先分析后给建议、先调研后推荐）
4. 涉及 ≥3 个独立子查询

**简单任务（不要 make_plan）**：

1. 单实体查询（"我的邮箱"、"AgentA 是什么"）
2. 单一事实回答
3. 闲聊 / 礼貌用语
4. 多轮上下文里的简单追问（"再展开一下"）

简单任务请**直接回答**或最多调一次业务 tool；不要为了显得复杂而强行 make_plan。
"""

# LLM-judge prompt：评 plan 结构（粒度 / 顺序 / 覆盖度 / 业务对齐）
_JUDGE_PROMPT = """你是一个 Agent plan-execute 流程的评委。下面给你看：
1. 用户问题
2. 一个 LLM 自动生成的 plan（按编号顺序列出的 3-7 个步骤）

请按以下 4 个维度评分（每项 0/0.5/1，加权后总分 0-5）：

- **粒度合适**（满分 1.5）：每步动作明确、可独立执行；既不过于宽泛（"先研究一下"）也不过于琐碎（"打开浏览器"）。
- **顺序合理**（满分 1）：前后依赖关系正确，关键步骤不缺位。
- **覆盖度**（满分 1.5）：完成 plan 后能产出用户真正想要的答案；不缺少综合/对比/总结这类收口步骤。
- **业务对齐**（满分 1）：步骤与用户问题语义对齐，没有跑题或自我重复。

输出**严格的 JSON**，格式：

{"score": <0.0-5.0 浮点>, "reason": "<≤ 80 字简评>"}

只输出 JSON，不要带 markdown 代码块、不要前后说明。"""


# ── 数据加载 ─────────────────────────────────────────────────────────────────


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        sys.exit(f"❌ 找不到 dataset：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── make_plan tool_call 解析 ─────────────────────────────────────────────────


def _extract_make_plan_args(message: Any) -> dict[str, Any] | None:
    """从 chat completion message 中抽出第一个 make_plan 调用的 args；无则返回 None。"""
    tool_calls = getattr(message, "tool_calls", None) or []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        if fn is None or getattr(fn, "name", None) != "make_plan":
            continue
        raw_args = getattr(fn, "arguments", "") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {}
        return args or {}
    return None


def _extract_other_tool_names(message: Any) -> list[str]:
    """除 make_plan 外其它 tool 调用名（debug / 报告用）。"""
    tool_calls = getattr(message, "tool_calls", None) or []
    names: list[str] = []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", "") if fn else ""
        if name and name != "make_plan":
            names.append(name)
    return names


# ── 判定 1：plan recall（识别层）────────────────────────────────────────────


def _judge_recall(
    case: dict[str, Any], make_plan_args: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    """返回 (pass, reasons)。category 决定判据。"""
    category = case.get("category", "positive")
    reasons: list[str] = []

    if category == "positive":
        if make_plan_args is None:
            return False, ["make_plan: ❌ 期望调用，但 LLM 未调"]
        steps = make_plan_args.get("steps", [])
        if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
            return False, [f"make_plan: ❌ steps 字段非法：{steps!r}"]
        min_steps = int(case.get("min_steps", 1))
        max_steps = int(case.get("max_steps", 99))
        n = len(steps)
        if not (min_steps <= n <= max_steps):
            return False, [
                f"make_plan: ❌ 步数 {n} 不在 [{min_steps}, {max_steps}]：{steps!r}"
            ]
        reasons.append(f"make_plan: ✓ 调用 + 步数 {n} 在 [{min_steps}, {max_steps}]")
        return True, reasons

    if category == "negative":
        if make_plan_args is not None:
            return False, [
                f"make_plan: ❌ 期望不调，实际调了 steps={make_plan_args.get('steps', [])!r}"
            ]
        reasons.append("make_plan: ✓ 未触发（符合预期）")
        return True, reasons

    return False, [f"未知 category: {category!r}"]


# ── 判定 2：plan 结构 LLM-judge（仅 positive 通过 case 评分）─────────────────


def _llm_judge_plan_structure(
    question: str, steps: list[str],
) -> tuple[float | None, str]:
    """调一次 LLM judge，返回 (score, reason)。失败时返回 (None, error_msg)。"""
    plan_block = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(steps))
    user_msg = (
        f"## 用户问题\n{question}\n\n"
        f"## LLM 生成的 plan\n{plan_block}\n\n"
        "请按上面 4 个维度评分，输出严格 JSON。"
    )
    judge_msgs = [
        {"role": "system", "content": _JUDGE_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    try:
        resp = chat(judge_msgs, temperature=0.0)
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        return None, f"judge LLM 调用失败：{e}"

    # 容错解析：剥离 markdown 代码块，仅取第一段 {...}
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        return None, f"judge 返回非 JSON：{raw[:200]!r}"
    try:
        data = json.loads(m.group(0))
        score = float(data.get("score", -1))
        if not (0.0 <= score <= 5.0):
            return None, f"judge score 越界：{score}"
        reason = str(data.get("reason", "")).strip() or "（无）"
        return score, reason
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return None, f"judge JSON 解析失败：{e}；raw={raw[:200]!r}"


# ── 单 case 执行 ─────────────────────────────────────────────────────────────


def _run_case(case: dict[str, Any], judge_enabled: bool) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": _BASE_PROMPT},
        {"role": "user", "content": case["question"]},
    ]
    # 不传 skill_bodies — eval 只看 plan 行为
    tools = get_tools()

    try:
        resp = chat(messages, tools=tools, temperature=0.2)
        message = resp.choices[0].message
        make_plan_args = _extract_make_plan_args(message)
        other_calls = _extract_other_tool_names(message)
        answer = (getattr(message, "content", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        return {
            "id": case["id"],
            "pass": False,
            "category": case.get("category", "positive"),
            "question": case["question"],
            "answer": "",
            "make_plan_steps": [],
            "other_tool_calls": [],
            "judge_score": None,
            "judge_reason": "",
            "reasons": [f"LLM 调用失败: {e}"],
            "error": str(e),
            "note": case.get("note", ""),
        }

    passed, reasons = _judge_recall(case, make_plan_args)
    steps: list[str] = list(make_plan_args.get("steps", [])) if make_plan_args else []

    judge_score: float | None = None
    judge_reason: str = ""
    if passed and case.get("category") == "positive" and steps and judge_enabled:
        judge_score, judge_reason = _llm_judge_plan_structure(case["question"], steps)
        if judge_score is not None:
            reasons.append(
                f"plan-structure: {judge_score:.1f}/5 — {judge_reason}"
            )
        else:
            reasons.append(f"plan-structure: judge 失败 — {judge_reason}")

    return {
        "id": case["id"],
        "pass": passed,
        "category": case.get("category", "positive"),
        "question": case["question"],
        "answer": answer,
        "make_plan_steps": steps,
        "other_tool_calls": other_calls,
        "judge_score": judge_score,
        "judge_reason": judge_reason,
        "reasons": reasons,
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
        "provider": getattr(config, "ACTIVE_PROVIDER", "?"),
    }


def _truncate(text: str, n: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …(truncated)"


def _render_markdown(
    results: list[dict[str, Any]],
    passed: int,
    total: int,
    avg_judge: float | None,
    dataset_path: Path,
    env: dict[str, str],
    judge_enabled: bool,
) -> str:
    rate = passed / total if total else 0.0
    recall_verdict = (
        f"✅ 合格 (≥ {_RECALL_PASS_RATE:.0%})" if rate >= _RECALL_PASS_RATE
        else f"⚠️ 未达 {_RECALL_PASS_RATE:.0%} 判据"
    )

    lines: list[str] = []
    lines.append("# Plan Recall + Structure 评估报告")
    lines.append("")
    lines.append(f"- **时间**: {env['timestamp']}")
    lines.append(f"- **Git**: {env['git']}")
    lines.append(f"- **Python**: {env['python']}")
    lines.append(f"- **Provider**: {env['provider']}")
    lines.append(f"- **Dataset**: `{dataset_path}`")
    lines.append(f"- **LLM-judge**: {'开启' if judge_enabled else '关闭'}")
    lines.append("")

    lines.append("## 核心指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 样本数 | {total} |")
    lines.append(f"| 识别通过数 | {passed} |")
    lines.append(f"| 识别通过率 | {rate:.1%} |")
    lines.append(f"| 识别判据 (≥ {_RECALL_PASS_RATE:.0%}) | {recall_verdict} |")
    if avg_judge is not None:
        struct_verdict = (
            f"✅ 合格 (≥ {_PLAN_STRUCTURE_PASS_SCORE})"
            if avg_judge >= _PLAN_STRUCTURE_PASS_SCORE
            else f"⚠️ 未达 {_PLAN_STRUCTURE_PASS_SCORE} 判据"
        )
        lines.append(f"| plan 结构均分 (positive 通过) | {avg_judge:.2f}/5 |")
        lines.append(f"| 结构判据 (≥ {_PLAN_STRUCTURE_PASS_SCORE}) | {struct_verdict} |")
    lines.append("")

    pos = [r for r in results if r.get("category") == "positive"]
    neg = [r for r in results if r.get("category") == "negative"]
    if pos or neg:
        lines.append("## 分组指标")
        lines.append("")
        lines.append("| 组 | 通过 / 总数 |")
        lines.append("|---|---|")
        if pos:
            lines.append(f"| positive | {sum(1 for r in pos if r['pass'])} / {len(pos)} |")
        if neg:
            lines.append(f"| negative | {sum(1 for r in neg if r['pass'])} / {len(neg)} |")
        lines.append("")

    lines.append("## 全 case 总览")
    lines.append("")
    lines.append("| id | category | make_plan 步数 | 结构分 | pass |")
    lines.append("|---|---|---|---|:-:|")
    for r in results:
        flag = "✅" if r["pass"] else "❌"
        n = len(r.get("make_plan_steps") or []) or "—"
        score = (
            f"{r['judge_score']:.1f}/5" if r.get("judge_score") is not None else "—"
        )
        lines.append(
            f"| `{r['id']}` | {r.get('category', '?')} | {n} | {score} | {flag} |"
        )
    lines.append("")

    # positive 通过 case 的 plan 全文（便于人工 spot check）
    pos_passed = [r for r in pos if r["pass"]]
    if pos_passed:
        lines.append(f"## Positive 通过 case 的 plan 详情（{len(pos_passed)}）")
        lines.append("")
        for r in pos_passed:
            lines.append(f"### `{r['id']}`")
            lines.append("")
            lines.append(f"- **question**: {r['question']}")
            if r.get("judge_score") is not None:
                lines.append(f"- **structure**: {r['judge_score']:.1f}/5 — {r['judge_reason']}")
            lines.append("- **plan**:")
            for i, s in enumerate(r.get("make_plan_steps") or [], start=1):
                lines.append(f"  {i}. {s}")
            lines.append("")

    fails = [r for r in results if not r["pass"]]
    lines.append(f"## Fail 用例（{len(fails)}）")
    lines.append("")
    if not fails:
        lines.append("_全部通过，无 fail。_")
        lines.append("")
    else:
        for idx, r in enumerate(fails, start=1):
            lines.append(f"### {idx}. `{r['id']}`")
            lines.append("")
            if r.get("note"):
                lines.append(f"- **维度**: {r['note']}")
            lines.append(f"- **question**: {r['question']}")
            for line in r.get("reasons", []):
                lines.append(f"- {line}")
            if r.get("other_tool_calls"):
                lines.append(f"- **other tool_calls**: {r['other_tool_calls']!r}")
            if r.get("error"):
                lines.append(f"- **error**: `{r['error']}`")
            if r.get("answer"):
                lines.append("")
                lines.append("<details><summary>LLM answer</summary>")
                lines.append("")
                lines.append("```")
                lines.append(_truncate(r["answer"], 800))
                lines.append("```")
                lines.append("")
                lines.append("</details>")
            lines.append("")

    return "\n".join(lines)


def _dump_report(
    results: list[dict[str, Any]],
    passed: int,
    total: int,
    avg_judge: float | None,
    dataset_path: Path,
    env: dict[str, str],
    judge_enabled: bool,
) -> Path:
    reports_dir = Path(__file__).parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"plan-eval-{ts}.md"
    out.write_text(
        _render_markdown(results, passed, total, avg_judge, dataset_path, env, judge_enabled),
        encoding="utf-8",
    )
    return out


# ── 主入口 ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "\n常用命令：\n"
            "  python -m tools.agent_eval.plan.eval_plan                              # 跑全部\n"
            "  python -m tools.agent_eval.plan.eval_plan --case P01-positive-...      # 单 case\n"
            "  python -m tools.agent_eval.plan.eval_plan --dataset path/to.json       # 自定义 golden\n"
            "  python -m tools.agent_eval.plan.eval_plan --no-report                  # 不落盘\n"
            "  python -m tools.agent_eval.plan.eval_plan --no-judge                   # 不调 LLM-judge\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset", type=Path, default=_DEFAULT_DATASET,
        help=f"golden 数据集 JSON 路径（默认: {_DEFAULT_DATASET}）",
    )
    parser.add_argument("--case", type=str, default="", help="只跑指定 id（精确匹配）")
    parser.add_argument("--no-report", action="store_true", help="不落盘 Markdown 报告")
    parser.add_argument("--no-judge", action="store_true", help="不调 LLM-judge 评 plan 结构分")
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    if args.case:
        dataset = [c for c in dataset if c["id"] == args.case]
        if not dataset:
            sys.exit(f"❌ 没有 id={args.case} 的 case")

    judge_enabled = not args.no_judge

    print(
        f"\n🧪 Plan Recall + Structure 评估（{len(dataset)} case，"
        f"LLM-judge {'开' if judge_enabled else '关'}）\n"
    )

    results: list[dict[str, Any]] = []
    for i, case in enumerate(dataset, 1):
        print(f"  [{i:>2}/{len(dataset)}] {case['id']} ... ", end="", flush=True)
        r = _run_case(case, judge_enabled)
        results.append(r)
        flag = "✅" if r["pass"] else "❌"
        print(flag)
        for line in r["reasons"]:
            print(f"        · {line}")

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    rate = passed / total if total else 0.0

    # plan 结构均分（仅 positive 通过且 judge 成功的 case）
    judged_scores = [
        r["judge_score"] for r in results
        if r["pass"] and r.get("category") == "positive" and r.get("judge_score") is not None
    ]
    avg_judge: float | None = (
        sum(judged_scores) / len(judged_scores) if judged_scores else None
    )

    print(
        f"\n📊 识别通过 {passed}/{total} ({rate:.0%})  "
        f"{'✅ 合格' if rate >= _RECALL_PASS_RATE else '⚠️  未达判据'}"
    )
    if avg_judge is not None:
        struct_ok = avg_judge >= _PLAN_STRUCTURE_PASS_SCORE
        print(
            f"📐 plan 结构均分 {avg_judge:.2f}/5  "
            f"{'✅ 合格' if struct_ok else '⚠️  未达判据'}"
        )
    print("")

    if not args.no_report:
        env = _collect_env()
        report = _dump_report(results, passed, total, avg_judge, args.dataset, env, judge_enabled)
        print(f"📁 报告已存储：{report}\n")

    # 退出码：识别通过率 + 结构均分都达标才 0
    ok_recall = rate >= _RECALL_PASS_RATE
    ok_struct = (avg_judge is None) or (avg_judge >= _PLAN_STRUCTURE_PASS_SCORE)
    sys.exit(0 if (ok_recall and ok_struct) else 1)


if __name__ == "__main__":
    main()
