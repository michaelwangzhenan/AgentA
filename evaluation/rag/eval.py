"""
RAG 检索评估脚本（Iter-4）。

跑前提：先 ingest 至少一遍知识库；脚本调用 src.rag.retriever.search 实际检索。

使用方式：
    python -m evaluation.rag.eval                       # 用默认 golden.json（不存在则尝试 example）
    python -m evaluation.rag.eval --golden custom.json  # 指定黄金集
    python -m evaluation.rag.eval --k 10                # 评估 top-K
    python -m evaluation.rag.eval --no-rewriter         # 关闭 query 改写（用于基线对比）
    python -m evaluation.rag.eval --no-rerank           # 关闭 reranker（用于基线对比）
    python -m evaluation.rag.eval --json out.json       # 同时输出 JSON 文件供 diff/CI

黄金集格式（list[item]，逐条 item 字段如下）：
    query                    必填，str，用户问题
    expected_source          可选，str，精确匹配 hit.source（含 ext / 相对路径，建议用 expected_source_contains）
    expected_source_contains 可选，str，子串匹配 hit.source（如文件名/目录片段）
    expected_keywords        可选，list[str]，OR 关系：任一在 chunk 文本中出现即视为 keyword_hit
    note                     可选，str，自描述备注（不参与评估）

输出指标（k = top-K）：
    hit_source@k    expected_source / contains 在 top-K 中命中的比例
    hit_keyword@k   expected_keywords 任一在 top-K 文本中命中的比例
    hit_either@k    上述任一命中（更宽松）
    MRR             第一次命中位置的倒数平均（衡量"早命中"程度）

注意：未填 expected_source*/expected_keywords 的 item 会自动按"该指标的分母"剔除，
避免空目标污染统计。命中"以 expected_source* 在 OR 上的并集"为准。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import src.config as config
from src.rag.retriever import search

logger = logging.getLogger(__name__)

DEFAULT_GOLDEN = Path(__file__).parent / "golden.json"
EXAMPLE_GOLDEN = Path(__file__).parent / "golden.example.json"


@dataclass
class CaseResult:
    query: str
    first_hit_rank: int | None
    source_hit: bool
    keyword_hit: bool
    has_source_target: bool
    has_keyword_target: bool
    top_sources: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    items: int
    k: int
    use_rewriter: bool
    use_rerank: bool
    hit_source_at_k: float | None
    hit_keyword_at_k: float | None
    hit_either_at_k: float
    mrr: float
    cases: list[CaseResult] = field(default_factory=list)


def _is_source_match(item: dict[str, Any], hit_source: str) -> bool:
    exp = item.get("expected_source", "")
    exp_contains = item.get("expected_source_contains", "")
    if exp and hit_source == exp:
        return True
    if exp_contains and exp_contains in hit_source:
        return True
    return False


def _is_keyword_match(keywords: list[str], doc_text: str) -> bool:
    if not keywords:
        return False
    low = doc_text.lower()
    return any(kw.lower() in low for kw in keywords)


def evaluate(
    items: list[dict[str, Any]],
    k: int,
    use_rewriter: bool,
    use_rerank: bool,
) -> EvalReport:
    """对每条黄金对调一次 search()，统计命中情况。"""
    n = len(items)
    cases: list[CaseResult] = []

    src_targets = 0
    kw_targets = 0
    src_hits = 0
    kw_hits = 0
    either_hits = 0
    rr_sum = 0.0

    # query 改写按需引入；--no-rewriter / config 关闭时跳过 expand_queries 的 LLM 调用
    expand_fn = None
    if use_rewriter:
        try:
            from src.rag.query_rewriter import expand_queries as expand_fn  # type: ignore
        except Exception as e:
            logger.warning("[eval] query_rewriter 不可用，已自动禁用：%s", e)
            expand_fn = None
            use_rewriter = False

    rerank_fn = None
    if use_rerank and config.RERANKER_ENABLED:
        try:
            from src.rag.reranker import rerank as rerank_fn  # type: ignore
        except Exception as e:
            logger.warning("[eval] reranker 不可用，已自动禁用：%s", e)
            rerank_fn = None
            use_rerank = False

    for item in items:
        query: str = item["query"]
        keywords: list[str] = item.get("expected_keywords") or []
        has_src = bool(item.get("expected_source") or item.get("expected_source_contains"))
        has_kw = bool(keywords)

        if has_src:
            src_targets += 1
        if has_kw:
            kw_targets += 1

        queries = list(expand_fn(query)) if expand_fn else [query]
        hits = search(query, top_k=k, queries=queries)
        if rerank_fn and hits:
            try:
                hits = rerank_fn(query, hits, top_k=k)
            except Exception as e:
                logger.warning("[eval] rerank 失败，使用原始 hits: %s", e)

        first_rank: int | None = None
        case_src_hit = False
        case_kw_hit = False
        for rank, h in enumerate(hits, start=1):
            matched = False
            if has_src and _is_source_match(item, h.source):
                case_src_hit = True
                matched = True
            if has_kw and _is_keyword_match(keywords, h.document or ""):
                case_kw_hit = True
                matched = True
            if matched and first_rank is None:
                first_rank = rank

        if case_src_hit:
            src_hits += 1
        if case_kw_hit:
            kw_hits += 1
        if case_src_hit or case_kw_hit:
            either_hits += 1
        if first_rank is not None:
            rr_sum += 1.0 / first_rank

        cases.append(
            CaseResult(
                query=query,
                first_hit_rank=first_rank,
                source_hit=case_src_hit,
                keyword_hit=case_kw_hit,
                has_source_target=has_src,
                has_keyword_target=has_kw,
                top_sources=[h.source for h in hits[:3]],
            )
        )

    # 含目标的样本占比；无目标的不进入对应分母
    hit_src = (src_hits / src_targets) if src_targets else None
    hit_kw = (kw_hits / kw_targets) if kw_targets else None
    hit_either = (either_hits / n) if n else 0.0
    mrr = (rr_sum / n) if n else 0.0

    return EvalReport(
        items=n,
        k=k,
        use_rewriter=use_rewriter,
        use_rerank=use_rerank,
        hit_source_at_k=hit_src,
        hit_keyword_at_k=hit_kw,
        hit_either_at_k=hit_either,
        mrr=mrr,
        cases=cases,
    )


def _load_golden(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        if path == DEFAULT_GOLDEN and EXAMPLE_GOLDEN.exists():
            logger.warning(
                "[eval] %s 不存在，回退到示例 %s（请基于该示例编写自己的黄金集）",
                path, EXAMPLE_GOLDEN,
            )
            path = EXAMPLE_GOLDEN
        else:
            raise FileNotFoundError(f"找不到黄金集: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"黄金集必须是 list[item]，实际类型: {type(data)}")
    for i, it in enumerate(data):
        if "query" not in it or not it["query"]:
            raise ValueError(f"item[{i}] 缺少 query 字段")
    return data


def _print_report(rep: EvalReport) -> None:
    print("=" * 60)
    print("RAG 检索评估")
    print("=" * 60)
    print(f"  样本数:         {rep.items}")
    print(f"  Top-K:          {rep.k}")
    print(f"  Query 改写:     {rep.use_rewriter}")
    print(f"  Reranker:       {rep.use_rerank}")
    print("-" * 60)

    def _fmt(v: float | None) -> str:
        return "—" if v is None else f"{v:.2%}"

    print(f"  hit_source@k:   {_fmt(rep.hit_source_at_k)}")
    print(f"  hit_keyword@k:  {_fmt(rep.hit_keyword_at_k)}")
    print(f"  hit_either@k:   {rep.hit_either_at_k:.2%}")
    print(f"  MRR:            {rep.mrr:.4f}")
    print("-" * 60)
    print("逐条详情：")
    for c in rep.cases:
        flag = "✓" if (c.source_hit or c.keyword_hit) else "✗"
        rank_str = f"#{c.first_hit_rank}" if c.first_hit_rank else "miss"
        head = f"  {flag} [{rank_str:>5}] {c.query[:60]}"
        print(head)
        if not (c.source_hit or c.keyword_hit) and c.top_sources:
            print(f"          实际 top-3 source: {c.top_sources}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RAG 检索评估")
    ap.add_argument("--golden", default=str(DEFAULT_GOLDEN),
                    help=f"黄金集 JSON 路径（默认 {DEFAULT_GOLDEN.name}，不存在则用 golden.example.json）")
    ap.add_argument("--k", type=int, default=config.RAG_TOP_K,
                    help=f"评估 top-K，默认 RAG_TOP_K={config.RAG_TOP_K}")
    ap.add_argument("--no-rewriter", action="store_true", help="禁用 query 改写（基线对比）")
    ap.add_argument("--no-rerank", action="store_true", help="禁用 reranker（基线对比）")
    ap.add_argument("--json", dest="json_out", default="", help="把详细报告输出到该 JSON 文件")
    ap.add_argument("--quiet", action="store_true", help="不输出逐条详情")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    items = _load_golden(Path(args.golden))

    use_rewriter = (not args.no_rewriter) and config.RAG_QUERY_REWRITE_ENABLED
    use_rerank = not args.no_rerank
    rep = evaluate(items, k=args.k, use_rewriter=use_rewriter, use_rerank=use_rerank)

    if args.quiet:
        # 静默模式：只打 4 项汇总
        print(f"items={rep.items} k={rep.k} "
              f"hit_source@k={rep.hit_source_at_k} hit_keyword@k={rep.hit_keyword_at_k} "
              f"hit_either@k={rep.hit_either_at_k:.4f} MRR={rep.mrr:.4f}")
    else:
        _print_report(rep)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(rep), f, ensure_ascii=False, indent=2)
        print(f"📁 详细报告已写入 {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
