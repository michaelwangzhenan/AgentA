"""runner --llm 答案质量链路 UT（iter_14）。

全部 mock 外部依赖：search（检索）/ 答案生成 / 两个 LLM 评委，不发真实请求。
验证点：
  - 平均分聚合（评委软失败 score=None 的条目不计入平均）
  - --llm N 截断条数；N<=0 跑全部
  - 评委模型覆盖：EVAL_JUDGE_MODEL 有效时评分期间切到评委模型；非法时回落回答模型
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import src.config as config
from tools.eval_common import JudgeResult
from tools.rag_eval import runner


def _items(n: int) -> list[dict]:
    return [{"query": f"问题{i}"} for i in range(n)]


def _fake_hits() -> list[SimpleNamespace]:
    return [SimpleNamespace(document="检索到的资料正文", source="doc.md")]


def _run(items, *, limit, faith_scores, rel_scores):
    """跑一遍 evaluate_answer_quality，按给定分数序列驱动两个评委 mock。"""
    faith_iter = iter(faith_scores)
    rel_iter = iter(rel_scores)

    def _fake_faith(question, context, answer):
        return JudgeResult(score=next(faith_iter), reason="r", raw="")

    def _fake_rel(question, answer):
        return JudgeResult(score=next(rel_iter), reason="r", raw="")

    with patch("tools.rag_eval.runner.search", return_value=_fake_hits()), \
         patch("tools.rag_eval.runner._generate_rag_answer", return_value="答案"), \
         patch("tools.rag_eval.rag_judge.judge_faithfulness", side_effect=_fake_faith), \
         patch("tools.rag_eval.rag_judge.judge_answer_relevance", side_effect=_fake_rel):
        return runner.evaluate_answer_quality(
            items, k=5, use_rewriter=False, use_rerank=True, limit=limit,
        )


def test_avg_excludes_soft_failures() -> None:
    # faith=[4,2,None] → 平均 3.0（None 不计）；rel 全 3 → 平均 3.0
    rep = _run(_items(3), limit=0, faith_scores=[4.0, 2.0, None], rel_scores=[3.0, 3.0, 3.0])
    assert rep.scored == 3
    assert len(rep.cases) == 3
    assert rep.avg_faithfulness == 3.0
    assert rep.avg_relevance == 3.0


def test_all_soft_fail_avg_none() -> None:
    rep = _run(_items(2), limit=0, faith_scores=[None, None], rel_scores=[None, None])
    assert rep.avg_faithfulness is None
    assert rep.avg_relevance is None


def test_limit_truncates() -> None:
    rep = _run(_items(5), limit=2, faith_scores=[4.0, 4.0], rel_scores=[4.0, 4.0])
    assert rep.scored == 2
    assert len(rep.cases) == 2


def test_limit_zero_runs_all() -> None:
    rep = _run(_items(4), limit=0, faith_scores=[4.0] * 4, rel_scores=[4.0] * 4)
    assert rep.scored == 4


def test_judge_model_override_applied(monkeypatch) -> None:
    # 评委有单独模型时，评分期间 current_active_model 应切到评委模型
    judge_key = next(iter(config.MODEL_CONFIGS))
    monkeypatch.setattr(config, "EVAL_JUDGE_MODEL", judge_key)
    seen: list[str] = []

    def _capture_faith(question, context, answer):
        seen.append(config.current_active_model())
        return JudgeResult(score=4.0, reason="r", raw="")

    def _fake_rel(question, answer):
        return JudgeResult(score=4.0, reason="r", raw="")

    with patch("tools.rag_eval.runner.search", return_value=_fake_hits()), \
         patch("tools.rag_eval.runner._generate_rag_answer", return_value="答案"), \
         patch("tools.rag_eval.rag_judge.judge_faithfulness", side_effect=_capture_faith), \
         patch("tools.rag_eval.rag_judge.judge_answer_relevance", side_effect=_fake_rel):
        rep = runner.evaluate_answer_quality(
            _items(1), k=5, use_rewriter=False, use_rerank=True, limit=0,
        )

    assert rep.judge_model == judge_key
    assert seen == [judge_key]


def test_invalid_judge_model_falls_back(monkeypatch) -> None:
    # 非法评委模型回落回答模型：judge_model 标签 = 回答模型，评分期间不加覆盖
    monkeypatch.setattr(config, "EVAL_JUDGE_MODEL", "__not_a_real_model__")
    answer_model = config.current_active_model()
    seen: list[str] = []

    def _capture_faith(question, context, answer):
        seen.append(config.current_active_model())
        return JudgeResult(score=4.0, reason="r", raw="")

    def _fake_rel(question, answer):
        return JudgeResult(score=4.0, reason="r", raw="")

    with patch("tools.rag_eval.runner.search", return_value=_fake_hits()), \
         patch("tools.rag_eval.runner._generate_rag_answer", return_value="答案"), \
         patch("tools.rag_eval.rag_judge.judge_faithfulness", side_effect=_capture_faith), \
         patch("tools.rag_eval.rag_judge.judge_answer_relevance", side_effect=_fake_rel):
        rep = runner.evaluate_answer_quality(
            _items(1), k=5, use_rewriter=False, use_rerank=True, limit=0,
        )

    assert rep.judge_model == answer_model
    assert seen == [answer_model]
