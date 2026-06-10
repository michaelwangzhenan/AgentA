"""faithfulness / answer-relevance 评委 UT（iter_14）。judge LLM 全部 mock。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from tools.agent_eval.judge import judge_answer_relevance, judge_faithfulness


def _fake_resp(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_faithfulness_parses_score() -> None:
    with patch("tools.agent_eval.judge.llm_judge.chat",
               return_value=_fake_resp('{"score": 4.5, "reason": "有据可依"}')):
        res = judge_faithfulness("问题?", "检索资料内容", "基于资料的答案")
    assert res.ok
    assert res.score == 4.5


def test_relevance_parses_score() -> None:
    with patch("tools.agent_eval.judge.llm_judge.chat",
               return_value=_fake_resp('{"score": 3.0, "reason": "切题"}')):
        res = judge_answer_relevance("问题?", "切题的答案")
    assert res.ok
    assert res.score == 3.0


def test_empty_answer_soft_fail() -> None:
    # output 为空时 judge 直接软返回 None（不调 LLM）
    res = judge_faithfulness("问题?", "资料", "")
    assert not res.ok
    assert res.score is None


def test_llm_error_soft_fail() -> None:
    with patch("tools.agent_eval.judge.llm_judge.chat", side_effect=RuntimeError("boom")):
        res = judge_answer_relevance("问题?", "答案")
    assert not res.ok
    assert res.score is None
