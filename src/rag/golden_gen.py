"""RAG 入库后自动生成 golden 候选。

入库一篇资料后，调 LLM 据其内容生成若干"该问什么 + 期望命中关键词"的评估题，写入
``GoldenStore``（来源 ai、状态 pending），等管理员在前端审核后合入正式评估集。

设计要点：
- **后台跑、用户不感知**：由 Web 上传路由 fire-and-forget 调度（见 ``src/api/routes/kb.py``）。
- **软失败**：解析 / LLM / 落库任一步出错只记日志，绝不影响主入库链路。
- 期望字段对齐 ``GoldenStore`` / rag_eval 黄金集：query + expected_keywords +
  expected_source_contains（用文档 source 作来源匹配锚点）。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import src.config as config

logger = logging.getLogger(__name__)

_GEN_SYS = """你是一个 RAG 评估数据集出题助手。给你一篇资料，请据其内容出 {max_q} 道"检索评估题"，
用来检验知识库能否就这篇资料的内容召回到正确片段。

要求：
- 每道题是一个用户**可能真实提出**的问题，答案能在资料里找到。
- 给每题配 2-4 个 expected_keywords：命中即说明检索到了对的内容（用资料里的关键术语 / 实体）。
- 问题用资料本身的语言（中文资料出中文题，英文资料出英文题）。

严格输出 JSON 数组，每个元素形如：
{{"query": "<问题>", "expected_keywords": ["<词1>", "<词2>"]}}

只输出 JSON 数组一段，不要 markdown 代码块、不要前后说明。"""


def generate_candidates(
    text: str, max_q: int, llm_model: str | None = None,
) -> list[dict[str, Any]]:
    """调 LLM 据资料正文生成 golden 候选；失败返回空列表（不抛）。"""
    body = (text or "").strip()
    if not body:
        return []
    n = max(1, int(max_q))
    # 截断过长正文，控制 token：取前若干字符已足够出题
    snippet = body[:6000]
    from src.llm.provider import chat

    msgs = [
        {"role": "system", "content": _GEN_SYS.format(max_q=n)},
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
        })
    return out


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
    llm_model 为 MODEL_CONFIGS 的 model id；force=True 时 llm_model 必填（L2 手动生成）。
    """
    from src.rag.golden_options import clamp_golden_max_q

    if not llm_model and not force:
        return 0
    if not llm_model:
        return 0
    try:
        from src.rag.parser import parse_file

        text = parse_file(Path(file_path))
        q = clamp_golden_max_q(max_q)
        candidates = generate_candidates(text, q, llm_model=llm_model)
        if not candidates:
            return 0
        from src.stores.golden_store import (
            SOURCE_AI,
            STATUS_PENDING,
            get_shared_store,
        )

        store = get_shared_store()
        written = 0
        for c in candidates:
            try:
                store.create(
                    query=c["query"],
                    expected_keywords=c.get("expected_keywords"),
                    expected_source_contains=source,
                    note="入库自动生成，待审核",
                    source=SOURCE_AI,
                    status=STATUS_PENDING,
                    doc_id=doc_id,
                )
                written += 1
            except Exception:  # noqa: BLE001 — 单条失败不影响其它
                logger.debug("[golden_gen] 写入单条候选失败（已忽略）", exc_info=True)
        logger.info(
            "[golden_gen] 文档 %s 自动生成 golden 候选 %d 条（pending）", source, written
        )
        return written
    except Exception:  # noqa: BLE001 — 后台旁路，绝不抛
        logger.warning("[golden_gen] 自动生成 golden 失败（已忽略）", exc_info=True)
        return 0
