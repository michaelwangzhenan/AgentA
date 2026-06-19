"""测试 [`src/agent/core/critic_manager.py`](../src/agent/core/critic_manager.py) Phase 2.5 G1 + G2。

覆盖：
    - CriticVerdict dataclass 字段 + frozen / failure 默认值
    - _load_prompt：文件存在路径 + FileNotFoundError → RuntimeError 含路径
    - CriticManager.__init__：默认 threshold/timeout 取 config + critic prompt 缓存
    - review_grading：critic >= threshold → passed=True；< threshold → passed=False
    - review_grading：JudgeResult.score=None / chat 异常 / 超时 → failure=True passed=True
    - filter_chunks：empty hits / query → 直接返；critic 返回过滤；critic 失败软放行原始
    - _parse_rag_verdicts：合法 K 长度 + 非法长度 / 非 dict / score 非 0/5 兜底
    - 单例 get_critic_manager + reset_for_test

UT 全部 mock 外部 LLM（chat / judge_with_llm），不发任何真实请求。
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.agent.core import critic_manager as hm
from src.agent.core.critic_manager import (
    CriticManager,
    CriticVerdict,
    _load_prompt,
    get_critic_manager,
    reset_for_test,
)
from tools.eval_common import JudgeResult


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def critic_prompts(tmp_path: Path) -> tuple[Path, Path]:
    """临时 critic prompt 文件对，放进 tmp_path 不污染真目录。"""
    quiz = tmp_path / "quiz_critic.txt"
    rag = tmp_path / "rag_critic.txt"
    quiz.write_text("Quiz 评分维度（满分 5）：\n- 答案匹配度\n- 反馈合理性", encoding="utf-8")
    rag.write_text("RAG 相关性判定标准：\n- 5=相关 / 0=不相关", encoding="utf-8")
    return quiz, rag


@pytest.fixture
def manager(critic_prompts: tuple[Path, Path]) -> CriticManager:
    quiz_path, rag_path = critic_prompts
    return CriticManager(
        threshold=3.5, timeout=5.0,
        quiz_critic_path=quiz_path, rag_critic_path=rag_path,
    )


def _mock_chat_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


# ── CriticVerdict ────────────────────────────────────────────────────────────

class TestCriticVerdict:

    def test_default_failure_false(self) -> None:
        v = CriticVerdict(passed=True, score=4.0, reason="ok", raw="{}")
        assert v.failure is False

    def test_failure_explicit_true(self) -> None:
        v = CriticVerdict(passed=True, score=None, reason="超时", raw="", failure=True)
        assert v.failure is True

    def test_frozen(self) -> None:
        v = CriticVerdict(passed=True, score=4.0, reason="x", raw="")
        with pytest.raises((AttributeError, Exception)):
            v.passed = False  # type: ignore[misc]


# ── _load_prompt ──────────────────────────────────────────────────────────────

class TestLoadPrompt:

    def test_load_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "x.txt"
        p.write_text("hello world\n", encoding="utf-8")
        assert _load_prompt(p) == "hello world"

    def test_load_missing_raises_with_path(self, tmp_path: Path) -> None:
        p = tmp_path / "nope.txt"
        with pytest.raises(RuntimeError) as exc:
            _load_prompt(p)
        assert "nope.txt" in str(exc.value) or "缺失" in str(exc.value)


# ── CriticManager 初始化 ─────────────────────────────────────────────────────

class TestManagerInit:

    def test_uses_provided_threshold_and_timeout(
        self, critic_prompts: tuple[Path, Path],
    ) -> None:
        quiz_path, rag_path = critic_prompts
        m = CriticManager(
            threshold=2.5, timeout=10.0,
            quiz_critic_path=quiz_path, rag_critic_path=rag_path,
        )
        assert m.threshold == 2.5
        assert m.timeout == 10.0

    def test_falls_back_to_config_defaults(
        self, critic_prompts: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        quiz_path, rag_path = critic_prompts
        import src.config as _cfg
        monkeypatch.setattr(_cfg, "CRITIC_GRADING_THRESHOLD", 4.2)
        monkeypatch.setattr(_cfg, "CRITIC_LLM_TIMEOUT_SEC", 30.0)
        m = CriticManager(
            quiz_critic_path=quiz_path, rag_critic_path=rag_path,
        )
        assert m.threshold == 4.2
        assert m.timeout == 30.0

    def test_loads_critic_prompts_from_files(
        self, critic_prompts: tuple[Path, Path],
    ) -> None:
        quiz_path, rag_path = critic_prompts
        m = CriticManager(quiz_critic_path=quiz_path, rag_critic_path=rag_path)
        assert "答案匹配度" in m._quiz_criteria
        assert "RAG 相关性" in m._rag_criteria


# ── review_grading（Q1） ─────────────────────────────────────────────────────

class TestReviewGrading:

    def test_pass_when_critic_score_ge_threshold(self, manager: CriticManager) -> None:
        with patch(
            "src.agent.core.critic_manager.judge_with_llm",
            return_value=JudgeResult(score=4.5, reason="批改合理", raw="{}"),
        ):
            v = manager.review_grading(
                stem="什么是 RAG", user_answer="检索增强生成",
                correct_answer="Retrieval-Augmented Generation",
                agent_score=0.9, agent_feedback="基本对",
            )
        assert v.passed is True
        assert v.failure is False
        assert v.score == 4.5

    def test_fail_when_critic_score_below_threshold(self, manager: CriticManager) -> None:
        with patch(
            "src.agent.core.critic_manager.judge_with_llm",
            return_value=JudgeResult(score=2.0, reason="给分过高", raw="{}"),
        ):
            v = manager.review_grading(
                stem="x", user_answer="y", correct_answer="z",
                agent_score=1.0, agent_feedback="完全正确",
            )
        assert v.passed is False
        assert v.failure is False
        assert v.score == 2.0
        assert "给分过高" in v.reason

    def test_critic_internal_failure_returns_failure_true_passed_true(
        self, manager: CriticManager,
    ) -> None:
        """critic 自身失败（score=None）→ failure=True, passed=True 软放行。"""
        with patch(
            "src.agent.core.critic_manager.judge_with_llm",
            return_value=JudgeResult(score=None, reason="JSON 解析失败", raw="bad"),
        ):
            v = manager.review_grading(
                stem="x", user_answer="y", correct_answer="z",
                agent_score=0.5, agent_feedback="ok",
            )
        assert v.passed is True
        assert v.failure is True
        assert v.score is None

    def test_chat_exception_returns_failure_true(self, manager: CriticManager) -> None:
        with patch(
            "src.agent.core.critic_manager.judge_with_llm",
            side_effect=RuntimeError("network down"),
        ):
            v = manager.review_grading(
                stem="x", user_answer="y", correct_answer="z",
                agent_score=0.5, agent_feedback="ok",
            )
        assert v.passed is True
        assert v.failure is True
        assert "network down" in v.reason

    def test_timeout_returns_failure_true_passed_true(
        self, critic_prompts: tuple[Path, Path],
    ) -> None:
        """timeout 用很短 timeout + 让 judge_with_llm sleep 触发。"""
        quiz_path, rag_path = critic_prompts
        m = CriticManager(
            threshold=3.5, timeout=0.1,
            quiz_critic_path=quiz_path, rag_critic_path=rag_path,
        )

        def _slow_judge(**_: object) -> JudgeResult:
            time.sleep(1.0)
            return JudgeResult(score=4.0, reason="slow", raw="")

        with patch(
            "src.agent.core.critic_manager.judge_with_llm",
            side_effect=_slow_judge,
        ):
            v = m.review_grading(
                stem="x", user_answer="y", correct_answer="z",
                agent_score=0.5, agent_feedback="ok",
            )
        assert v.passed is True
        assert v.failure is True
        assert "超时" in v.reason


# ── filter_chunks（R1） ──────────────────────────────────────────────────────

class _FakeHit:
    """轻量 fake，只保留 filter_chunks 关心的 .document 属性。"""
    def __init__(self, doc: str) -> None:
        self.document = doc


class TestFilterChunks:

    def test_empty_hits_returns_empty(self, manager: CriticManager) -> None:
        assert manager.filter_chunks(query="x", hits=[]) == []

    def test_empty_query_returns_original(self, manager: CriticManager) -> None:
        hits = [_FakeHit("doc A"), _FakeHit("doc B")]
        assert manager.filter_chunks(query="   ", hits=hits) == hits

    def test_filter_keeps_relevant_drops_irrelevant(self, manager: CriticManager) -> None:
        hits = [_FakeHit("relevant content"), _FakeHit("noise"), _FakeHit("relevant 2")]
        raw = '{"verdicts": [{"i": 1, "score": 5}, {"i": 2, "score": 0}, {"i": 3, "score": 5}]}'
        with patch(
            "src.agent.core.critic_manager.chat",
            return_value=_mock_chat_response(raw),
        ):
            kept = manager.filter_chunks(query="something", hits=hits)
        assert len(kept) == 2
        assert kept[0].document == "relevant content"
        assert kept[1].document == "relevant 2"

    def test_filter_all_kept_when_all_relevant(self, manager: CriticManager) -> None:
        hits = [_FakeHit("a"), _FakeHit("b")]
        raw = '{"verdicts": [{"i": 1, "score": 5}, {"i": 2, "score": 5}]}'
        with patch(
            "src.agent.core.critic_manager.chat",
            return_value=_mock_chat_response(raw),
        ):
            kept = manager.filter_chunks(query="q", hits=hits)
        assert len(kept) == 2

    def test_filter_returns_empty_when_all_irrelevant(self, manager: CriticManager) -> None:
        hits = [_FakeHit("a"), _FakeHit("b")]
        raw = '{"verdicts": [{"i": 1, "score": 0}, {"i": 2, "score": 0}]}'
        with patch(
            "src.agent.core.critic_manager.chat",
            return_value=_mock_chat_response(raw),
        ):
            kept = manager.filter_chunks(query="q", hits=hits)
        assert kept == []

    def test_chat_exception_falls_back_to_original(self, manager: CriticManager) -> None:
        hits = [_FakeHit("a"), _FakeHit("b")]
        with patch(
            "src.agent.core.critic_manager.chat",
            side_effect=RuntimeError("oops"),
        ):
            kept = manager.filter_chunks(query="q", hits=hits)
        assert kept == hits

    def test_parse_failure_falls_back_to_original(self, manager: CriticManager) -> None:
        hits = [_FakeHit("a"), _FakeHit("b")]
        with patch(
            "src.agent.core.critic_manager.chat",
            return_value=_mock_chat_response("not a json"),
        ):
            kept = manager.filter_chunks(query="q", hits=hits)
        assert kept == hits

    def test_verdicts_length_mismatch_falls_back(self, manager: CriticManager) -> None:
        """K=2 但 critic 只给 1 条 verdict → 解析失败 → 软放行。"""
        hits = [_FakeHit("a"), _FakeHit("b")]
        raw = '{"verdicts": [{"i": 1, "score": 5}]}'
        with patch(
            "src.agent.core.critic_manager.chat",
            return_value=_mock_chat_response(raw),
        ):
            kept = manager.filter_chunks(query="q", hits=hits)
        assert kept == hits

    def test_timeout_falls_back_to_original(
        self, critic_prompts: tuple[Path, Path],
    ) -> None:
        quiz_path, rag_path = critic_prompts
        m = CriticManager(
            threshold=3.5, timeout=0.1,
            quiz_critic_path=quiz_path, rag_critic_path=rag_path,
        )
        hits = [_FakeHit("a")]

        def _slow_chat(*args: object, **kwargs: object) -> SimpleNamespace:
            time.sleep(1.0)
            return _mock_chat_response('{"verdicts":[{"i":1,"score":5}]}')

        with patch("src.agent.core.critic_manager.chat", side_effect=_slow_chat):
            kept = m.filter_chunks(query="q", hits=hits)
        assert kept == hits

    def test_truncates_long_chunks(self, manager: CriticManager) -> None:
        """单条 chunk 超过截断阈值 → 内部截断，不撑爆 prompt。"""
        long_doc = "x" * 5000
        hits = [_FakeHit(long_doc)]
        captured: dict[str, str] = {}

        def _capture_chat(messages: list[dict[str, str]], **_: object) -> SimpleNamespace:
            captured["user"] = messages[1]["content"]
            return _mock_chat_response('{"verdicts":[{"i":1,"score":5}]}')

        with patch("src.agent.core.critic_manager.chat", side_effect=_capture_chat):
            manager.filter_chunks(query="q", hits=hits)
        assert "user" in captured
        # 截断标记或 800 字以内（含截断省略号）
        assert "…" in captured["user"] or len(captured["user"]) < 5000


# ── _parse_rag_verdicts ──────────────────────────────────────────────────────

class TestParseRagVerdicts:

    def test_parse_clean_json(self) -> None:
        raw = '{"verdicts":[{"i":1,"score":5},{"i":2,"score":0}]}'
        scores = CriticManager._parse_rag_verdicts(raw, k=2)
        assert scores == [5.0, 0.0]

    def test_parse_with_markdown_block(self) -> None:
        raw = '```json\n{"verdicts":[{"i":1,"score":5}]}\n```'
        scores = CriticManager._parse_rag_verdicts(raw, k=1)
        assert scores == [5.0]

    def test_parse_score_outside_0_5_normalized(self) -> None:
        """score=3 应归 5（≥2.5）；score=1 应归 0。"""
        raw = '{"verdicts":[{"i":1,"score":3},{"i":2,"score":1}]}'
        scores = CriticManager._parse_rag_verdicts(raw, k=2)
        assert scores == [5.0, 0.0]

    def test_parse_length_mismatch_returns_none(self) -> None:
        raw = '{"verdicts":[{"i":1,"score":5}]}'
        assert CriticManager._parse_rag_verdicts(raw, k=2) is None

    def test_parse_no_json_returns_none(self) -> None:
        assert CriticManager._parse_rag_verdicts("plain text", k=1) is None

    def test_parse_non_dict_verdict_returns_none(self) -> None:
        raw = '{"verdicts":[5, 0]}'
        assert CriticManager._parse_rag_verdicts(raw, k=2) is None


# ── 单例 ─────────────────────────────────────────────────────────────────────

class TestSingleton:

    def test_get_returns_same_instance(
        self, critic_prompts: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        quiz_path, rag_path = critic_prompts
        monkeypatch.setattr(hm, "_DEFAULT_QUIZ_CRITIC", quiz_path)
        monkeypatch.setattr(hm, "_DEFAULT_RAG_CRITIC", rag_path)
        reset_for_test()
        try:
            m1 = get_critic_manager()
            m2 = get_critic_manager()
            assert m1 is m2
        finally:
            reset_for_test()

    def test_reset_for_test_clears_singleton(
        self, critic_prompts: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        quiz_path, rag_path = critic_prompts
        monkeypatch.setattr(hm, "_DEFAULT_QUIZ_CRITIC", quiz_path)
        monkeypatch.setattr(hm, "_DEFAULT_RAG_CRITIC", rag_path)
        reset_for_test()
        try:
            m1 = get_critic_manager()
            reset_for_test()
            m2 = get_critic_manager()
            assert m1 is not m2
        finally:
            reset_for_test()
