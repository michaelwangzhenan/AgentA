"""
RAG 检索评估脚本（Iter-5）。

跑前提：先 ingest 至少一遍知识库；脚本调用 src.rag.retriever.search 实际检索。

使用方式：
    python -m tools.rag_eval.runner                                  # 跑默认 golden，仅终端汇总
    python -m tools.rag_eval.runner --no-rewriter                    # 关闭 query 改写（基线对比）
    python -m tools.rag_eval.runner --no-rerank                      # 关闭 reranker（真关，透传给 retriever）
    python -m tools.rag_eval.runner -o tools/rag_eval/reports/m3.md  # 同时落盘 Markdown + .log trace
    python -m tools.rag_eval.runner -v                               # 终端打印 [search] 阶段化日志

设计原则：
    - 终端默认仅显示进度（\\r 单行刷新）+ 结果汇总；-v 才把 INFO 倒灌进终端。
    - -o 输出 Markdown 时自动 sidecar 一份 <out>.md.log 收集 INFO trace，便于事后诊断。
    - 详细结果（含 Miss 用例定位信息）全部走 -o 写到 Markdown，供人浏览 / 历史对比。
    - --no-rerank 通过 search(rerank=False) 透传给 retriever 内层，真正禁用 cross-encoder
      精排（早期实现只关 eval 层的二次 rerank，retriever 内层仍跑，导致 ablation 失效）。

黄金集格式（list[item]，逐条 item 字段如下）：
    query                    必填，str，用户问题
    expected_source          可选，str，精确匹配 hit.source（含 ext / 相对路径，建议用 expected_source_contains）
    expected_source_contains 可选，str，子串匹配 hit.source（如文件名/目录片段）
    expected_keywords        可选，list[str]，OR 关系：任一在 chunk 文本中出现即视为 keyword_hit
    note                     可选，str，自描述备注（不参与评估）

输出指标（k = top-K, 取自 config.RAG_TOP_K）：
    hit_source@k    expected_source / contains 在 top-K 中命中的比例
    hit_keyword@k   expected_keywords 任一在 top-K 文本中命中的比例
    hit_either@k    上述任一命中（更宽松）
    MRR             第一次命中位置的倒数平均（衡量"早命中"程度）

Markdown 报告结构（results-first：打开就能看到结果，无需滚动）：
    标题块                  时间戳 / git / python / provider
    ## 核心指标             指标 → 值 表格
    ## 环境与配置           Embeddings / Reranker / BM25 / Query rewrite / Retrieval 等
    ## Miss 用例 (N)         未命中样本 + 期望 vs 实际 top-3，CI 回归定位最有用

注意：未填 expected_source*/expected_keywords 的 item 会自动按"该指标的分母"剔除，
避免空目标污染统计。命中"以 expected_source* 在 OR 上的并集"为准。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 必须在 import src.config 之前加载 .env：config.py 在模块导入时即用 os.getenv
# 读取所有配置（含 *_API_KEY），而 rag_eval 入口不像 main.py / ingest.py 那样
# 自带 load_dotenv()，单独 `python -m tools.rag_eval.runner` 启动时若不显式加载，
# 会拿到空 key，导致 query 改写 / 翻译轴 LLM 调用全部 401 静默降级。
from dotenv import load_dotenv
load_dotenv(override=True)

# ---------------------------------------------------------------------------
# 评估期间彻底静音第三方进度条 / 下载条，避免 "Batches: 100%|##########|" 之类刷屏。
# 这一段必须在 import sentence_transformers / huggingface_hub / chromadb 之前生效。
# 仅 monkey-patch 当前进程的 tqdm 类，不修改任何 src/* 产品代码 ——
# main.py / ingestion 等其他入口照旧能看到自己的进度条。
# ---------------------------------------------------------------------------
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

# 模型 cold start 时 transformers / huggingface 会打 FutureWarning / UserWarning
# （如 "clean_up_tokenization_spaces was not set"），它们走 Python warnings 模块、
# 不经 logging，basicConfig 压不住，需要单独 filter。评估期只关心最终汇总，全部静音。
import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import tqdm as _tqdm  # noqa: E402 — 必须在 src.* import 之前完成 patch
import tqdm.auto as _tqdm_auto  # noqa: E402

_orig_tqdm_init = _tqdm.std.tqdm.__init__


def _silent_tqdm_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
    kwargs["disable"] = True
    _orig_tqdm_init(self, *args, **kwargs)


_tqdm.std.tqdm.__init__ = _silent_tqdm_init
_tqdm.tqdm.__init__ = _silent_tqdm_init
_tqdm_auto.tqdm.__init__ = _silent_tqdm_init

import src.config as config  # noqa: E402 — 必须在 load_dotenv / tqdm patch 之后
from src.rag.retriever import search  # noqa: E402 — 同上

logger = logging.getLogger(__name__)

# 调试 / 切数据集时直接改这里。CLI 不再暴露 --golden 选项，避免 ingest_eval.sh
# 之类批量脚本到处传路径，反而难追踪用了哪一份 golden。
DEFAULT_GOLDEN = Path(__file__).parent / "golden.json"


@dataclass
class CaseResult:
    query: str
    first_hit_rank: int | None       # min(first_source_rank, first_keyword_rank)，留给 MRR
    first_source_rank: int | None    # source 在 top-K 中的首次命中名次；None=miss
    first_keyword_rank: int | None   # keyword 在 top-K 中的首次命中名次；None=miss
    source_hit: bool                 # ≡ first_source_rank is not None
    keyword_hit: bool                # ≡ first_keyword_rank is not None
    has_source_target: bool
    has_keyword_target: bool
    top_sources: list[str] = field(default_factory=list)
    expected_source: str = ""
    expected_keywords: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    """
    评估报告。

    字段顺序 = 渲染顺序，按 "results-first" 排：
      1. 核心指标（items / k / hit_* / mrr）
      2. 实验开关（use_rewriter / use_rerank）
      3. metadata —— 影响结果的全部配置因子（git / 模型 / 阈值 / KB 状态）
      4. cases —— 逐条详情（最大块，给 Markdown 渲染 Miss 用例小节用）

    位置敏感指标（@1 / @3 / @k）专门设计来暴露 reranker 的真实工作量：
    rerank 主要价值在 top-1/2 精度，hit@k 这种"K 内任一命中"的粗指标看不到 +pp；
    hit@1 / hit@3 一旦出现差异，说明开关在做事。keyword 命中是 chunk-level（rank 弱相关），
    只保留 @k，避免噪音指标。
    """
    items: int = 0
    k: int = 0
    hit_source_at_1: float | None = None
    hit_source_at_3: float | None = None
    hit_source_at_k: float | None = None
    hit_keyword_at_k: float | None = None
    hit_either_at_1: float = 0.0
    hit_either_at_3: float = 0.0
    hit_either_at_k: float = 0.0
    mrr: float = 0.0
    use_rewriter: bool = False
    use_rerank: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)


def _git_info(repo_root: Path) -> dict[str, Any]:
    """读 git commit 短哈希 + dirty 标志；命令缺失或非 git 仓库时静默回退。"""
    out: dict[str, Any] = {"commit": "unknown", "dirty": False}
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            out["commit"] = r.stdout.strip()
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            out["dirty"] = bool(r.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return out


def _kb_counts(active: list[tuple[str, str, str]]) -> dict[str, int]:
    """实测每个启用 collection 的 chunk 数；ChromaDB 不可用 / 库未建好时退化为空 dict。"""
    counts: dict[str, int] = {}
    try:
        import chromadb  # noqa: WPS433 — 仅在评估收集 metadata 时需要
        client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
        for _alias, _model, coll in active:
            try:
                counts[coll] = client.get_or_create_collection(coll).count()
            except Exception:  # noqa: BLE001 — collection 不存在 / schema 不匹配 等
                counts[coll] = -1
    except Exception:  # noqa: BLE001 — chromadb 未装 / db 路径异常
        return {}
    return counts


def _golden_lang_split(items: list[dict[str, Any]]) -> tuple[int, int]:
    """粗略统计 golden 集中 EN / ZH query 数量（中文字符存在性判定，对评估足够）。"""
    en = 0
    for it in items:
        q = str(it.get("query", ""))
        has_zh = any(0x4e00 <= ord(c) <= 0x9fff for c in q)
        if not has_zh:
            en += 1
    return en, len(items) - en


def _collect_metadata(
    golden_path: Path,
    items: list[dict[str, Any]],
    k: int,
    use_rewriter_eff: bool,
    use_rerank_eff: bool,
) -> dict[str, Any]:
    """
    汇总"会影响评估结果"的所有因子。

    分组：
        env       运行环境（时间戳、git、python、provider）
        golden    数据集（路径、规模、双语分布）
        embeddings  active alias → (model, collection) 映射
        kb_counts collection 实际 chunk 数量（实测，便于和 commit 关联）
        reranker  开关 / 模型 / min_score / recall_multiplier
        dense_thresholds  全局 + per-model 阈值（Iter-2 / Iter-5）
        bm25      开关 / 经典超参 / RRF k
        query_rewrite  改写 / HyDE / 翻译轴 三个轴的开关 + max_queries
        retrieval top_k / per_source / chunk 切分
    """
    repo_root = Path(__file__).resolve().parents[2]
    active = config.iter_active_embeddings()
    en_q, zh_q = _golden_lang_split(items)

    return {
        "env": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "git": _git_info(repo_root),
            "python": platform.python_version(),
            "platform": sys.platform,
            "llm_provider": config.ACTIVE_PROVIDER,
        },
        "golden": {
            "path": str(golden_path),
            "size": len(items),
            "en_queries": en_q,
            "zh_queries": zh_q,
        },
        "embeddings": {
            "active_aliases": [a for a, _, _ in active],
            "by_alias": {a: {"model": m, "collection": c} for a, m, c in active},
        },
        "retriever": {
            # RAG_ACTIVE_EMBEDDINGS 原始配置值（可能含未知别名，与 active 经过 fallback 后未必一致）
            "active_aliases": list(config.RAG_ACTIVE_EMBEDDINGS),
        },
        "kb_counts": _kb_counts(active),
        "reranker": {
            "enabled": bool(config.RERANKER_ENABLED and use_rerank_eff),
            "model": config.RERANKER_MODEL,
            "recall_multiplier": config.RERANKER_RECALL_MULTIPLIER,
            "min_score": config.RAG_RERANK_MIN_SCORE,
        },
        "dense_thresholds": {
            "global": config.RAG_DENSE_MIN_SCORE,
            "per_model": dict(config.RAG_DENSE_MIN_SCORE_PER_MODEL),
        },
        "bm25": {
            "enabled": config.BM25_ENABLED,
            "k1": config.BM25_K1,
            "b": config.BM25_B,
            "rrf_k": config.RRF_K,
        },
        "query_rewrite": {
            "enabled": bool(config.RAG_QUERY_REWRITE_ENABLED and use_rewriter_eff),
            "max_queries": config.RAG_REWRITE_MAX_QUERIES,
            "hyde_enabled": config.RAG_HYDE_ENABLED,
            "translate_enabled": config.RAG_TRANSLATE_QUERY_ENABLED,
        },
        "retrieval": {
            "top_k": k,
            "k_per_source": config.RAG_K_PER_SOURCE,
            "chunk_size": config.CHUNK_SIZE,
            "chunk_overlap": config.CHUNK_OVERLAP,
        },
    }


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


# 进度条用：query 前 N 字截断 + 右侧 padding 到固定列宽，
# 这样 \r 覆盖时不会留下上一条 query 的尾巴。
_PROGRESS_QUERY_WIDTH = 50


def _print_progress(i: int, n: int, query: str) -> None:
    """\\r 单行刷新进度。tqdm 已被 monkey-patch disable，这里走最朴素的 stdout.write。"""
    # 中文字符宽度估算：取 query 前 N 个码点，长度仍按"字符数 < 视觉宽度"，
    # 对宽度敏感的对齐做不到完全像素级，但对终端"覆盖式刷新不留尾巴"已经足够。
    snippet = query[:_PROGRESS_QUERY_WIDTH]
    sys.stdout.write(
        f"\r  [{i:>3}/{n}] {snippet:<{_PROGRESS_QUERY_WIDTH}}"
    )
    sys.stdout.flush()


def _clear_progress_line() -> None:
    """评估循环结束后清掉进度行，给后续的汇总打印一张干净的画布。"""
    # 总宽度 = 4(缩进/括号) + 3+1+? + 1 + query_width，保守清 80 列足够
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()


def evaluate(
    items: list[dict[str, Any]],
    k: int,
    use_rewriter: bool,
    use_rerank: bool,
) -> EvalReport:
    """对每条黄金对调一次 search()，统计命中情况，同时打 \\r 进度。"""
    n = len(items)
    cases: list[CaseResult] = []

    # 位置敏感累积：分 1 / 3 / k 三档分桶，避免 evaluate 后再次遍历 cases。
    # _src_targets / _kw_targets 是各自分母（无目标的 item 不进入对应分母），
    # 其余按"命中名次 <= 阈值 R"递增。@k 与旧版 src_hits / kw_hits 完全等价。
    src_targets = 0
    kw_targets = 0
    src_hits_at_1 = 0
    src_hits_at_3 = 0
    src_hits_at_k = 0
    kw_hits_at_k = 0
    either_at_1 = 0
    either_at_3 = 0
    either_at_k = 0
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

    # rerank 不再由 eval 包外层冗余精排，而是通过 search(rerank=...) 透传给 retriever
    # 内层，让 retriever 自己决定是否启用 cross-encoder：
    #   · use_rerank=True  → search(rerank=None)，retriever 走 config.RERANKER_ENABLED
    #   · use_rerank=False → search(rerank=False)，retriever 强制跳过
    # 早期版本在拿到 search() 结果后又调一次 rerank_fn，造成 double-rerank：
    # 关 eval 层 rerank_fn 仍然不能关掉 retriever 内层那一次，
    # baseline vs --no-rerank 因此完全等同。这里彻底删除外层调用。
    search_rerank: bool | None = None if use_rerank else False
    # 同步把"实际生效的 rerank 状态"反映到 use_rerank：retriever 还可能因
    # config.RERANKER_ENABLED=False 默默不跑，把它显式落地，避免后续 metadata
    # 显示 ON 但 trace 显示 skipped 这种和实际不一致。
    if use_rerank and not config.RERANKER_ENABLED:
        use_rerank = False

    for i, item in enumerate(items, start=1):
        query: str = item["query"]
        _print_progress(i, n, query)

        keywords: list[str] = item.get("expected_keywords") or []
        has_src = bool(item.get("expected_source") or item.get("expected_source_contains"))
        has_kw = bool(keywords)

        if has_src:
            src_targets += 1
        if has_kw:
            kw_targets += 1

        queries = list(expand_fn(query)) if expand_fn else [query]
        hits = search(query, top_k=k, queries=queries, rerank=search_rerank)

        # 分别记录 source / keyword 的首次命中名次，便于 hit@1 / hit@3 这种位置
        # 敏感指标分桶。两个 rank 都拿到后可提前 break，避免无谓比较。
        first_source_rank: int | None = None
        first_keyword_rank: int | None = None
        for rank, h in enumerate(hits, start=1):
            if has_src and first_source_rank is None and _is_source_match(item, h.source):
                first_source_rank = rank
            if has_kw and first_keyword_rank is None and _is_keyword_match(keywords, h.document or ""):
                first_keyword_rank = rank
            if (not has_src or first_source_rank is not None) \
               and (not has_kw or first_keyword_rank is not None):
                break

        case_src_hit = first_source_rank is not None
        case_kw_hit = first_keyword_rank is not None

        # 把 source / keyword 各自的首次命中名次合并成 "either 首次命中"，给 MRR 用
        if first_source_rank and first_keyword_rank:
            first_rank: int | None = min(first_source_rank, first_keyword_rank)
        else:
            first_rank = first_source_rank or first_keyword_rank

        # 分桶累积（含目标的 item 才进入对应分母）
        if has_src and first_source_rank is not None:
            if first_source_rank <= 1:
                src_hits_at_1 += 1
            if first_source_rank <= 3:
                src_hits_at_3 += 1
            src_hits_at_k += 1  # hits 长度本身就 <= k，能进 for 循环就 ≤ k
        if has_kw and first_keyword_rank is not None:
            kw_hits_at_k += 1
        if first_rank is not None:
            if first_rank <= 1:
                either_at_1 += 1
            if first_rank <= 3:
                either_at_3 += 1
            either_at_k += 1
            rr_sum += 1.0 / first_rank

        # expected_source / keywords 一并塞进 CaseResult，便于 Markdown
        # 在 Miss 小节里展示"期望 vs 实际"，免去渲染时再回查 golden
        exp_src = item.get("expected_source") or item.get("expected_source_contains") or ""

        cases.append(
            CaseResult(
                query=query,
                first_hit_rank=first_rank,
                first_source_rank=first_source_rank,
                first_keyword_rank=first_keyword_rank,
                source_hit=case_src_hit,
                keyword_hit=case_kw_hit,
                has_source_target=has_src,
                has_keyword_target=has_kw,
                top_sources=[h.source for h in hits[:3]],
                expected_source=exp_src,
                expected_keywords=list(keywords),
            )
        )

    _clear_progress_line()

    # 含目标的样本占比；无目标的 item 不进入对应分母（hit_source / hit_keyword）。
    # hit_either 用全样本 n 作分母（任意命中都算赢，包括只测 keyword 的 item）。
    def _pct(num: int, den: int) -> float | None:
        return (num / den) if den else None

    hit_src_at_1 = _pct(src_hits_at_1, src_targets)
    hit_src_at_3 = _pct(src_hits_at_3, src_targets)
    hit_src_at_k = _pct(src_hits_at_k, src_targets)
    hit_kw_at_k = _pct(kw_hits_at_k, kw_targets)
    hit_either_at_1 = (either_at_1 / n) if n else 0.0
    hit_either_at_3 = (either_at_3 / n) if n else 0.0
    hit_either_at_k = (either_at_k / n) if n else 0.0
    mrr = (rr_sum / n) if n else 0.0

    return EvalReport(
        items=n,
        k=k,
        use_rewriter=use_rewriter,
        use_rerank=use_rerank,
        hit_source_at_1=hit_src_at_1,
        hit_source_at_3=hit_src_at_3,
        hit_source_at_k=hit_src_at_k,
        hit_keyword_at_k=hit_kw_at_k,
        hit_either_at_1=hit_either_at_1,
        hit_either_at_3=hit_either_at_3,
        hit_either_at_k=hit_either_at_k,
        mrr=mrr,
        cases=cases,
    )


def _load_golden(path: Path) -> list[dict[str, Any]]:
    """读黄金集；不存在直接抛。example 回退已下线 —— 调试请直接编辑 DEFAULT_GOLDEN 常量。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到黄金集: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"黄金集必须是 list[item]，实际类型: {type(data)}")
    for i, it in enumerate(data):
        if "query" not in it or not it["query"]:
            raise ValueError(f"item[{i}] 缺少 query 字段")
    return data


def _print_report(rep: EvalReport, report_path: Path | None = None) -> None:
    """终端汇总：核心指标 + 环境配置两块；逐条详情走 Markdown，不再在终端铺。"""
    def _fmt(v: float | None) -> str:
        return "—" if v is None else f"{v:.2%}"

    print("=" * 60)
    print("RAG 检索评估 — 结果")
    print("=" * 60)
    print(f"  样本数:         {rep.items}")
    print(f"  Top-K:          {rep.k}")
    print(
        f"  hit_source:     @1={_fmt(rep.hit_source_at_1)}  "
        f"@3={_fmt(rep.hit_source_at_3)}  @k={_fmt(rep.hit_source_at_k)}"
    )
    print(f"  hit_keyword@k:  {_fmt(rep.hit_keyword_at_k)}")
    print(
        f"  hit_either:     @1={rep.hit_either_at_1:.2%}  "
        f"@3={rep.hit_either_at_3:.2%}  @k={rep.hit_either_at_k:.2%}"
    )
    print(f"  MRR:            {rep.mrr:.4f}")

    m = rep.metadata or {}
    if m:
        print("-" * 60)
        print("环境和配置：")
        env = m.get("env", {})
        git = env.get("git", {})
        dirty_flag = "*" if git.get("dirty") else ""
        print(
            f"  环境:           git={git.get('commit', '?')}{dirty_flag}  "
            f"python={env.get('python', '?')}  provider={env.get('llm_provider', '?')}"
        )
        g = m.get("golden", {})
        print(
            f"  Golden:         {g.get('size', 0)} items"
            f"  (en={g.get('en_queries', 0)}, zh={g.get('zh_queries', 0)})"
        )
        em = m.get("embeddings", {})
        by = em.get("by_alias", {})
        kb = m.get("kb_counts", {})
        parts: list[str] = []
        for a in em.get("active_aliases", []):
            info = by.get(a, {})
            model_short = info.get("model", "?").split("/")[-1]
            coll = info.get("collection", "?")
            parts.append(f"{a}({model_short}, {coll}={kb.get(coll, '?')})")
        print(f"  Embeddings:     {', '.join(parts) or '?'}")
        rt_aliases = m.get("retriever", {}).get("active_aliases", [])
        print(f"  Retriever:      {','.join(rt_aliases) or '?'}")
        rr = m.get("reranker", {})
        rr_model_short = rr.get("model", "?").split("/")[-1]
        print(
            f"  Reranker:       {'ON ' if rr.get('enabled') else 'OFF'} "
            f"{rr_model_short}  min_score={rr.get('min_score', '?')}"
        )
        bm = m.get("bm25", {})
        qr = m.get("query_rewrite", {})
        print(
            f"  BM25/RRF:       {'ON' if bm.get('enabled') else 'OFF'}  "
            f"k1={bm.get('k1')}  b={bm.get('b')}  rrf_k={bm.get('rrf_k')}"
        )
        print(
            f"  Query rewrite:  {'ON' if qr.get('enabled') else 'OFF'}  "
            f"max={qr.get('max_queries')}  hyde={qr.get('hyde_enabled')}  "
            f"translate={qr.get('translate_enabled')}"
        )
    print("=" * 60)
    if report_path is not None:
        print(f"📁 报告已写入 {report_path}")


def _render_markdown(rep: EvalReport) -> str:
    """把 EvalReport 渲染成给人看的 Markdown：标题 + 核心指标表 + 环境配置 + Miss 用例。"""
    def _fmt(v: float | None) -> str:
        return "—" if v is None else f"{v:.2%}"

    m = rep.metadata or {}
    env = m.get("env", {})
    git = env.get("git", {})
    g = m.get("golden", {})
    em = m.get("embeddings", {})
    by = em.get("by_alias", {})
    kb = m.get("kb_counts", {})
    rr = m.get("reranker", {})
    bm = m.get("bm25", {})
    qr = m.get("query_rewrite", {})
    rt = m.get("retrieval", {})

    dirty_flag = "*" if git.get("dirty") else ""

    lines: list[str] = []
    lines.append("# RAG 检索评估报告")
    lines.append("")
    lines.append(f"- **时间**: {env.get('timestamp', '?')}")
    lines.append(f"- **Git**: {git.get('commit', '?')}{dirty_flag}")
    lines.append(f"- **Python**: {env.get('python', '?')}")
    lines.append(f"- **Provider**: {env.get('llm_provider', '?')}")
    lines.append("")

    lines.append("## 核心指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 样本数 | {rep.items} |")
    lines.append(f"| Top-K | {rep.k} |")
    # 位置敏感指标先列 @1 / @3 / @k，方便对比 rerank/rewriter 是否真在做事
    lines.append(f"| hit_source@1 | {_fmt(rep.hit_source_at_1)} |")
    lines.append(f"| hit_source@3 | {_fmt(rep.hit_source_at_3)} |")
    lines.append(f"| hit_source@k | {_fmt(rep.hit_source_at_k)} |")
    lines.append(f"| hit_keyword@k | {_fmt(rep.hit_keyword_at_k)} |")
    lines.append(f"| hit_either@1 | {rep.hit_either_at_1:.2%} |")
    lines.append(f"| hit_either@3 | {rep.hit_either_at_3:.2%} |")
    lines.append(f"| hit_either@k | {rep.hit_either_at_k:.2%} |")
    lines.append(f"| MRR | {rep.mrr:.4f} |")
    lines.append("")

    lines.append("## 环境与配置")
    lines.append("")
    lines.append(f"- **Golden**: {g.get('path', '?')} — {g.get('size', 0)} items "
                 f"(en={g.get('en_queries', 0)}, zh={g.get('zh_queries', 0)})")
    emb_parts: list[str] = []
    for a in em.get("active_aliases", []):
        info = by.get(a, {})
        model_short = info.get("model", "?").split("/")[-1]
        coll = info.get("collection", "?")
        emb_parts.append(f"{a}({model_short}, {coll}={kb.get(coll, '?')})")
    lines.append(f"- **Embeddings**: {', '.join(emb_parts) or '?'}")
    rt_aliases = m.get("retriever", {}).get("active_aliases", [])
    lines.append(f"- **Retriever**: {','.join(rt_aliases) or '?'}")
    rr_model_short = rr.get("model", "?").split("/")[-1]
    lines.append(
        f"- **Reranker**: {'ON' if rr.get('enabled') else 'OFF'}  {rr_model_short}  "
        f"min_score={rr.get('min_score', '?')}  recall_x{rr.get('recall_multiplier', '?')}"
    )
    lines.append(
        f"- **BM25/RRF**: {'ON' if bm.get('enabled') else 'OFF'}  "
        f"k1={bm.get('k1')}  b={bm.get('b')}  rrf_k={bm.get('rrf_k')}"
    )
    lines.append(
        f"- **Query rewrite**: {'ON' if qr.get('enabled') else 'OFF'}  "
        f"max={qr.get('max_queries')}  hyde={qr.get('hyde_enabled')}  "
        f"translate={qr.get('translate_enabled')}"
    )
    lines.append(
        f"- **Retrieval**: top_k={rt.get('top_k')}  k_per_source={rt.get('k_per_source')}  "
        f"chunk_size={rt.get('chunk_size')}  chunk_overlap={rt.get('chunk_overlap')}"
    )
    lines.append("")

    # Miss 用例：has_*_target 为 True 但都没命中。
    # 没目标的 case 不进 Miss（避免没期望的"自由 query"被计为漏召）。
    misses = [
        c for c in rep.cases
        if (c.has_source_target or c.has_keyword_target)
        and not (c.source_hit or c.keyword_hit)
    ]
    lines.append(f"## Miss 用例 ({len(misses)})")
    lines.append("")
    if not misses:
        lines.append("_全部命中，无 miss。_")
        lines.append("")
    else:
        for idx, c in enumerate(misses, start=1):
            lines.append(f"### {idx}. `{c.query}`")
            lines.append("")
            lines.append(f"- 期望 source: {c.expected_source or '—'}")
            kw_str = ", ".join(c.expected_keywords) if c.expected_keywords else "—"
            lines.append(f"- 期望 keywords: {kw_str}")
            if c.top_sources:
                lines.append("- 实际 top-3 source:")
                for s in c.top_sources:
                    lines.append(f"  - `{s}`")
            else:
                lines.append("- 实际 top-3 source: _无返回_")
            lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK 编码，遇到 ✓ ✗ 📁 等 Unicode 会抛 UnicodeEncodeError
    # 把 stdout/stderr reconfigure 成 utf-8 避免 _print_report 中途崩溃；
    # 旧版 Python / 被管道重定向的 stream 没有 reconfigure 方法，try 静默忽略
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="RAG 检索评估")
    ap.add_argument("--no-rewriter", action="store_true", help="禁用 query 改写（基线对比）")
    ap.add_argument("--no-rerank", action="store_true", help="禁用 reranker（基线对比）")
    ap.add_argument("-o", dest="output", default="",
                    help="把详细报告写到该 Markdown 文件（同时把 INFO trace 落到同名 .log）")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="终端打印 retriever 阶段化 INFO 日志（会干扰单行进度条）")
    ap.add_argument("--quiet", action="store_true",
                    help="进一步压到 ERROR（与 -v 互斥，-v 优先）")
    args = ap.parse_args(argv)

    # 日志策略（兼顾默认 UX 与诊断可观测性）：
    #   终端：默认 ERROR（保留 \r 单行进度条干净）；-v 开 INFO；--quiet 显式 ERROR；
    #   文件：给了 -o 时把 INFO 永久落到 <output>.log，无需重跑就能回看 [search] trace。
    # 这条折中替代了"默认 INFO 倒灌进度条"的方案：日常用看终端汇总，诊断时直接读
    # .log（reports/round2-no-rerank.md 旁边就是 round2-no-rerank.md.log）。
    # force=True 必要：上面 import src.rag.retriever 时若 retriever / chromadb /
    # sentence_transformers 任何一个偷调过 basicConfig，没 force=True 这行会被静默跳过。
    terminal_level = logging.INFO if args.verbose else logging.ERROR
    logging.basicConfig(
        level=logging.DEBUG,  # root 收所有，handler 各自决定阈值
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
        force=True,
    )
    logging.getLogger().handlers[0].setLevel(terminal_level)
    if args.output:
        log_path = Path(args.output).with_suffix(Path(args.output).suffix + ".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)

    items = _load_golden(DEFAULT_GOLDEN)

    k = config.RAG_TOP_K
    use_rewriter = (not args.no_rewriter) and config.RAG_QUERY_REWRITE_ENABLED
    use_rerank = not args.no_rerank

    # 评估前先打 header：首条 query 前可能要等模型 cold start，
    # 不打这行用户只能盯着空白屏幕猜进度
    print(
        f"RAG 检索评估 — {len(items)} cases, top-K={k}, "
        f"rewriter={'ON' if use_rewriter else 'OFF'}, "
        f"rerank={'ON' if use_rerank else 'OFF'}"
    )

    rep = evaluate(items, k=k, use_rewriter=use_rewriter, use_rerank=use_rerank)

    # 在 evaluate 之后收集 metadata：rep.use_rewriter / rep.use_rerank 已反映
    # "实际是否生效"（query_rewriter 不可用会被 evaluate 自动降级），所以这里
    # 用 rep.* 而非命令行 args，避免 metadata 显示和实际行为不一致。
    rep.metadata = _collect_metadata(
        golden_path=DEFAULT_GOLDEN,
        items=items,
        k=k,
        use_rewriter_eff=rep.use_rewriter,
        use_rerank_eff=rep.use_rerank,
    )

    # 先落盘 Markdown 再打印：_print_report 若在 Windows 控制台抛 UnicodeEncodeError
    # 会导致整个 main 退出码非 0，旧版会把 -o 写文件这一步也跳过；
    # 调换顺序后即便 print 崩了，至少报告已稳稳落盘
    report_path: Path | None = None
    if args.output:
        report_path = Path(args.output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_render_markdown(rep), encoding="utf-8")

    _print_report(rep, report_path=report_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
