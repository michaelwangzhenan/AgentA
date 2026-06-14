"""
Memory Recall Golden 评估 (Phase 1.2 / 1.3 / 1.4 共用)

要回答的问题：写入 UserMemoryStore 的记忆 / 项目 rules / RAG 引用展示，
能否被 system_prompt 正确注入并被 LLM 回答所遵循？

设计：
    1) 每个 case 描述若干条"已有记忆 + 一个新问题"
    2) 把记忆灌进 UserMemoryStore → 用 MemoryManager.build_system_prompt 拼出
       含 <user_context> 块的真实 system prompt
    3) 调真实 LLM（src.llm.provider.chat），拿到 answer
    4) 用 must_contain_any (OR) + must_not_contain (NOT) 关键词检查 answer 是否
       遵循了记忆里的偏好/指令

Phase 1.3 扩展：case 可加 `rules: str` 字段模拟项目根 `.agenta/rules.md`，
通过 `build_rules_block` 拼入 `<user_rules>` 块；R0x 系列 case 验证。

Phase 1.4 扩展：case 可加 `mock_hits: [...]` 字段模拟 `search_knowledge` 工具
返回，走 `CitationBuilder` 编号 → 格式化为带 [n] 的 RAG 上下文 → 让 LLM 写
[n] 引用 → 复现 `Agent.run()` 末尾的 sources 块拼接；配合 `expect_citation_block`
布尔字段校验最终回答是否带 `— sources —` 块；C0x 系列 case 验证。

判据：通过率 ≥ 80%（dataset.json 当前 13 case → ≥ 11 通过算合格）

使用：
    python -m tools.agent_eval.memory.recall_golden -h
    python -m tools.agent_eval.memory.recall_golden                              # 跑全部
    python -m tools.agent_eval.memory.recall_golden --case M01-lang-zh           # 只跑指定 id
    python -m tools.agent_eval.memory.recall_golden --dataset other.json         # 自定义 golden

报告落盘到 `tools/reports/memory/recall-<timestamp>.md`，含元信息（时间 /
git / python / provider）+ 核心指标 + 全 case 总览 + Fail 用例详情
（question / system_prompt / answer 截断 + 触发的 must/not 规则），便于事后诊断。

为什么不用 LLM-judge？
    本期 7 case 规模小，关键词/regex 就能把 80% 准召出来；LLM-judge 留 Phase 2
    Plan/Quiz feature 第二次复用时再上 framework（详 iter_2_agent.md §4.9.2 显式不做项）。
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# 必须在 import src.config 之前加载 .env：config.py 在模块导入时即用 os.getenv
# 读取所有配置（含 *_API_KEY），而本脚本不像 main.py / ingest.py 那样自带
# load_dotenv()，单独 `python -m tools.agent_eval.memory.recall_golden` 启动时
# 若不显式加载，会拿到空 key，导致 chat() 全部 401（Incorrect API key provided）。
from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

import src.config as config  # noqa: E402 — 必须在 load_dotenv 之后
from src.agent.core.citation_builder import CitationBuilder  # noqa: E402
from src.agent.core.memory_manager import MemoryManager  # noqa: E402
from src.agent.core.rules_loader import build_rules_block  # noqa: E402
from src.llm.provider import chat  # noqa: E402
from src.memory.user_memory import UserMemoryStore  # noqa: E402
from src.rag.retriever import Hit, format_search_results  # noqa: E402


_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        sys.exit(f"❌ 找不到 dataset：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_system_prompt(
    memories: list[str],
    rules: str | None = None,
) -> str:
    """构造一次性 system prompt，复现 `Agent.run()` 的三层拼接：

        base → <user_rules>（可选）→ <user_context>

    用真实的 `UserMemoryStore` + `MemoryManager` + `build_rules_block`，
    不走 mock，确保评估的是端到端行为（含 `_sanitize`、防注入 guard）。

    Args:
        memories: case 里写的 user_memory 条目（每条一句自然语言）。
        rules: case 可选 `rules` 字段；`None` / 空串 → 不注入 `<user_rules>` 块。
    """
    base_prompt = "你是一个有用的 AI 助手。"
    with tempfile.TemporaryDirectory() as td:
        store = UserMemoryStore(str(Path(td) / "eval.db"))
        try:
            for text in memories:
                store.add(text, source="manual")
            mgr = MemoryManager(
                user_memory=store,
                chat_history=MagicMock(),  # 不会用到
                session_id="eval-session",
                llm_chat=MagicMock(),
            )
            base_with_rules = base_prompt + build_rules_block(rules)
            return mgr.build_system_prompt(base_with_rules)
        finally:
            store.close()


def _check_expectations(answer: str, expected: dict[str, list[str]]) -> tuple[bool, list[str]]:
    """OR for must_contain_any, AND-NOT for must_not_contain. 返回 (pass, reasons)."""
    reasons: list[str] = []
    must_any: list[str] = expected.get("must_contain_any", []) or []
    must_not: list[str] = expected.get("must_not_contain", []) or []

    ok_any = True
    if must_any:
        hits = [kw for kw in must_any if kw in answer]
        ok_any = len(hits) > 0
        reasons.append(
            f"must_contain_any: 命中 {hits!r}" if ok_any
            else f"must_contain_any: ❌ 全部未命中 {must_any!r}"
        )

    ok_not = True
    if must_not:
        violated = [kw for kw in must_not if kw in answer]
        ok_not = len(violated) == 0
        reasons.append(
            "must_not_contain: ✓" if ok_not
            else f"must_not_contain: ❌ 出现禁词 {violated!r}"
        )

    return (ok_any and ok_not), reasons


def _build_mock_hits(specs: list[dict[str, Any]]) -> list[Hit]:
    """把 dataset 里 `mock_hits` 子段转成真实 `Hit` 对象，模拟 RAG 召回。

    Phase 1.4：用于评估"LLM 看到编号化 RAG 结果 → 写 [n] → 程序拼 sources"
    端到端链路，**不依赖真实知识库**（无需 ingest、跨环境稳定）。
    """
    return [
        Hit(
            source=s["source"],
            document=s.get("document", ""),
            distance=s.get("distance", 0.1),
            collection=s.get("collection", "kb_mock"),
            metadata={
                "heading_path": s.get("heading_path"),
                "page_no": s.get("page_no"),
            },
        )
        for s in specs
    ]


def _check_citation_block(answer: str, expect: bool) -> tuple[bool, str]:
    """校验最终回答里 `— sources —` 块的出现 / 缺席是否符合 `expect`。"""
    has_block = "— sources —" in answer
    if expect and not has_block:
        return False, "expect_citation_block: ❌ 期望含 `— sources —` 块但未出现"
    if (not expect) and has_block:
        return False, "expect_citation_block: ❌ 期望无 `— sources —` 块但出现了"
    return True, f"expect_citation_block: ✓ ({'has' if has_block else 'no'})"


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    """跑单 case，返回结果 dict。

    支持三种 case 形态：
    - 仅 `memories` / `rules`（Phase 1.2/1.3 风格）：纯 system prompt 注入评估；
    - 加 `mock_hits` + `expect_citation_block`（Phase 1.4）：把 mock hits 走
      `CitationBuilder` + `format_search_results` 拼到 user message 里模拟
      `search_knowledge` 工具返回，LLM 回答后再走 `extract_used` + `render`
      拼接 sources 块，最终回答（含或不含 sources）参与关键词与块校验。
    """
    # Phase 1.3：可选 rules 字段，用于 R0x rules-driven case
    system_prompt = _build_system_prompt(case["memories"], rules=case.get("rules"))

    # Phase 1.4：mock_hits 存在时走 citation 评估路径
    builder: CitationBuilder | None = None
    user_content = case["question"]
    if "mock_hits" in case and case["mock_hits"]:
        builder = CitationBuilder()
        hits = _build_mock_hits(case["mock_hits"])
        nums = builder.register(hits)
        rag_block = format_search_results(hits, citation_nums=nums)
        user_content = (
            f"{case['question']}\n\n"
            f"[search_knowledge 返回结果]\n{rag_block}"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        resp = chat(messages, temperature=0.7)
        answer = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return {
            "id": case["id"],
            "pass": False,
            "error": str(e),
            "question": case["question"],
            "answer": "",
            "reasons": [f"LLM 调用失败: {e}"],
        }

    # Phase 1.4：复现 `Agent.run()` 末尾的 sources 拼接
    final_answer = answer
    if builder is not None:
        used = builder.extract_used(final_answer)
        final_answer = final_answer + builder.render(used)

    passed, reasons = _check_expectations(final_answer, case.get("expected", {}))

    # Phase 1.4：可选 expect_citation_block 校验（True / False / 不写 = 不校验）
    expect_cb = case.get("expect_citation_block")
    if expect_cb is not None:
        cb_ok, cb_reason = _check_citation_block(final_answer, bool(expect_cb))
        passed = passed and cb_ok
        reasons.append(cb_reason)

    return {
        "id": case["id"],
        "pass": passed,
        "question": case["question"],
        "system_prompt": system_prompt,
        "answer": final_answer,
        "reasons": reasons,
        "note": case.get("note", ""),
    }


def _collect_env() -> dict[str, str]:
    """报告头部元信息：时间 / git short sha (+ dirty mark) / python / provider。"""
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
    pass_threshold: float = 0.80,
) -> str:
    """渲染：标题 + 元信息 + 核心指标 + 全 case 总览 + Fail 详情。

    设计同 [rag_eval/runner.py](../../rag_eval/runner.py)：失败用例放最后、含
    question / system_prompt / answer 截断 + 触发的 reasons，便于事后回归定位。
    """
    rate = passed / total if total else 0.0
    verdict = f"✅ 合格 (≥ {pass_threshold:.0%})" if rate >= pass_threshold else f"⚠️ 未达 {pass_threshold:.0%} 判据"

    lines: list[str] = []
    lines.append("# Memory Recall Golden 评估报告")
    lines.append("")
    lines.append(f"- **时间**: {env['timestamp']}")
    lines.append(f"- **Git**: {env['git']}")
    lines.append(f"- **Python**: {env['python']}")
    lines.append(f"- **Provider**: {env['provider']}")
    lines.append(f"- **Dataset**: `{dataset_path}`")
    lines.append(f"- **阈值**: 通过率 ≥ {pass_threshold:.0%}")
    lines.append("")

    lines.append("## 核心指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 样本数 | {total} |")
    lines.append(f"| 通过数 | {passed} |")
    lines.append(f"| 通过率 | {rate:.1%} |")
    lines.append(f"| 判据 (≥ {pass_threshold:.0%}) | {verdict} |")
    lines.append("")

    lines.append("## 全 case 总览")
    lines.append("")
    lines.append("| id | pass | reasons |")
    lines.append("|---|:-:|---|")
    for r in results:
        reasons = "<br>".join(r.get("reasons") or []) or "—"
        flag = "✅" if r["pass"] else "❌"
        lines.append(f"| `{r['id']}` | {flag} | {reasons} |")
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
            if r.get("system_prompt"):
                lines.append("")
                lines.append("<details><summary>注入的 system_prompt</summary>")
                lines.append("")
                lines.append("```")
                lines.append(_truncate(r["system_prompt"], 800))
                lines.append("```")
                lines.append("")
                lines.append("</details>")
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


def _build_summary(
    passed: int, total: int, env: dict[str, str], pass_threshold: float
) -> dict[str, Any]:
    """卡片用结构化 summary（与 .md 配对落 .json）。"""
    rate = passed / total if total else 0.0
    return {
        "timestamp": env.get("timestamp", ""),
        "git": env.get("git", ""),
        "total": total,
        "passed": passed,
        "rate": rate,
        "pass_threshold": pass_threshold,
        "ok": rate >= pass_threshold,
    }


def _dump_report(
    results: list[dict[str, Any]],
    passed: int,
    total: int,
    dataset_path: Path,
    env: dict[str, str],
    pass_threshold: float = 0.80,
) -> Path:
    """落盘 recall-<ts>.md + 配对 .json，返回 md 路径。"""
    import json

    from tools.eval_common.report_paths import reports_dir as eval_reports_dir
    reports_dir = eval_reports_dir("memory")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"recall-{ts}.md"
    out.write_text(
        _render_markdown(results, passed, total, dataset_path, env, pass_threshold),
        encoding="utf-8",
    )
    out.with_suffix(".json").write_text(
        json.dumps(_build_summary(passed, total, env, pass_threshold), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "\n常用命令：\n"
            "  python -m tools.agent_eval.memory.recall_golden                            # 跑全部 case\n"
            "  python -m tools.agent_eval.memory.recall_golden --case M01-lang-zh         # 单 case\n"
            "  python -m tools.agent_eval.memory.recall_golden --dataset path/to.json     # 自定义 golden\n"
            "  python -m tools.agent_eval.memory.recall_golden --no-report                # 只屏幕，不保存\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset", type=Path, default=_DEFAULT_DATASET,
        help=f"golden 数据集 JSON 路径（默认: {_DEFAULT_DATASET}）",
    )
    parser.add_argument(
        "--case", type=str, default="", help="只跑指定 id（精确匹配）",
    )
    parser.add_argument(
        "--no-report", action="store_true", help="只在屏幕打印，不落盘 Markdown 报告",
    )
    parser.add_argument(
        "--pass-threshold", dest="pass_threshold", type=float, default=0.80,
        help="通过率达标阈值（≥），默认 0.80",
    )
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    if args.case:
        dataset = [c for c in dataset if c["id"] == args.case]
        if not dataset:
            sys.exit(f"❌ 没有 id={args.case} 的 case")

    print(f"\n🧪 Memory Recall Golden 评估（{len(dataset)} case）\n")

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
    pt = args.pass_threshold
    print(
        f"\n📊 通过 {passed}/{total} ({rate:.0%})  "
        f"{f'✅ 合格 (≥{pt:.0%})' if rate >= pt else f'⚠️  未达 {pt:.0%} 判据'}\n"
    )

    if not args.no_report:
        env = _collect_env()
        report = _dump_report(results, passed, total, args.dataset, env, pt)
        print(f"📁 报告已落盘：{report}\n")

    sys.exit(0 if rate >= pt else 1)


if __name__ == "__main__":
    main()
