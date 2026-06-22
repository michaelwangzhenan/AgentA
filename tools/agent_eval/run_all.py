"""离线评估统一入口：一条命令跑全部 eval，汇总成一份总报告。

各 feature 的评估脚本仍可单独跑（见各自模块）；本聚合器把它们逐个拉起（子进程，
彼此环境隔离、各自 load_dotenv），按**退出码**判 PASS / FAIL，最后落一份
``tools/reports/run_all/run-all-<ts>.md`` 总报告，并在有任一 FAIL 时整体非零退出
（CI 直接用退出码做门禁，无需 grep）。

用法：
    python -m tools.agent_eval.run_all              # 跑全部（含耗 token 的 LLM 评估）
    python -m tools.agent_eval.run_all --ci         # 只跑不耗 token 的子集（CI 门禁用）
    python -m tools.agent_eval.run_all --no-report  # 只打印，不落总报告

CI 门禁口径（D3）：``--ci`` 只跑不消耗 LLM token 的确定性项（如安全拦截 --no-llm）；
faithfulness / recall 等耗 token 的项不进 PR 门禁，本地 / 手动跑全量。
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

import src.config as config  # noqa: E402 — 必须在 load_dotenv 之后


@dataclass(frozen=True)
class EvalTask:
    """一个被聚合的评估子任务。"""
    name: str
    module: str
    args: list[str] = field(default_factory=list)
    ci_safe: bool = False        # True = 不耗 token、确定性，可进 CI 门禁
    note: str = ""


# 评估任务清单。ci_safe 项不调真实 LLM（不耗 token），可在 CI 跑；其余为耗 token 的
# LLM 评估，仅本地 / 手动全量跑。
TASKS: list[EvalTask] = [
    EvalTask("安全拦截", "tools.agent_eval.security.eval_security", ["--no-llm"],
             ci_safe=True, note="prompt injection / tool 名单门（regex + 名单门，不耗 token）"),
    EvalTask("RAG 检索", "tools.rag_eval.runner", [],
             ci_safe=False, note="recall@k / MRR（需已 ingest 知识库）"),
    EvalTask("记忆召回", "tools.agent_eval.memory.eval_memory", [],
             ci_safe=False, note="记忆 / rules / RAG 引用注入端到端（耗 token）"),
    EvalTask("Skill 路由", "tools.agent_eval.skills.eval_skills", [],
             ci_safe=False, note="SKILL.md 识别（耗 token）"),
    EvalTask("Plan", "tools.agent_eval.plan_execute.eval_plan_execute", [],
             ci_safe=False, note="make_plan 识别 + 结构 judge（耗 token）"),
    EvalTask("Quiz", "tools.agent_eval.quiz.eval_quiz", [],
             ci_safe=False, note="quiz 创建识别 + 质量 judge（耗 token）"),
    EvalTask("SRS", "tools.agent_eval.srs.eval_srs", [],
             ci_safe=False, note="SRS 触发识别（耗 token）"),
]


@dataclass
class TaskResult:
    name: str
    passed: bool
    returncode: int
    tail: str
    note: str


def _run_task(task: EvalTask) -> TaskResult:
    """子进程跑一个 eval；退出码 0 = PASS。捕获 stdout 末尾若干行进总报告。"""
    cmd = [sys.executable, "-m", task.module, *task.args]
    print(f"\n=== 跑 [{task.name}] {' '.join(cmd)} ===")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except Exception as e:  # noqa: BLE001 — 子进程拉起失败也算 FAIL，不中断其它任务
        return TaskResult(task.name, False, -1, f"子进程启动失败：{e}", task.note)
    out = (proc.stdout or "").strip().splitlines()
    tail = "\n".join(out[-8:]) if out else (proc.stderr or "").strip()[-500:]
    # 实时回显末尾，便于本地观察
    if tail:
        print(tail)
    return TaskResult(task.name, proc.returncode == 0, proc.returncode, tail, task.note)


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


def _reports_dir() -> Path:
    from tools.eval_common.report_paths import reports_dir
    return reports_dir("run_all")


def _render_md(results: list[TaskResult], env: dict[str, str], ci: bool) -> str:
    lines: list[str] = []
    lines.append("# 评估总报告（run_all）")
    lines.append("")
    lines.append(f"- **时间**: {env['timestamp']}")
    lines.append(f"- **Git**: {env['git']}")
    lines.append(f"- **Python**: {env['python']}")
    lines.append(f"- **Provider**: {env['provider']}")
    lines.append(f"- **模式**: {'CI（仅不耗 token 子集）' if ci else '全量'}")
    lines.append("")
    passed = sum(1 for r in results if r.passed)
    lines.append(f"## 汇总：{passed}/{len(results)} PASS")
    lines.append("")
    lines.append("| 任务 | 结果 | 退出码 | 说明 |")
    lines.append("|---|:-:|:-:|---|")
    for r in results:
        lines.append(
            f"| {r.name} | {'PASS' if r.passed else 'FAIL'} | {r.returncode} | {r.note} |"
        )
    lines.append("")
    # 各任务输出末尾，便于定位失败
    lines.append("## 各任务输出末尾")
    lines.append("")
    for r in results:
        lines.append(f"### {r.name} — {'PASS' if r.passed else 'FAIL'}")
        lines.append("")
        lines.append("```")
        lines.append(r.tail or "（无输出）")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    # 应用 UI 设置页持久化的 config override（.agenta/config_overrides.json），让 UI 改的
    # 评估相关配置在聚合入口及其报告里也生效（各子进程脚本各自再应用一次，互不影响）。
    try:
        from src.api.runtime import config_overrides
        config_overrides.apply_overrides()
    except Exception:  # noqa: BLE001 — 应用失败用默认配置继续
        pass

    ap = argparse.ArgumentParser(description="离线评估统一入口（聚合各 eval 出一份总报告）")
    ap.add_argument("--ci", action="store_true",
                    help="只跑不耗 token 的确定性子集（CI 门禁用）")
    ap.add_argument("--no-report", action="store_true", help="只打印，不落总报告")
    args = ap.parse_args(argv)

    tasks = [t for t in TASKS if t.ci_safe] if args.ci else TASKS
    if not tasks:
        print("没有可跑的任务")
        return 0

    results = [_run_task(t) for t in tasks]
    env = _collect_env()

    passed = sum(1 for r in results if r.passed)
    print("\n" + "=" * 56)
    print(f"评估总结：{passed}/{len(results)} PASS")
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name} (exit={r.returncode})")
    print("=" * 56)

    if not args.no_report:
        out = _reports_dir() / f"run-all-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        out.write_text(_render_md(results, env, args.ci), encoding="utf-8")
        print(f"总报告已保存：{out}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
