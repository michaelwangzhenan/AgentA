"""
Phase 1 性能基准（session + memory 合二为一）

要回答的问题：随着数据量增长（10/100/1000/5000…），
    - /sessions 列出/搜索是否卡？
    - /memory 列出/查询是否卡？

使用：
    python -m tools.agent_eval.perf_eval -h
    python -m tools.agent_eval.perf_eval                              # 默认 target=session
    python -m tools.agent_eval.perf_eval --target session             # 仅 session
    python -m tools.agent_eval.perf_eval --target memory              # 仅 memory
    python -m tools.agent_eval.perf_eval --target all                 # 全部
    python -m tools.agent_eval.perf_eval --sizes 100,1000,5000        # 指定数据档位

输出说明（每个数字 = 5 次中位数，单位 ms）：

  [target=session] /sessions 命令侧
    size           本次跑的 session 数
    no-filter      list_sessions() 无 query 全量返回             —— 查询类
    id-prefix      list_sessions(query='sess-00001')              —— 查询类
    keyword        list_sessions(query='ReAct')                   —— 查询类（搜索）
    limit=20       list_sessions(limit=20)                        —— 查询类
    render-full    cli.handlers.list_sessions() 全量打印          —— 渲染类
    render-filt    cli.handlers.list_sessions(query='ReAct')      —— 渲染类

  [target=memory] /memory 命令侧
    size           本次跑的记忆条数
    load_all       UserMemoryStore.load_all() 全量返回            —— 查询类
    load_ctx       UserMemoryStore.load_for_context(max_chars=1500) —— 注入路径
    upsert         单次 upsert（新 key）                          —— 写入类
    update_value   按 id 修改 value                                —— /memory edit 路径
    render-list    cli.handlers._print_memory_list 全量打印       —— 渲染类（分组 + source + 时间）

判据（用最大 size 行对照；不达标考虑加索引 / FTS5 / 分页）：

  session（默认 size=5000）
    - 查询类 4 列全部 < 50ms       —— SQL 单表直读
    - 渲染类 2 列全部 < 200ms      —— 字符串拼接 + 时间格式化
    - keyword / no-filter < 2x     —— LIKE 在 10K 量级无需 index

  memory（个人学习者实际量级 ≤ 100；测到 size=1000 验证留余量）
    - load_all < 20ms              —— 几十到几百条单表
    - load_ctx < 30ms              —— 含字符串拼接 + 截断
    - upsert/update < 10ms         —— 单行写
    - render-list < 100ms          —— 分组 + 多行输出

报告会同时落盘到 `tools/agent_eval/reports/perf-<target>-<timestamp>.md`，
含元信息（时间 / git / python / provider）+ 测量表 + 判据自动评估（PASS/FAIL），
便于人工浏览和阶段间对比。
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import tempfile
import timeit
from datetime import datetime
from pathlib import Path

# 评估工具统一规约：entry point 必须先 load_dotenv() 再 import src.*，
# 否则 src.config 在 import 时拿到的 *_API_KEY 都是空串，未来一旦加 LLM 调用
# （如 LLM-as-Judge）就会 401。本脚本目前只测 SQLite 不踩坑，但保持一致。
from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

import src.config as config  # noqa: E402 — 必须在 load_dotenv 之后
from src.cli import handlers  # noqa: E402
from src.memory.chat_history import ChatHistoryStore  # noqa: E402
from src.memory.user_memory import UserMemoryStore  # noqa: E402


# ── 通用计时工具 ────────────────────────────────────────────────────────────

def _time_ms(callable_, n_runs: int = 5) -> float:
    """跑 n_runs 次取中位数（毫秒）。屏蔽 cold-start 抖动。"""
    samples = timeit.repeat(callable_, number=1, repeat=n_runs)
    samples.sort()
    return samples[len(samples) // 2] * 1000.0


def _reports_dir() -> Path:
    """tools/agent_eval/reports/，不存在则创建。"""
    p = Path(__file__).parent / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _collect_env() -> dict[str, str]:
    """收集报告头部元信息：时间 / git short sha (+ dirty mark) / python / provider。"""
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
    except Exception:  # noqa: BLE001 — git 不在 / 超时都不阻塞评估
        pass
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git": git_part,
        "python": platform.python_version(),
        "provider": getattr(config, "ACTIVE_MODEL", "?"),
    }


def _render_session_md(rows: list[dict], env: dict[str, str]) -> str:
    """session target 的 Markdown 报告：表 + 判据自动评估。"""
    lines: list[str] = []
    lines.append("# Perf 评估报告 — session")
    lines.append("")
    lines.append(f"- **时间**: {env['timestamp']}")
    lines.append(f"- **Git**: {env['git']}")
    lines.append(f"- **Python**: {env['python']}")
    lines.append(f"- **Provider**: {env['provider']}")
    lines.append("")

    lines.append("## /sessions 性能基准")
    lines.append("")
    lines.append("每个数字 = 5 次中位数（单位 ms）。")
    lines.append("")
    lines.append("| size | no-filter | id-prefix | keyword | limit=20 | render-full | render-filt |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| {r['size']} | {r['query_none_ms']:.2f} | {r['query_id_prefix_ms']:.2f} | "
            f"{r['query_keyword_ms']:.2f} | {r['query_with_limit_ms']:.2f} | "
            f"{r['render_full_ms']:.2f} | {r['render_filtered_ms']:.2f} |"
        )
    lines.append("")

    lines.append("## 判据评估")
    lines.append("")
    if not rows:
        lines.append("_无数据。_")
        return "\n".join(lines)
    last = rows[-1]
    queries = [
        last["query_none_ms"], last["query_id_prefix_ms"],
        last["query_keyword_ms"], last["query_with_limit_ms"],
    ]
    renders = [last["render_full_ms"], last["render_filtered_ms"]]
    ratio = (last["query_keyword_ms"] / last["query_none_ms"]) if last["query_none_ms"] else 0.0
    checks = [
        ("查询类 4 列 < 50 ms",     max(queries) < 50,   f"实测最大 {max(queries):.2f} ms"),
        ("渲染类 2 列 < 200 ms",    max(renders) < 200,  f"实测最大 {max(renders):.2f} ms"),
        ("keyword / no-filter < 2x", ratio < 2.0,        f"实测 {ratio:.2f}x"),
    ]
    lines.append(f"以最大 size={last['size']} 行对照（不达标考虑加索引 / FTS5）：")
    lines.append("")
    lines.append("| 判据 | 结果 | 说明 |")
    lines.append("|---|:-:|---|")
    for name, ok, note in checks:
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'} | {note} |")
    lines.append("")
    return "\n".join(lines)


def _render_memory_md(rows: list[dict], env: dict[str, str]) -> str:
    """memory target 的 Markdown 报告：表 + 判据自动评估。"""
    lines: list[str] = []
    lines.append("# Perf 评估报告 — memory")
    lines.append("")
    lines.append(f"- **时间**: {env['timestamp']}")
    lines.append(f"- **Git**: {env['git']}")
    lines.append(f"- **Python**: {env['python']}")
    lines.append(f"- **Provider**: {env['provider']}")
    lines.append("")

    lines.append("## /memory 性能基准")
    lines.append("")
    lines.append("每个数字 = 5 次中位数（单位 ms）。")
    lines.append("")
    lines.append("| size | load_all | load_ctx | upsert | update_value | render-list |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| {r['size']} | {r['load_all_ms']:.2f} | {r['load_ctx_ms']:.2f} | "
            f"{r['upsert_ms']:.2f} | {r['update_value_ms']:.2f} | {r['render_list_ms']:.2f} |"
        )
    lines.append("")

    lines.append("## 判据评估")
    lines.append("")
    if not rows:
        lines.append("_无数据。_")
        return "\n".join(lines)
    last = rows[-1]
    checks = [
        ("load_all < 20 ms",        last["load_all_ms"] < 20,     f"实测 {last['load_all_ms']:.2f} ms"),
        ("load_ctx < 30 ms",        last["load_ctx_ms"] < 30,     f"实测 {last['load_ctx_ms']:.2f} ms"),
        ("upsert < 10 ms",          last["upsert_ms"] < 10,       f"实测 {last['upsert_ms']:.2f} ms"),
        ("update_value < 10 ms",    last["update_value_ms"] < 10, f"实测 {last['update_value_ms']:.2f} ms"),
        ("render-list < 100 ms",    last["render_list_ms"] < 100, f"实测 {last['render_list_ms']:.2f} ms"),
    ]
    lines.append(f"以最大 size={last['size']} 行对照（实际单用户场景 ≤ 100 条，留余量）：")
    lines.append("")
    lines.append("| 判据 | 结果 | 说明 |")
    lines.append("|---|:-:|---|")
    for name, ok, note in checks:
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'} | {note} |")
    lines.append("")
    return "\n".join(lines)


def _dump_report(target: str, rows: list[dict], env: dict[str, str]) -> Path:
    """落盘 perf-<target>-<ts>.md，返回路径。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = _reports_dir() / f"perf-{target}-{ts}.md"
    md = _render_session_md(rows, env) if target == "session" else _render_memory_md(rows, env)
    out.write_text(md, encoding="utf-8")
    return out


# ── target: session ────────────────────────────────────────────────────────

def _seed_sessions(store: ChatHistoryStore, n: int) -> None:
    """生成 n 个 session，首问内容含 ReAct/RAG/Memory 关键词便于 keyword 过滤测试。"""
    for i in range(n):
        sid = f"sess-{i:06d}-{'foo' if i % 3 == 0 else 'bar'}"
        keyword = "ReAct" if i % 5 == 0 else "RAG" if i % 5 == 1 else "Memory"
        store.append(sid, {"role": "user", "content": f"how does {keyword} work in topic-{i}"})


def _bench_session_size(size: int) -> dict[str, float]:
    # try/finally 保证 store.close() 一定执行；否则 KeyboardInterrupt 时 SQLite
    # 句柄仍持有 perf.db，TemporaryDirectory.__exit__ 走 rmtree 会在 Windows 上
    # 抛 WinError 32（文件被占用），把真异常埋掉。
    with tempfile.TemporaryDirectory() as td:
        store = ChatHistoryStore(db_path=str(Path(td) / "perf.db"))
        try:
            _seed_sessions(store, size)

            row: dict[str, float] = {"size": size}
            row["query_none_ms"] = _time_ms(lambda: store.list_sessions())
            row["query_id_prefix_ms"] = _time_ms(lambda: store.list_sessions(query="sess-00001"))
            row["query_keyword_ms"] = _time_ms(lambda: store.list_sessions(query="ReAct"))
            row["query_with_limit_ms"] = _time_ms(lambda: store.list_sessions(limit=20))

            sink: list[str] = []
            row["render_full_ms"] = _time_ms(
                lambda: handlers.list_sessions(store, current_session_id="sess-000010-foo", out=sink.append)
            )
            sink.clear()
            row["render_filtered_ms"] = _time_ms(
                lambda: handlers.list_sessions(store, query="ReAct", out=sink.append)
            )
            return row
        finally:
            store.close()


def _print_session_table(rows: list[dict]) -> None:
    print("\n[session] /sessions 性能基准（每个测量 5 次中位数）\n")
    header = (
        f"{'size':>6}  {'no-filter':>11}  {'id-prefix':>11}  {'keyword':>11}  "
        f"{'limit=20':>11}  {'render-full':>13}  {'render-filt':>13}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['size']:>6}  "
            f"{r['query_none_ms']:>9.2f}ms  "
            f"{r['query_id_prefix_ms']:>9.2f}ms  "
            f"{r['query_keyword_ms']:>9.2f}ms  "
            f"{r['query_with_limit_ms']:>9.2f}ms  "
            f"{r['render_full_ms']:>11.2f}ms  "
            f"{r['render_filtered_ms']:>11.2f}ms"
        )
    print(
        "\n判据（以最大 size 行对照）：\n"
        "  - 查询类 4 列 < 50ms\n"
        "  - 渲染类 2 列 < 200ms\n"
        "  - keyword / no-filter < 2x\n"
    )


# ── target: memory ─────────────────────────────────────────────────────────

# 5 个固定 category，确保种子数据均匀
_MEM_CATEGORIES = ["preference", "background", "instruction", "task", "correction"]


def _seed_memories(store: UserMemoryStore, n: int) -> None:
    """生成 n 条记忆，cat 轮转，key 唯一（保证 UNIQUE 不冲突）。"""
    for i in range(n):
        cat = _MEM_CATEGORIES[i % len(_MEM_CATEGORIES)]
        store.upsert(
            cat,
            f"key_{i:06d}",
            f"value for entry {i}, used to test render and load_for_context",
            source="auto" if i % 3 else "manual",
        )


def _bench_memory_size(size: int) -> dict[str, float]:
    # try/finally 同 _bench_session_size：保证中断时 db 句柄释放，避免
    # TemporaryDirectory cleanup 在 Windows 上抛 WinError 32 掩盖真异常。
    with tempfile.TemporaryDirectory() as td:
        store = UserMemoryStore(str(Path(td) / "perf_mem.db"))
        try:
            _seed_memories(store, size)

            row: dict[str, float] = {"size": size}
            row["load_all_ms"] = _time_ms(lambda: store.load_all())
            row["load_ctx_ms"] = _time_ms(lambda: store.load_for_context(max_chars=1500))

            # upsert / update 用专门的 size+1 / 选第一个 id 避免污染统计
            next_idx = size
            row["upsert_ms"] = _time_ms(
                lambda i=[next_idx]: (
                    store.upsert(
                        "preference",
                        f"key_{i[0]:06d}",
                        "perf write",
                        source="manual",
                    ),
                    i.__setitem__(0, i[0] + 1),
                )[0]
            )
            first_id = store.load_all()[0]["id"]
            row["update_value_ms"] = _time_ms(
                lambda c=[0]: (
                    store.update_value(first_id, f"perf updated {c[0]}"),
                    c.__setitem__(0, c[0] + 1),
                )[0]
            )

            sink: list[str] = []
            row["render_list_ms"] = _time_ms(
                lambda: handlers._print_memory_list(store, out=sink.append)
            )
            return row
        finally:
            store.close()


def _print_memory_table(rows: list[dict]) -> None:
    print("\n[memory] /memory 性能基准（每个测量 5 次中位数）\n")
    header = (
        f"{'size':>6}  {'load_all':>11}  {'load_ctx':>11}  {'upsert':>11}  "
        f"{'update_val':>11}  {'render-list':>13}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['size']:>6}  "
            f"{r['load_all_ms']:>9.2f}ms  "
            f"{r['load_ctx_ms']:>9.2f}ms  "
            f"{r['upsert_ms']:>9.2f}ms  "
            f"{r['update_value_ms']:>9.2f}ms  "
            f"{r['render_list_ms']:>11.2f}ms"
        )
    print(
        "\n判据（以最大 size 行对照；实际单用户场景 ≤ 100 条）：\n"
        "  - load_all < 20ms      —— SQL 全量直读\n"
        "  - load_ctx < 30ms      —— 含 _sanitize + 截断\n"
        "  - upsert/update < 10ms —— 单行写\n"
        "  - render-list < 100ms  —— 分组 + 多行打印\n"
    )


# ── 入口 ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "\n常用命令：\n"
            "  python -m tools.agent_eval.perf_eval                              # 默认 target=session\n"
            "  python -m tools.agent_eval.perf_eval --target memory              # 仅 memory\n"
            "  python -m tools.agent_eval.perf_eval --target all                 # 全部\n"
            "  python -m tools.agent_eval.perf_eval --sizes 100,1000,5000        # 自定义档位\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target",
        choices=["session", "memory", "all"],
        default="session",
        help="跑哪一组基准（默认: %(default)s）",
    )
    parser.add_argument(
        "--sizes",
        default="",
        help="逗号分隔档位；为空时按 target 各自取默认（session=10,100,1000；memory=10,100,1000）",
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="只在屏幕打印，不落盘 Markdown 报告"
    )
    args = parser.parse_args()

    default_sizes = {
        "session": [10, 100, 1000],
        "memory":  [10, 100, 1000],
    }

    def _parse_sizes(default: list[int]) -> list[int]:
        if not args.sizes:
            return default
        return [int(s) for s in args.sizes.split(",") if s.strip()]

    env = _collect_env()
    targets = ["session", "memory"] if args.target == "all" else [args.target]
    for t in targets:
        sizes = _parse_sizes(default_sizes[t])
        rows: list[dict] = []
        if t == "session":
            for n in sizes:
                rows.append(_bench_session_size(n))
            _print_session_table(rows)
        else:
            for n in sizes:
                rows.append(_bench_memory_size(n))
            _print_memory_table(rows)
        if not args.no_report:
            path = _dump_report(t, rows, env)
            print(f"报告已保存：{path}")


if __name__ == "__main__":
    main()
