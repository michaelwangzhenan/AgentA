"""
Phase 1.1 Session —— /sessions 命令的性能基准测试（benchmark）

要回答的问题：随着 session 数量增长（10/100/1000/5000），用户敲 /sessions
是否会卡？「查询」(SQL) 和「渲染」(CLI 打印) 两段分别多慢？

使用：
    python -m tools.agent_eval.feature_session.session_perf_eval -h                          # 查看帮助
    python -m tools.agent_eval.feature_session.session_perf_eval                             # 默认跑 size=10,100,1000 三档
    python -m tools.agent_eval.feature_session.session_perf_eval --sizes 100,1000,5000       # 指定 size

输出列说明（每个数字是 5 次中位数，单位 ms）：
    size           本次跑的 session 数
    no-filter      list_sessions() 无 query 全量返回             —— 查询类
    id-prefix      list_sessions(query='sess-00001')              —— 查询类
    keyword        list_sessions(query='ReAct')                   —— 查询类（搜索）
    limit=20       list_sessions(limit=20)                        —— 查询类
    render-full    cli.handlers.list_sessions() 全量打印          —— 渲染类
    render-filt    cli.handlers.list_sessions(query='ReAct')      —— 渲染类

判据（用 size=5000 这一行对照；任一项不达标要考虑上索引 / FTS5）：
    - 查询类 4 列全部 < 50ms      —— SQL 单表直读，实测通常 < 15ms
    - 渲染类 2 列全部 < 200ms     —— 含字符串拼接 + 时间格式化
    - keyword / no-filter < 2x    —— LIKE 在 10K 量级无需 index

"""

from __future__ import annotations

import argparse
import tempfile
import timeit
from pathlib import Path

from src.cli import handlers
from src.memory.chat_history import ChatHistoryStore


def _seed(store: ChatHistoryStore, n: int) -> None:
    """生成 n 个 session，首问内容含可搜索关键词。"""
    for i in range(n):
        sid = f"sess-{i:06d}-{'foo' if i % 3 == 0 else 'bar'}"
        keyword = "ReAct" if i % 5 == 0 else "RAG" if i % 5 == 1 else "Memory"
        store.append(sid, {"role": "user", "content": f"how does {keyword} work in topic-{i}"})


def _time_ms(callable_, n_runs: int = 5) -> float:
    """跑 n_runs 次取中位数，返回毫秒。"""
    samples = timeit.repeat(callable_, number=1, repeat=n_runs)
    samples.sort()
    return samples[len(samples) // 2] * 1000.0


def _check_size(size: int) -> dict[str, float]:
    """对单一 size 跑全部测量，返回 metric dict。"""
    with tempfile.TemporaryDirectory() as td:
        store = ChatHistoryStore(db_path=str(Path(td) / "perf.db"))
        _seed(store, size)

        result: dict[str, float] = {"size": size}

        result["query_none_ms"] = _time_ms(lambda: store.list_sessions())
        result["query_id_prefix_ms"] = _time_ms(lambda: store.list_sessions(query="sess-00001"))
        result["query_keyword_ms"] = _time_ms(lambda: store.list_sessions(query="ReAct"))
        result["query_with_limit_ms"] = _time_ms(lambda: store.list_sessions(limit=20))

        sink: list[str] = []
        result["render_full_ms"] = _time_ms(
            lambda: handlers.list_sessions(store, current_session_id="sess-000010-foo", out=sink.append)
        )
        sink.clear()
        result["render_filtered_ms"] = _time_ms(
            lambda: handlers.list_sessions(store, query="ReAct", out=sink.append)
        )
        store.close()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "\n"
            "常用命令：\n"
            "  python -m tools.agent_eval.feature_session.session_perf_eval                          # 默认跑 size=10,100,1000\n"
            "  python -m tools.agent_eval.feature_session.session_perf_eval --sizes 100,1000,5000   # 指定 size\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sizes",
        default="10,100,1000",
        help="逗号分隔的 session 数量档位（默认: %(default)s）。例: --sizes 100,1000,5000",
    )
    args = parser.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    print(f"\n📊 list_sessions 性能基准（每个测量取 5 次中位数，列含义见 -h）\n")
    header = f"{'size':>6}  {'no-filter':>11}  {'id-prefix':>11}  {'keyword':>11}  {'limit=20':>11}  {'render-full':>13}  {'render-filt':>13}"
    print(header)
    print("-" * len(header))
    for n in sizes:
        r = _check_size(n)
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
        "\n判据（以最大 size 行对照；不达标考虑上索引 / FTS5）：\n"
        "  - 查询类 4 列 < 50ms\n"
        "  - 渲染类 2 列 < 200ms\n"
        "  - keyword / no-filter < 2x\n"
    )


if __name__ == "__main__":
    main()
