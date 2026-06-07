"""
Skill Recall Golden 评估 (Phase 1.5)

要回答的问题：把 `.agenta/skills/` 真实 skill 的 frontmatter 通过 catalog 注入到
system prompt 后，LLM 在看到不同用户问题时，是否能**主动**调对 `load_skill(name=…)`
（positive 用例 = 调对预期 skill；negative 用例 = 不调任何 load_skill）。

对应 Step 0 验收：
    ② 主动认出：LLM 不需手动 /cmd 就能从 catalog 描述里识别该用哪个 skill。

设计：
    1) 真实扫 `.agenta/skills/`（不 mock），跟 main.py 启动逻辑保持一致；
    2) 对每个 case 拼一份"base + catalog"的 system prompt + 用户问题；
    3) 调一次真实 LLM（`src.llm.provider.chat`）并传 `tools=get_tools(skill_bodies)`
       让 LLM 走 function-calling 决定要不要调 load_skill；
    4) 解析 `response.choices[0].message.tool_calls`，按 case.category 判 pass：
         - positive  → tool_calls 中含 load_skill(name=expected_skill) → pass
         - negative  → tool_calls 中**不含**任何 load_skill 调用 → pass
       （LLM 可能转去调 search_knowledge / web_search，本评估只关心 load_skill）

判据：通过率 ≥ 80%（与 Phase 1.2 / 1.4 recall_golden 一致）。

为什么不直接跑完整 Agent.run()：
    本评估只关心"主动认出"这一步，run() 会把整个多轮工具循环 + 后续推理跑完，
    噪音多、耗时长、还会触发 RAG 真实检索；单步 chat() 已足够覆盖验收 ②。

使用：
    python -m tools.agent_eval.skills.recall_skill                              # 跑全部
    python -m tools.agent_eval.skills.recall_skill --case S01-positive-planner  # 单 case
    python -m tools.agent_eval.skills.recall_skill --dataset other.json         # 自定义 golden
    python -m tools.agent_eval.skills.recall_skill --no-report                  # 不落盘
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

# 必须在 import src.config 之前加载 .env（同 recall_golden.py 的理由）
from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

import src.config as config  # noqa: E402 — 必须在 load_dotenv 之后
from src.agent.tools import get_tools  # noqa: E402
from src.skills.skill_loader import build_skill_catalog, scan_skills  # noqa: E402
from src.llm.provider import chat  # noqa: E402


_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"
_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agenta" / "skills"

# 评估用的简短 base prompt：明确说明 catalog 用法，让 LLM 真正会去调 load_skill。
# 不直接复用 src.agent.agent.SYSTEM_PROMPT 是为了让 eval 独立可控，避免 SYSTEM_PROMPT
# 后续被改导致 eval 行为漂移。
_BASE_PROMPT = (
    "你是一个善于使用工具的 AI 助手。当用户问题与下方列出的某个 Skill 描述匹配时，"
    "**优先调用 `load_skill` 工具加载完整指令**，再按 skill 指令完成任务。"
    "若问题与所有 Skill 都不匹配（比如纯闲聊、纯事实查询），就直接回答，不要调 load_skill。"
)


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        sys.exit(f"❌ 找不到 dataset：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_system_prompt(skill_bodies_keys: dict[str, str], catalog_text: str) -> str:
    """base + catalog，跟 Agent.__init__ 注入 skills 的拼装方式保持一致。

    `skill_bodies_keys` 只是用 dict.keys() 作占位（避免把 body 全量打到 system prompt
    里，eval 这一步只测"主动认出"，不测"按指令执行"）。
    """
    return _BASE_PROMPT + catalog_text


def _extract_load_skill_calls(message: Any) -> list[str]:
    """从 chat completion message 中抽出所有 load_skill 调用的 name 参数。

    返回值是 name 列表，便于 positive 判 "name in calls" / negative 判 "calls == []"。
    """
    tool_calls = getattr(message, "tool_calls", None) or []
    names: list[str] = []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        if fn is None or getattr(fn, "name", None) != "load_skill":
            continue
        raw_args = getattr(fn, "arguments", "") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {}
        name = (args or {}).get("name", "")
        if name:
            names.append(str(name))
    return names


def _judge(case: dict[str, Any], load_skill_calls: list[str]) -> tuple[bool, list[str]]:
    """返回 (pass, reasons)。category 决定判据。"""
    category = case.get("category", "positive")
    expected = case.get("expected_skill")
    reasons: list[str] = []

    if category == "positive":
        if not expected:
            return False, ["positive case 缺 expected_skill 字段"]
        if expected in load_skill_calls:
            reasons.append(f"load_skill: ✓ 命中 {expected!r}")
            return True, reasons
        if load_skill_calls:
            reasons.append(
                f"load_skill: ❌ 期望 {expected!r}，实际调了 {load_skill_calls!r}"
            )
        else:
            reasons.append(f"load_skill: ❌ 期望 {expected!r}，但 LLM 没调 load_skill")
        return False, reasons

    if category == "negative":
        if load_skill_calls:
            reasons.append(f"load_skill: ❌ 期望不调，实际调了 {load_skill_calls!r}")
            return False, reasons
        reasons.append("load_skill: ✓ 未触发（符合预期）")
        return True, reasons

    return False, [f"未知 category: {category!r}"]


def _run_case(case: dict[str, Any], skill_bodies: dict[str, str], catalog: str) -> dict[str, Any]:
    system_prompt = _build_system_prompt(skill_bodies, catalog)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case["question"]},
    ]
    tools = get_tools(skill_bodies)

    try:
        # temperature 调低以减少 tool-call 随机性，让 eval 跨次跑更稳定
        resp = chat(messages, tools=tools, temperature=0.2)
        message = resp.choices[0].message
        load_skill_calls = _extract_load_skill_calls(message)
        answer = (getattr(message, "content", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        return {
            "id": case["id"],
            "pass": False,
            "category": case.get("category", "positive"),
            "question": case["question"],
            "answer": "",
            "load_skill_calls": [],
            "reasons": [f"LLM 调用失败: {e}"],
            "error": str(e),
            "note": case.get("note", ""),
        }

    passed, reasons = _judge(case, load_skill_calls)
    return {
        "id": case["id"],
        "pass": passed,
        "category": case.get("category", "positive"),
        "expected_skill": case.get("expected_skill"),
        "question": case["question"],
        "system_prompt": system_prompt,
        "answer": answer,
        "load_skill_calls": load_skill_calls,
        "reasons": reasons,
        "note": case.get("note", ""),
    }


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
    skills_loaded: list[str],
) -> str:
    rate = passed / total if total else 0.0
    verdict = "✅ 合格 (≥ 80%)" if rate >= 0.80 else "⚠️ 未达 80% 判据"

    lines: list[str] = []
    lines.append("# Skill Recall Golden 评估报告")
    lines.append("")
    lines.append(f"- **时间**: {env['timestamp']}")
    lines.append(f"- **Git**: {env['git']}")
    lines.append(f"- **Python**: {env['python']}")
    lines.append(f"- **Provider**: {env['provider']}")
    lines.append(f"- **Dataset**: `{dataset_path}`")
    lines.append(f"- **Loaded Skills**: {', '.join(skills_loaded) or '（无）'}")
    lines.append("")

    lines.append("## 核心指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 样本数 | {total} |")
    lines.append(f"| 通过数 | {passed} |")
    lines.append(f"| 通过率 | {rate:.1%} |")
    lines.append(f"| 判据 (≥ 80%) | {verdict} |")
    lines.append("")

    # category breakdown — 让人一眼看出 positive vs negative 的偏倚
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
    lines.append("| id | category | expected | 实际 load_skill | pass |")
    lines.append("|---|---|---|---|:-:|")
    for r in results:
        flag = "✅" if r["pass"] else "❌"
        expected = r.get("expected_skill") or "—"
        actual = ", ".join(r.get("load_skill_calls") or []) or "（无）"
        lines.append(
            f"| `{r['id']}` | {r.get('category', '?')} | {expected} | {actual} | {flag} |"
        )
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
    dataset_path: Path,
    env: dict[str, str],
    skills_loaded: list[str],
) -> Path:
    reports_dir = Path(__file__).parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"skill-recall-{ts}.md"
    out.write_text(
        _render_markdown(results, passed, total, dataset_path, env, skills_loaded),
        encoding="utf-8",
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "\n常用命令：\n"
            "  python -m tools.agent_eval.skills.recall_skill                              # 跑全部 case\n"
            "  python -m tools.agent_eval.skills.recall_skill --case S01-positive-planner  # 单 case\n"
            "  python -m tools.agent_eval.skills.recall_skill --dataset path/to.json       # 自定义 golden\n"
            "  python -m tools.agent_eval.skills.recall_skill --no-report                  # 不落盘\n"
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

    scan = scan_skills(_SKILLS_DIR)
    if scan.failed:
        # 与 main.py 启动行为对齐：失败也要让用户看到，避免静默
        print(f"⚠️ Skills 加载有 {len(scan.failed)} 个失败：")
        for f in scan.failed:
            print(f"  ✗ {f.path}：{f.reason}")
    if not scan.loaded:
        sys.exit(f"❌ {_SKILLS_DIR} 下未发现可用 skill，无法评估")

    skill_bodies = {name: info.body for name, info in scan.loaded.items()}
    catalog = build_skill_catalog(scan.loaded)
    skills_loaded = list(scan.loaded.keys())

    print(
        f"\n🧪 Skill Recall Golden 评估（{len(dataset)} case，"
        f"已加载 {len(skills_loaded)} 个 skill: {', '.join(skills_loaded)}）\n"
    )

    results: list[dict[str, Any]] = []
    for i, case in enumerate(dataset, 1):
        print(f"  [{i:>2}/{len(dataset)}] {case['id']} ... ", end="", flush=True)
        r = _run_case(case, skill_bodies, catalog)
        results.append(r)
        flag = "✅" if r["pass"] else "❌"
        print(flag)
        for line in r["reasons"]:
            print(f"        · {line}")

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    rate = passed / total if total else 0.0
    print(
        f"\n📊 通过 {passed}/{total} ({rate:.0%})  "
        f"{'✅ 合格 (≥80%)' if rate >= 0.80 else '⚠️  未达 80% 判据'}\n"
    )

    if not args.no_report:
        env = _collect_env()
        report = _dump_report(results, passed, total, args.dataset, env, skills_loaded)
        print(f"📁 报告已存储：{report}\n")

    sys.exit(0 if rate >= 0.80 else 1)


if __name__ == "__main__":
    main()
