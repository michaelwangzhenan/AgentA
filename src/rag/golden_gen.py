"""RAG 入库后自动生成 golden 候选。

入库一篇资料后，调 LLM 据其内容生成若干"该问什么 + 期望命中关键词"的评估题，写入
``GoldenStore``（来源 ai、状态 pending），等管理员在前端审核后合入正式评估集。

设计要点：
- **后台跑、用户不感知**：由 Web 上传路由 fire-and-forget 调度（见 ``src/api/routes/kb.py``）。
- **软失败**：解析 / LLM / 落库任一步出错只记日志，绝不影响主入库链路。
- 期望字段对齐 ``GoldenStore`` / rag_eval 黄金集：query + expected_keywords +
  expected_source_contains（用文档 source 作来源匹配锚点）+ type。
- 出题数：``⌈字数 / 1000⌉``，不超过请求 / env 的 ``golden_max_q`` 上限；大文件分段多轮 LLM。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

# 小文件单次送全文；超过则滑动窗口分段（与旧版 6000 截断对齐）
_SMALL_FILE_MAX_CHARS = 6000
_SEGMENT_SIZE = 5500
_SEGMENT_OVERLAP = 400

_GOLDEN_TYPES: frozenset[str] = frozenset({
    "personal-bio",
    "personal-project",
    "3gpp-def",
    "3gpp-proc",
    "3gpp-param",
    "project-impl",
})

_GEN_SYS = """你是一个 RAG 评估数据集出题助手。给你一篇资料的**片段**，请据其内容出 {max_q} 道"检索评估题"，
用来检验知识库能否就这篇资料的内容召回到正确片段。

要求：
- 每道题是一个用户**可能真实提出**的问题，答案能在本片段里找到。
- 给每题配 2-4 个 expected_keywords：必须来自本片段正文的关键术语 / 实体（OR 命中即可）。
- 为每题选 type，只能从以下取值选一：
  personal-bio | personal-project | 3gpp-def | 3gpp-proc | 3gpp-param | project-impl
- 禁止泛问（如「本文主要内容」「请总结全文」）。
- 问题用资料本身的语言（中文资料出中文题，英文资料出英文题）。
{segment_line}{hint_line}
严格输出 JSON 数组，每个元素形如：
{{"query": "<问题>", "expected_keywords": ["<词1>", "<词2>"], "type": "<类型>"}}

只输出 JSON 数组一段，不要 markdown 代码块、不要前后说明。"""


def _source_hints(source: str) -> str:
    """据文件名给 LLM 一点 type 倾向（不参与检索）。"""
    s = (source or "").lower()
    lines: list[str] = []
    if any(x in s for x in ("resume", "简历", "cv")):
        lines.append("- 资料似个人简历，type 优先 personal-bio / personal-project。")
    if any(x in s for x in ("ts-", "3gpp", "38.", "23.", "24.", "25.")):
        lines.append("- 资料似 3GPP 技术规范，type 优先 3gpp-def / 3gpp-proc / 3gpp-param。")
    if "project" in s or "项目" in s:
        lines.append("- 资料似项目说明，type 可考虑 project-impl / personal-project。")
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


def _split_segments(text: str) -> list[str]:
    """大文件滑动窗口分段；小文件返回单段全文。"""
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= _SMALL_FILE_MAX_CHARS:
        return [body]
    segments: list[str] = []
    start = 0
    while start < len(body):
        end = min(start + _SEGMENT_SIZE, len(body))
        segments.append(body[start:end])
        if end >= len(body):
            break
        start = max(start + 1, end - _SEGMENT_OVERLAP)
    return segments


def _allocate_per_segment(max_q: int, n_segments: int) -> list[int]:
    """把总题数轮询分配到各段；某段为 0 则跳过 LLM 调用。"""
    if n_segments <= 0 or max_q <= 0:
        return []
    counts = [0] * n_segments
    for i in range(max_q):
        counts[i % n_segments] += 1
    return counts


def _normalize_query_key(query: str) -> str:
    return re.sub(r"\s+", "", (query or "").strip().lower())


def _normalize_type(raw: Any) -> str:
    t = str(raw or "").strip().lower()
    return t if t in _GOLDEN_TYPES else ""


def _parse_llm_candidates(raw: str, max_q: int) -> list[dict[str, Any]]:
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        logger.warning("[golden_gen] LLM 返回非 JSON 数组，跳过: %r", raw[:200])
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.warning("[golden_gen] JSON 解析失败（已忽略）: %s", e)
        return []
    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    n = max(1, int(max_q))
    for it in data[:n]:
        if not isinstance(it, dict):
            continue
        q = str(it.get("query", "")).strip()
        if not q:
            continue
        kws = it.get("expected_keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        out.append({
            "query": q,
            "expected_keywords": [str(k).strip() for k in kws if str(k).strip()],
            "type": _normalize_type(it.get("type")),
        })
    return out


def _call_llm_for_segment(
    snippet: str,
    max_q: int,
    llm_model: str | None,
    *,
    segment_index: int,
    segment_total: int,
    source: str,
) -> list[dict[str, Any]]:
    if not snippet.strip() or max_q <= 0:
        return []
    n = max(1, int(max_q))
    segment_line = (
        f"- 这是文档第 {segment_index}/{segment_total} 段，只根据本段内容出题。\n"
        if segment_total > 1
        else ""
    )
    hint_line = _source_hints(source)
    from src.llm.provider import chat

    msgs = [
        {
            "role": "system",
            "content": _GEN_SYS.format(
                max_q=n,
                segment_line=segment_line,
                hint_line=hint_line,
            ),
        },
        {"role": "user", "content": f"## 资料正文\n{snippet}"},
    ]
    try:
        if llm_model:
            with config.use_llm_prefs(llm_model, False, 0):
                resp = chat(msgs, temperature=0.3)
        else:
            resp = chat(msgs, temperature=0.3)
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001 — 后台旁路，吞异常
        logger.warning("[golden_gen] LLM 生成失败（已忽略）: %s", e)
        return []
    return _parse_llm_candidates(raw, n)


def generate_candidates(
    text: str,
    max_q: int,
    llm_model: str | None = None,
    *,
    source: str = "",
) -> list[dict[str, Any]]:
    """调 LLM 据资料正文生成 golden 候选；失败返回空列表（不抛）。"""
    body = (text or "").strip()
    if not body:
        return []
    total_q = max(1, int(max_q))
    segments = _split_segments(body)
    if not segments:
        return []
    per_seg = _allocate_per_segment(total_q, len(segments))

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, (seg, q_n) in enumerate(zip(segments, per_seg, strict=False), start=1):
        if q_n <= 0:
            continue
        batch = _call_llm_for_segment(
            seg,
            q_n,
            llm_model,
            segment_index=idx,
            segment_total=len(segments),
            source=source,
        )
        for c in batch:
            key = _normalize_query_key(c["query"])
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(c)
            if len(merged) >= total_q:
                return merged[:total_q]
    return merged[:total_q]


def _write_golden_candidates(
    text: str,
    source: str,
    doc_id: str,
    max_q: int | None,
    llm_model: str,
) -> int:
    """据正文 LLM 出题并写入 GoldenStore；返回写入条数。"""
    from src.rag.golden_options import clamp_golden_max_q, compute_golden_max_q
    from src.stores.golden_store import SOURCE_AI, STATUS_PENDING, get_shared_store

    q = compute_golden_max_q(len(text or ""), clamp_golden_max_q(max_q))
    candidates = generate_candidates(text, q, llm_model=llm_model, source=source)
    if not candidates:
        return 0

    segments = _split_segments(text)
    n_seg = len(segments) or 1
    store = get_shared_store()
    written = 0
    for c in candidates:
        try:
            note = "AI 生成，待审核" if n_seg <= 1 else "AI 生成（多段），待审核"
            store.create(
                query=c["query"],
                expected_keywords=c.get("expected_keywords"),
                expected_source_contains=source,
                note=note,
                source=SOURCE_AI,
                status=STATUS_PENDING,
                doc_id=doc_id,
                golden_type=c.get("type") or "",
            )
            written += 1
        except Exception:  # noqa: BLE001 — 单条失败不影响其它
            logger.debug("[golden_gen] 写入单条候选失败（已忽略）", exc_info=True)
    logger.info(
        "[golden_gen] 文档 %s 自动生成 golden 候选 %d 条（pending，目标 %d）",
        source,
        written,
        q,
    )
    return written


def run_generation_for_text(
    text: str,
    source: str,
    doc_id: str = "",
    max_q: int | None = None,
    llm_model: str | None = None,
    *,
    force: bool = False,
) -> int:
    """据已有正文 LLM 出题 → 写入 GoldenStore（pending / ai）。返回写入条数。

    全程软失败：任何异常只记日志、返回 0。
    """
    if not llm_model and not force:
        return 0
    if not llm_model:
        return 0
    try:
        return _write_golden_candidates(text, source, doc_id, max_q, llm_model)
    except Exception:  # noqa: BLE001 — 后台旁路，绝不抛
        logger.warning("[golden_gen] 自动生成 golden 失败（已忽略）", exc_info=True)
        return 0


def run_generation_for_file(
    file_path: str | Path,
    source: str,
    doc_id: str = "",
    max_q: int | None = None,
    llm_model: str | None = None,
    *,
    force: bool = False,
) -> int:
    """解析文件 → LLM 出题 → 写入 GoldenStore（pending / ai）。返回写入条数。

    全程软失败：任何异常只记日志、返回 0。
    max_q 为 UI/env **上限**；实际题数 = min(⌈字数/1K⌉, 上限)。
    llm_model 为 MODEL_CONFIGS 的 model id；force=True 时 llm_model 必填（L2 手动生成）。
    """
    if not llm_model and not force:
        return 0
    if not llm_model:
        return 0
    try:
        from src.rag.parser import parse_file

        text = parse_file(Path(file_path))
        return _write_golden_candidates(text, source, doc_id, max_q, llm_model)
    except Exception:  # noqa: BLE001 — 后台旁路，绝不抛
        logger.warning("[golden_gen] 自动生成 golden 失败（已忽略）", exc_info=True)
        return 0
