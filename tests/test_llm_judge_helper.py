"""
测试 [`tools.agent_eval.judge.judge_with_llm`](../tools/agent_eval/judge/llm_judge.py) 公共 helper（Phase 2.2 D6 / D11）。

覆盖：
    - 入参校验：empty prompt / output、非法 score 区间
    - JSON 解析路径：标准输出、含 markdown 代码块、含前后说明文字
    - 容错：LLM 异常、非 JSON 输出、score 越界、reason 缺失
    - JudgeResult.ok 便捷属性
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.agent_eval.judge import JudgeResult, judge_with_llm


def _mock_chat_response(content: str) -> SimpleNamespace:
    """构造 openai-like response 对象，便于 patch chat() 返回值。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


# ── 入参校验 ────────────────────────────────────────────────────────────────

class TestInputValidation:

    def test_empty_prompt_returns_none(self) -> None:
        res = judge_with_llm(prompt="  ", output="something", criteria="x")
        assert res.score is None
        assert "prompt" in res.reason.lower()
        assert not res.ok

    def test_empty_output_returns_none(self) -> None:
        res = judge_with_llm(prompt="x", output="", criteria="x")
        assert res.score is None
        assert "output" in res.reason.lower()

    def test_invalid_score_range_returns_none(self) -> None:
        res = judge_with_llm(
            prompt="x", output="y", criteria="z", score_min=5.0, score_max=0.0,
        )
        assert res.score is None
        assert "score 区间" in res.reason


# ── JSON 解析正常路径 ──────────────────────────────────────────────────────

class TestParseOk:

    def test_parses_basic_json(self) -> None:
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response('{"score": 4.5, "reason": "结构清晰"}'),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.ok
        assert res.score == 4.5
        assert res.reason == "结构清晰"

    def test_parses_within_markdown_code_block(self) -> None:
        raw = '```json\n{"score": 3.0, "reason": "ok"}\n```'
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response(raw),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.ok
        assert res.score == 3.0

    def test_parses_with_surrounding_text(self) -> None:
        raw = '评分结果如下：{"score": 4.0, "reason": "good"} 谢谢'
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response(raw),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.ok
        assert res.score == 4.0

    def test_missing_reason_defaults(self) -> None:
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response('{"score": 2.5}'),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.ok
        assert res.reason == "（无）"

    def test_custom_score_range(self) -> None:
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response('{"score": 7.5, "reason": "x"}'),
        ):
            res = judge_with_llm(
                prompt="q", output="a", criteria="c", score_max=10.0,
            )
        assert res.ok
        assert res.score == 7.5


# ── 容错路径 ────────────────────────────────────────────────────────────────

class TestFailures:

    def test_llm_exception_soft_fail(self) -> None:
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            side_effect=RuntimeError("API 502"),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.score is None
        assert "API 502" in res.reason

    def test_non_json_response(self) -> None:
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response("评分：很好"),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.score is None
        assert "非 JSON" in res.reason

    def test_score_out_of_range(self) -> None:
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response('{"score": 9.9, "reason": "x"}'),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.score is None
        assert "越界" in res.reason
        # raw 应保留 LLM 原始返回便于排查
        assert "9.9" in res.raw

    def test_invalid_score_type(self) -> None:
        with patch(
            "tools.agent_eval.judge.llm_judge.chat",
            return_value=_mock_chat_response('{"score": "high", "reason": "x"}'),
        ):
            res = judge_with_llm(prompt="q", output="a", criteria="c")
        assert res.score is None
        assert "解析失败" in res.reason


# ── JudgeResult 数据类 ──────────────────────────────────────────────────────

class TestJudgeResult:

    def test_ok_property(self) -> None:
        assert JudgeResult(score=3.0, reason="x", raw="").ok is True
        assert JudgeResult(score=None, reason="err", raw="").ok is False

    def test_immutable(self) -> None:
        # frozen=True，应阻止字段修改
        r = JudgeResult(score=3.0, reason="x", raw="")
        with pytest.raises(Exception):
            r.score = 5.0  # type: ignore[misc]
