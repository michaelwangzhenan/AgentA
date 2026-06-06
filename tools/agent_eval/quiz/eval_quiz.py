"""
Phase 2.3 Quiz 业务评估器（[§4.9.8 #8](../../../docs/iter_2_agent.md#498-quiz-出题-phase-23)）

判定两件事（对应 Step 0 验收 ① + ②）：

1. **触发识别**：给定用户输入 → LLM 是否在第一轮就调对 tool
   - `create` case（出题需求）：应调 `make_plan`（嵌套 4 步落库）或 `create_quiz`（一次性落库，弱形式）
   - `history` case（查 quiz 历史 / 错题复盘）：应调 `query_quiz_history`
   - `negative` case（闲聊 / 定义查询 / 学习计划场景）：**不应**调 `create_quiz` / `grade_quiz` / `query_quiz_history`
2. **plan 质量**：create 通过且调 `make_plan` 的 case，把 plan steps 喂给 [`judge_with_llm`](../judge/llm_judge.py)
   按 "意图解析 / KB 检索 / 出题组织 / 落库步骤" 评 0-5 分，验收 ① 阈值 ≥ 4.0。

为什么 single-step 而非 e2e：
    完整 Agent.run() 跑 quiz 落库会经历 ≥ 4 轮（解析意图 / 检索 KB / 组题 / 落库）+ 真的写库
    （清理麻烦）。本评估只关心"LLM 看到 quiz-maker skill + 用户输入后第一轮决策对不对"，
    单步 chat() + 解析 tool_call 已足够覆盖 ① 验收主路径。落库逻辑 / 批改 / 跨 session 查询
    由 UT 覆盖（[`tests/test_quiz_store.py`](../../../tests/test_quiz_store.py)
    + [`tests/test_quiz_tools.py`](../../../tests/test_quiz_tools.py)）。

常用命令：

    python -m tools.agent_eval.quiz.eval_quiz
    python -m tools.agent_eval.quiz.eval_quiz --case Q01-create-rag
    python -m tools.agent_eval.quiz.eval_quiz --no-judge
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

import src.config as config  # noqa: E402
from src.agent.tools import get_tools  # noqa: E402
from src.llm.provider import chat  # noqa: E402
from tools.agent_eval.judge import judge_with_llm  # noqa: E402


_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"
# plan 质量 judge 分阈值（Step 0 验收 ① 要求 ≥ 4/5）
_PLAN_QUALITY_PASS_SCORE: float = 4.0
# 触发识别通过率阈值
_RECALL_PASS_RATE: float = 0.80


# 评估用 system prompt：模拟 quiz-maker skill 已激活 + study-planner skill 也常驻
# （生产 Agent 按 catalog 自动激活；评估为可重复用最小 prompt 模拟）
_EVAL_SYSTEM_PROMPT = """你是用户的个人学习助手，可调用四类工具：
- 基础检索：search_knowledge / web_search / fetch_url
- 通用 plan-execute：make_plan / update_step / abort_plan
- 学习计划业务：create_study_plan / update_study_progress / query_study_status
- Quiz 业务：create_quiz / grade_quiz / query_quiz_history

## 何时新建 quiz（必看）

当用户表达**自检出题需求**（如"考考我 RAG"/"出 5 道 ML 题"/"把 active 学习计划 stage 2 出成题"），
你应当帮用户落一份**跨 session 持久化的 quiz**，按 4 步走（D5 嵌套）：

1. 调 `make_plan(steps=[解析意图 / 查 KB / 组织题目 / 落库 4 步])`
2. 各步按 plan 顺序执行，最后调 `create_quiz(topic / plan_id?, questions=[...])` 落库

题数：用户指定按用户；未指定默认 10 道。题型按固定 60% MCQ + 40% 简答比例混合。

## 何时查 quiz 历史 / 复盘

- "我做过哪些 quiz" → 调 `query_quiz_history()` 不传参
- "上次 quiz 哪些错了 / 看 quiz X" → 调 `query_quiz_history(quiz_set_id=X, detail=true)`

## 何时**不要**触发 quiz 工具

- 单一事实问答（"X 是什么"）→ 应 search_knowledge
- 闲聊 → 直接回答
- 学习计划新建需求（"我想 N 周准备 X"）→ 触发 create_study_plan / make_plan，**不是** quiz
- 概念解释 / 对比 → search_knowledge / web_search
"""


# LLM-judge 评分维度（quiz 生成 plan 质量）
_PLAN_QUALITY_CRITERIA = """- **意图解析**（满分 1）：第一步明确解析 topic / plan_id / stage_idx 三入口，含题数判断。
- **KB 检索**（满分 1.5）：包含查 KB 步骤（search_knowledge / 检索素材），作为出题事实依据。
- **出题组织**（满分 1.5）：包含按比例组题步骤（60% MCQ + 40% 简答，题干 + 选项 + 标答 + 考点）。
- **落库步骤**（满分 1）：最后一步明确调 create_quiz 落库；不只停在"输出题目"。"""


# ── 数据加载 ─────────────────────────────────────────────────────────────────


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        sys.exit(f"❌ 找不到 dataset：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── tool_call 解析 ──────────────────────────────────────────────────────────


def _extract_first_tool_call(message: Any) -> tuple[str, dict[str, Any]] | None:
    """从 chat completion message 中抽出第一个 tool_call 的 (name, args)；无则 None。"""
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return None
    tc = tool_calls[0]
    fn = getattr(tc, "function", None)
    if fn is None:
        return None
    name = getattr(fn, "name", "") or ""
    raw_args = getattr(fn, "arguments", "") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        if not isinstance(args, dict):
            args = {}
    except (json.JSONDecodeError, TypeError):
        args = {}
    return name, args


def _extract_all_tool_call_names(message: Any) -> list[str]:
    """收集本轮所有 tool_call 的 name，便于报告里展示 LLM 实际选了什么。"""
    out: list[str] = []
    for tc in getattr(message, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        if fn is not None and getattr(fn, "name", None):
            out.append(fn.name)
    return out


# ── 判定 1：触发识别 ─────────────────────────────────────────────────────────


def _judge_recall(
    case: dict[str, Any], first_tool: tuple[str, dict[str, Any]] | None,
) -> tuple[bool, list[str]]:
    """根据 case 期望判断是否通过触发识别。"""
    category = case.get("category", "")
    first_name = first_tool[0] if first_tool else ""

    if category in ("create", "history"):
        expected = case.get("expected_first_tool", [])
        if first_name in expected:
            return True, [f"识别 ✓ 第一轮调 `{first_name}`"]
        if not first_tool:
            return False, [f"识别 ✗ LLM 第一轮无 tool_call（应调 {expected}）"]
        return False, [f"识别 ✗ 第一轮调 `{first_name}`（期望 {expected}）"]

    if category == "negative":
        forbidden = case.get("expected_first_tool_not", [])
        if first_name in forbidden:
            return False, [f"识别 ✗ 第一轮调 `{first_name}`（禁用 {forbidden}）"]
        return True, [f"识别 ✓ 第一轮{'无 tool_call' if not first_tool else f'调 `{first_name}`'}（未触发禁用）"]

    return False, [f"未知 category: {category!r}"]


# ── 判定 2：plan 质量 LLM-judge（仅 create 通过且调 make_plan 的 case） ────


def _judge_plan_quality(
    question: str, steps: list[str],
) -> tuple[float | None, str]:
    """对 make_plan 嵌套出的 steps 评 quiz 生成 plan 质量分（0-5）。"""
    plan_block = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(steps))
    res = judge_with_llm(
        role_intro="你是一个 quiz 生成计划质量评委",
        prompt=question,
        output=plan_block,
        criteria=_PLAN_QUALITY_CRITERIA,
    )
    return res.score, res.reason


# ── 单 case 跑 ──────────────────────────────────────────────────────────────


def _run_case(case: dict[str, Any], judge_enabled: bool) -> dict[str, Any]:
    """跑单个 case：single-step chat → 解析 tool_call → recall + 可选 quality judge。"""
    skill_bodies: dict[str, str] = {}
    tools = get_tools(skill_bodies)
    messages = [
        {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
        {"role": "user", "content": case["question"]},
    ]

    try:
        resp = chat(messages, tools=tools, temperature=0.2)
        message = resp.choices[0].message
        first = _extract_first_tool_call(message)
        all_tool_names = _extract_all_tool_call_names(message)
        answer = message.content or ""
    except Exception as e:  # noqa: BLE001
        return {
            "id": case["id"], "pass": False, "category": case.get("category", ""),
            "question": case["question"], "answer": "",
            "first_tool": "", "first_tool_args": {}, "all_tool_calls": [],
            "judge_score": None, "judge_reason": "",
            "reasons": [f"LLM 调用失败: {e}"], "error": str(e),
        }

    passed, reasons = _judge_recall(case, first)
    first_name = first[0] if first else ""
    first_args = first[1] if first else {}

    judge_score: float | None = None
    judge_reason: str = ""
    if (passed and case.get("judge_after") and case.get("category") == "create"
            and first_name == "make_plan" and judge_enabled):
        steps_raw = first_args.get("steps")
        if isinstance(steps_raw, list) and steps_raw:
            judge_score, judge_reason = _judge_plan_quality(case["question"], steps_raw)
            if judge_score is not None:
                reasons.append(f"plan-quality: {judge_score:.1f}/5 — {judge_reason}")
            else:
                reasons.append(f"plan-quality: judge 失败 — {judge_reason}")
        else:
            reasons.append("plan-quality: make_plan 未给 steps 列表，跳过 judge")

    return {
        "id": case["id"],
        "pass": passed,
        "category": case.get("category", ""),
        "question": case["question"],
        "answer": answer,
        "first_tool": first_name,
        "first_tool_args": first_args,
        "all_tool_calls": all_tool_names,
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
        "provider": getattr(config, "ACTIVE_MODEL", "?"),
    }


def _truncate(text: str, n: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …(truncated)"


def _render_markdown(
    results: list[dict[str, Any]],
    passed: int,
    total: int,
    avg_quality: float | None,
    dataset_path: Path,
    env: dict[str, str],
    judge_enabled: bool,
) -> str:
    rate = passed / total if total else 0.0
    recall_verdict = (
        f"✅ 合格 (≥ {_RECALL_PASS_RATE:.0%})" if rate >= _RECALL_PASS_RATE
        else f"⚠️ 未达 {_RECALL_PASS_RATE:.0%} 判据"
    )
    lines: list[str] = [
        "# Quiz 业务 触发识别 + 质量评估报告",
        "",
        f"- **时间**: {env['timestamp']}",
        f"- **Git**: {env['git']}",
        f"- **Python**: {env['python']}",
        f"- **Provider**: {env['provider']}",
        f"- **Dataset**: `{dataset_path}`",
        f"- **LLM-judge**: {'开启' if judge_enabled else '关闭'}",
        "",
        "## 核心指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 样本数 | {total} |",
        f"| 识别通过数 | {passed} |",
        f"| 识别通过率 | {rate:.1%} |",
        f"| 识别判据 (≥ {_RECALL_PASS_RATE:.0%}) | {recall_verdict} |",
    ]
    if avg_quality is not None:
        verdict = (
            f"✅ 合格 (≥ {_PLAN_QUALITY_PASS_SCORE})"
            if avg_quality >= _PLAN_QUALITY_PASS_SCORE
            else f"⚠️ 未达 {_PLAN_QUALITY_PASS_SCORE} 判据"
        )
        lines.append(f"| plan 质量均分 (create 通过) | {avg_quality:.2f}/5 |")
        lines.append(f"| 质量判据 (≥ {_PLAN_QUALITY_PASS_SCORE}) | {verdict} |")
    lines.append("")

    creates = [r for r in results if r.get("category") == "create"]
    histories = [r for r in results if r.get("category") == "history"]
    negs = [r for r in results if r.get("category") == "negative"]
    if creates or histories or negs:
        lines += ["## 分组指标", "", "| 组 | 通过 / 总数 |", "|---|---|"]
        if creates:
            lines.append(f"| create | {sum(1 for r in creates if r['pass'])} / {len(creates)} |")
        if histories:
            lines.append(f"| history | {sum(1 for r in histories if r['pass'])} / {len(histories)} |")
        if negs:
            lines.append(f"| negative | {sum(1 for r in negs if r['pass'])} / {len(negs)} |")
        lines.append("")

    lines += [
        "## 全 case 总览",
        "",
        "| id | category | 第一轮 tool | 质量分 | pass |",
        "|---|---|---|---|:-:|",
    ]
    for r in results:
        flag = "✅" if r["pass"] else "❌"
        first = f"`{r['first_tool']}`" if r["first_tool"] else "—"
        score = f"{r['judge_score']:.1f}/5" if r.get("judge_score") is not None else "—"
        lines.append(f"| `{r['id']}` | {r.get('category', '?')} | {first} | {score} | {flag} |")
    lines.append("")

    create_passed = [r for r in creates if r["pass"] and r.get("first_tool") == "make_plan"]
    if create_passed:
        lines += [f"## Create 通过 case 的 plan 详情（{len(create_passed)}）", ""]
        for r in create_passed:
            lines.append(f"### `{r['id']}`")
            lines.append("")
            lines.append(f"- **question**: {r['question']}")
            if r.get("judge_score") is not None:
                lines.append(f"- **quality**: {r['judge_score']:.1f}/5 — {r['judge_reason']}")
            steps = r.get("first_tool_args", {}).get("steps") or []
            lines.append("- **plan steps**:")
            for i, s in enumerate(steps, start=1):
                lines.append(f"  {i}. {s}")
            lines.append("")

    fails = [r for r in results if not r["pass"]]
    lines.append(f"## Fail 用例（{len(fails)}）")
    lines.append("")
    if not fails:
        lines += ["_全部通过，无 fail。_", ""]
    else:
        for idx, r in enumerate(fails, start=1):
            lines.append(f"### {idx}. `{r['id']}`")
            lines.append("")
            if r.get("note"):
                lines.append(f"- **维度**: {r['note']}")
            lines.append(f"- **question**: {r['question']}")
            for line in r.get("reasons", []):
                lines.append(f"- {line}")
            if r.get("all_tool_calls"):
                lines.append(f"- **all tool_calls**: {r['all_tool_calls']!r}")
            if r.get("error"):
                lines.append(f"- **error**: `{r['error']}`")
            if r.get("answer"):
                lines += [
                    "", "<details><summary>LLM answer</summary>", "",
                    "```", _truncate(r["answer"], 800), "```", "", "</details>",
                ]
            lines.append("")
    return "\n".join(lines)


def _dump_report(
    results: list[dict[str, Any]],
    passed: int,
    total: int,
    avg_quality: float | None,
    dataset_path: Path,
    env: dict[str, str],
    judge_enabled: bool,
) -> Path:
    reports_dir = Path(__file__).parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"quiz-eval-{ts}.md"
    out.write_text(
        _render_markdown(results, passed, total, avg_quality, dataset_path, env, judge_enabled),
        encoding="utf-8",
    )
    return out


# ── 主入口 ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "\n常用命令：\n"
            "  python -m tools.agent_eval.quiz.eval_quiz                  # 跑全部\n"
            "  python -m tools.agent_eval.quiz.eval_quiz --case Q01-...   # 单 case\n"
            "  python -m tools.agent_eval.quiz.eval_quiz --no-judge       # 不调 LLM-judge\n"
            "  python -m tools.agent_eval.quiz.eval_quiz --no-report      # 不落盘\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset", type=Path, default=_DEFAULT_DATASET,
        help=f"golden 数据集 JSON 路径（默认: {_DEFAULT_DATASET}）",
    )
    parser.add_argument("--case", type=str, default="", help="只跑指定 id（精确匹配）")
    parser.add_argument("--no-report", action="store_true", help="不落盘 Markdown 报告")
    parser.add_argument("--no-judge", action="store_true", help="不调 LLM-judge")
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    if args.case:
        dataset = [c for c in dataset if c["id"] == args.case]
        if not dataset:
            sys.exit(f"❌ 没有 id={args.case} 的 case")

    judge_enabled = not args.no_judge
    print(
        f"\n🧪 Quiz 业务评估（{len(dataset)} case，"
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
    judged_scores = [
        r["judge_score"] for r in results
        if r["pass"] and r.get("category") == "create" and r.get("judge_score") is not None
    ]
    avg_quality: float | None = (
        sum(judged_scores) / len(judged_scores) if judged_scores else None
    )

    print(
        f"\n📊 识别通过 {passed}/{total} ({rate:.0%})  "
        f"{'✅ 合格' if rate >= _RECALL_PASS_RATE else '⚠️  未达判据'}"
    )
    if avg_quality is not None:
        q_ok = avg_quality >= _PLAN_QUALITY_PASS_SCORE
        print(
            f"📐 plan 质量均分 {avg_quality:.2f}/5  "
            f"{'✅ 合格' if q_ok else '⚠️  未达判据'}"
        )
    print("")

    if not args.no_report:
        env = _collect_env()
        report = _dump_report(results, passed, total, avg_quality, args.dataset, env, judge_enabled)
        print(f"📁 报告已存储：{report}\n")

    ok_recall = rate >= _RECALL_PASS_RATE
    ok_quality = (avg_quality is None) or (avg_quality >= _PLAN_QUALITY_PASS_SCORE)
    sys.exit(0 if (ok_recall and ok_quality) else 1)


if __name__ == "__main__":
    main()
