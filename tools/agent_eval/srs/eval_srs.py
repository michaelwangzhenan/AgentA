"""
Phase 2.4 SRS 业务评估器（[§4.9.9 #7](../../../docs/iter_2_agent.md#499-srs-主动复习调度-phase-24)）

判定一件事（对应 Step 0 验收 ③）：

- **触发识别**：给定用户输入 → LLM 是否在第一轮就调对 tool
  - `due` case：应调 `query_srs_due`
  - `add` case：应调 `add_to_srs`
  - `review` case：应调 `review_srs_card`
  - `negative` case：**不应**调 SRS 四 tool

SM-2 算法本身的正确性由 UT（[`tests/test_srs_scheduler.py`](../../../tests/test_srs_scheduler.py)
40 case）覆盖；本评估只关心"LLM 看到 srs-review skill + 用户输入后第一轮决策对不对"，
**不调 LLM-judge**（D6 决策：review path 算法对齐由 UT 保，evaluator 只测触发识别率）。

常用命令：

    python -m tools.agent_eval.srs.eval_srs
    python -m tools.agent_eval.srs.eval_srs --case S01-due-today
    python -m tools.agent_eval.srs.eval_srs --no-report
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


_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"
# 触发识别通过率阈值（Step 0 验收 ③ 要求 ≥ 80%）
_RECALL_PASS_RATE: float = 0.80

# SRS 四 tool 名集合
_SRS_TOOL_NAMES = frozenset(("add_to_srs", "query_srs_due", "review_srs_card", "query_srs_stats"))


# 评估用 system prompt：模拟 srs-review skill 已激活 + quiz-maker / study-planner 也常驻
# （生产 Agent 按 catalog 自动激活；评估为可重复用最小 prompt 模拟）
_EVAL_SYSTEM_PROMPT = """你是用户的个人学习助手，可调用五类工具：
- 基础检索：search_knowledge / web_search / fetch_url
- 通用 plan-execute：make_plan / update_step / abort_plan
- 学习计划业务：create_study_plan / update_study_progress / query_study_status
- Quiz 业务：create_quiz / grade_quiz / query_quiz_history
- SRS 主动复习业务：add_to_srs / query_srs_due / review_srs_card / query_srs_stats

## 何时查 SRS 待复习卡

当用户表达**今天复习 / 出 due 卡 / 把 SRS 卡片背一下 / 抽卡 / 间隔重复回炉**等意图，
应当**第一轮就调 `query_srs_due`** 拉今天 due 列表（按 next_review_at <= now 排序）。
不要先 search_knowledge 或随意写答案 — SRS 队列是跨 session 持久化的，必须从 store 拉。

## 何时把卡加入 SRS

两条路径（看 source_type）：

1. **quiz_question** — 用户做完 quiz 后说"把错题加 SRS / 复习这些"：
   调 `add_to_srs(source_type="quiz_question", question_ids=[<错题 id 列表>])`
   错题 question_id 通常从 grade_quiz 返回或上下文取；用户给出明确 id 数组直接用。
2. **manual** — 用户手动给"正面 + 背面"："帮我加一张卡：正面 X 背面 Y"：
   调 `add_to_srs(source_type="manual", front=..., back=...)`

## 何时提交 review 评分

用户对一张 due 卡完成回忆 + 给出 4 档自评（again / hard / good / easy）时，
调 `review_srs_card(card_id, rating)` 更新该卡的 SM-2 调度状态。

4 档语义：`again` = 完全忘了（重置）/ `hard` = 想起来但费劲 / `good` = 正常 / `easy` = 太简单。

## 何时**不要**触发 SRS 工具

- 单一事实问答（"X 是什么 / 解释 Y / 对比 A vs B"）→ search_knowledge / web_search
- 闲聊 → 直接回答
- 学习计划新建需求（"我想 N 周准备 X"）→ create_study_plan / make_plan，**不是** SRS
- 新建 quiz（"考考我 / 出题"）→ create_quiz / make_plan，**不是** SRS
"""


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
    out: list[str] = []
    for tc in getattr(message, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        if fn is not None and getattr(fn, "name", None):
            out.append(fn.name)
    return out


# ── 判定：触发识别 ──────────────────────────────────────────────────────────


def _judge_recall(
    case: dict[str, Any], first_tool: tuple[str, dict[str, Any]] | None,
) -> tuple[bool, list[str]]:
    """根据 case 期望判断是否通过触发识别。"""
    category = case.get("category", "")
    first_name = first_tool[0] if first_tool else ""

    if category in ("due", "add", "review"):
        expected = case.get("expected_first_tool", [])
        if first_name in expected:
            return True, [f"识别 ✓ 第一轮调 `{first_name}`"]
        if not first_tool:
            return False, [f"识别 ✗ LLM 第一轮无 tool_call（应调 {expected}）"]
        return False, [f"识别 ✗ 第一轮调 `{first_name}`（期望 {expected}）"]

    if category == "negative":
        forbidden = case.get("expected_first_tool_not", list(_SRS_TOOL_NAMES))
        if first_name in forbidden:
            return False, [f"识别 ✗ 第一轮调 `{first_name}`（禁用 {forbidden}）"]
        return True, [
            f"识别 ✓ 第一轮"
            f"{'无 tool_call' if not first_tool else f'调 `{first_name}`'}（未触发 SRS 禁用）"
        ]

    return False, [f"未知 category: {category!r}"]


# ── 单 case 跑 ──────────────────────────────────────────────────────────────


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
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
            "reasons": [f"LLM 调用失败: {e}"], "error": str(e),
        }

    passed, reasons = _judge_recall(case, first)
    first_name = first[0] if first else ""
    first_args = first[1] if first else {}

    return {
        "id": case["id"],
        "pass": passed,
        "category": case.get("category", ""),
        "question": case["question"],
        "answer": answer,
        "first_tool": first_name,
        "first_tool_args": first_args,
        "all_tool_calls": all_tool_names,
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
    dataset_path: Path,
    env: dict[str, str],
) -> str:
    rate = passed / total if total else 0.0
    recall_verdict = (
        f"✅ 合格 (≥ {_RECALL_PASS_RATE:.0%})" if rate >= _RECALL_PASS_RATE
        else f"⚠️ 未达 {_RECALL_PASS_RATE:.0%} 判据"
    )
    lines: list[str] = [
        "# SRS 业务 触发识别评估报告",
        "",
        f"- **时间**: {env['timestamp']}",
        f"- **Git**: {env['git']}",
        f"- **Python**: {env['python']}",
        f"- **Provider**: {env['provider']}",
        f"- **Dataset**: `{dataset_path}`",
        "",
        "## 核心指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 样本数 | {total} |",
        f"| 识别通过数 | {passed} |",
        f"| 识别通过率 | {rate:.1%} |",
        f"| 识别判据 (≥ {_RECALL_PASS_RATE:.0%}) | {recall_verdict} |",
        "",
        "> SM-2 算法对齐由 UT 锁公式（`tests/test_srs_scheduler.py` 40 case），本评估不调 LLM-judge。",
        "",
    ]

    dues = [r for r in results if r.get("category") == "due"]
    adds = [r for r in results if r.get("category") == "add"]
    reviews = [r for r in results if r.get("category") == "review"]
    negs = [r for r in results if r.get("category") == "negative"]
    if dues or adds or reviews or negs:
        lines += ["## 分组指标", "", "| 组 | 通过 / 总数 |", "|---|---|"]
        if dues:
            lines.append(f"| due | {sum(1 for r in dues if r['pass'])} / {len(dues)} |")
        if adds:
            lines.append(f"| add | {sum(1 for r in adds if r['pass'])} / {len(adds)} |")
        if reviews:
            lines.append(f"| review | {sum(1 for r in reviews if r['pass'])} / {len(reviews)} |")
        if negs:
            lines.append(f"| negative | {sum(1 for r in negs if r['pass'])} / {len(negs)} |")
        lines.append("")

    lines += [
        "## 全 case 总览",
        "",
        "| id | category | 第一轮 tool | pass |",
        "|---|---|---|:-:|",
    ]
    for r in results:
        flag = "✅" if r["pass"] else "❌"
        first = f"`{r['first_tool']}`" if r["first_tool"] else "—"
        lines.append(f"| `{r['id']}` | {r.get('category', '?')} | {first} | {flag} |")
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
    dataset_path: Path,
    env: dict[str, str],
) -> Path:
    reports_dir = Path(__file__).parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"srs-eval-{ts}.md"
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
            "  python -m tools.agent_eval.srs.eval_srs                  # 跑全部\n"
            "  python -m tools.agent_eval.srs.eval_srs --case S01-...   # 单 case\n"
            "  python -m tools.agent_eval.srs.eval_srs --no-report      # 不落盘\n"
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

    print(f"\n🧪 SRS 业务评估（{len(dataset)} case）\n")

    results: list[dict[str, Any]] = []
    for i, case in enumerate(dataset, 1):
        print(f"  [{i:>2}/{len(dataset)}] {case['id']} ... ", end="", flush=True)
        r = _run_case(case)
        results.append(r)
        flag = "✅" if r["pass"] else "❌"
        print(flag)
        for line in r["reasons"]:
            print(f"        · {line}")

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    rate = passed / total if total else 0.0

    print(
        f"\n📊 识别通过 {passed}/{total} ({rate:.0%})  "
        f"{'✅ 合格' if rate >= _RECALL_PASS_RATE else '⚠️  未达判据'}"
    )
    print("")

    if not args.no_report:
        env = _collect_env()
        report = _dump_report(results, passed, total, args.dataset, env)
        print(f"📁 报告已存储：{report}\n")

    sys.exit(0 if rate >= _RECALL_PASS_RATE else 1)


if __name__ == "__main__":
    main()
