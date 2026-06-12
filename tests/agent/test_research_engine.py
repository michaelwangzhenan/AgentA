"""
test_research_engine —— Deep Research 四阶段编排 UT

全程 mock `chat` / `execute_tool`，不发真 LLM、不查真检索。
fake_chat 按 system prompt 关键词分派各阶段返回，对子代理并行乱序鲁棒。

覆盖：
- 规划解析 + 数量裁剪到上限
- 规划失败软降级为单子问题
- happy path 四阶段事件序列 + 落库（仅用户问题 + 最终报告两条）
- 子代理软失败（全空内容 → status=failed，不中断整体）
- 反思补查触发额外子代理
- 子代理工具调用路径（execute_tool 被调 + 来源计数）
- _parse_json 宽松解析
"""
from __future__ import annotations

from typing import Any

import pytest

import src.config as _cfg
import src.agent.core.research_engine as re_mod
from src.agent.core.research_engine import ResearchEngine, _parse_json


# ── fake LLM response 构件 ────────────────────────────────────────────────

class _Msg:
    def __init__(self, content: str | None = None, tool_calls: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = None


class _Choice:
    def __init__(self, msg: _Msg) -> None:
        self.message = msg


class _Usage:
    def __init__(self, p: int = 3, c: int = 5) -> None:
        self.prompt_tokens = p
        self.completion_tokens = c


class FakeResp:
    def __init__(self, content: str | None = None, tool_calls: Any = None) -> None:
        self.choices = [_Choice(_Msg(content, tool_calls))]
        self.usage = _Usage()


class _FakeToolCall:
    def __init__(self, name: str, args: str, call_id: str = "tc1") -> None:
        self.id = call_id
        self.function = type("Fn", (), {"name": name, "arguments": args})()


class FakeHistory:
    """最小 ChatHistoryStore：只记录 append 调用。"""

    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    def append(self, session_id: str, msg: dict[str, Any], user_id: int | None = None) -> None:
        self.appended.append({"session_id": session_id, "msg": msg, "user_id": user_id})


# ── fake_chat：按阶段分派 ──────────────────────────────────────────────────

def _make_fake_chat(
    *,
    subqs: str = '{"subquestions": ["子问题一", "子问题二"]}',
    plan_raises: bool = False,
    subagent_content: str | None = "这是发现小结，无引用。",
    reflect: str = '{"sufficient": true, "gap": "", "followups": []}',
    report: str = "这是研究报告正文。",
):
    """构造一个按 system prompt 关键词分派的 fake chat。"""

    def fake_chat(messages, tools=None, temperature=0.7, on_token_chunk=None):
        sys = messages[0]["content"]
        if "研究规划助手" in sys:
            if plan_raises:
                raise RuntimeError("plan llm down")
            return FakeResp(subqs)
        if "研究子代理" in sys:
            return FakeResp(subagent_content)
        if "研究质检助手" in sys:
            return FakeResp(reflect)
        if "研究报告撰写助手" in sys:
            if on_token_chunk:
                on_token_chunk(report)
            return FakeResp(report)
        return FakeResp("")

    return fake_chat


def _types(events: list) -> list[str]:
    return [e.type for e in events]


@pytest.fixture
def reflect_off(monkeypatch):
    monkeypatch.setattr(_cfg, "DEEP_RESEARCH_REFLECT_ENABLED", False)


# ── 规划 ────────────────────────────────────────────────────────────────────

class TestPlan:
    def test_parses_subquestions(self, monkeypatch, reflect_off) -> None:
        monkeypatch.setattr(re_mod, "chat", _make_fake_chat())
        events: list = []
        ResearchEngine(FakeHistory(), user_id=1).run(
            "主问题", session_id="s1", event_callback=events.append,
        )
        plan = next(e for e in events if e.type == "research_plan")
        texts = [q["text"] for q in plan.payload["subquestions"]]
        assert texts == ["子问题一", "子问题二"]

    def test_clamps_to_max(self, monkeypatch, reflect_off) -> None:
        monkeypatch.setattr(_cfg, "DEEP_RESEARCH_MAX_SUBQUESTIONS", 2)
        big = '{"subquestions": ["a", "b", "c", "d", "e"]}'
        monkeypatch.setattr(re_mod, "chat", _make_fake_chat(subqs=big))
        events: list = []
        ResearchEngine(FakeHistory()).run("q", session_id="s", event_callback=events.append)
        plan = next(e for e in events if e.type == "research_plan")
        assert len(plan.payload["subquestions"]) == 2

    def test_plan_failure_degrades_to_single(self, monkeypatch, reflect_off) -> None:
        monkeypatch.setattr(re_mod, "chat", _make_fake_chat(plan_raises=True))
        events: list = []
        ResearchEngine(FakeHistory()).run(
            "原始问题", session_id="s", event_callback=events.append,
        )
        starts = [e for e in events if e.type == "research_subagent_start"]
        assert len(starts) == 1
        assert starts[0].payload["question"] == "原始问题"


# ── happy path ───────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_event_sequence_and_persistence(self, monkeypatch, reflect_off) -> None:
        monkeypatch.setattr(re_mod, "chat", _make_fake_chat())
        history = FakeHistory()
        events: list = []
        report = ResearchEngine(history, user_id=7).run(
            "主问题", session_id="sess-1", event_callback=events.append,
        )

        types = _types(events)
        assert "research_started" in types
        assert "research_plan" in types
        assert types.count("research_subagent_start") == 2
        assert types.count("research_subagent_end") == 2
        assert "research_synthesizing" in types
        assert types[-1] == "final_answer"

        assert "研究报告正文" in report
        # 仅落库两条：用户问题 + 最终报告（中间过程不污染历史）
        assert len(history.appended) == 2
        assert history.appended[0]["msg"]["role"] == "user"
        assert history.appended[1]["msg"]["role"] == "assistant"
        assert history.appended[1]["msg"]["content"] == report
        # final_answer 标注用了工具、未个性化、带 usage
        final = events[-1]
        assert final.payload["used_tools"] is True
        assert final.payload["personalized"] is False
        assert final.payload["usage"] is not None

    def test_all_subagents_ok(self, monkeypatch, reflect_off) -> None:
        monkeypatch.setattr(re_mod, "chat", _make_fake_chat())
        events: list = []
        ResearchEngine(FakeHistory()).run("q", session_id="s", event_callback=events.append)
        ends = [e for e in events if e.type == "research_subagent_end"]
        assert all(e.payload["status"] == "ok" for e in ends)


# ── 子代理软失败 ─────────────────────────────────────────────────────────────

class TestSubagentSoftFailure:
    def test_empty_findings_marked_failed_but_pipeline_completes(
        self, monkeypatch, reflect_off
    ) -> None:
        monkeypatch.setattr(_cfg, "DEEP_RESEARCH_SUBAGENT_MAX_ROUNDS", 2)
        monkeypatch.setattr(
            re_mod, "chat", _make_fake_chat(subagent_content=""),
        )
        events: list = []
        report = ResearchEngine(FakeHistory()).run(
            "q", session_id="s", event_callback=events.append,
        )
        ends = [e for e in events if e.type == "research_subagent_end"]
        assert ends and all(e.payload["status"] == "failed" for e in ends)
        # 子代理全失败，整体仍出报告
        assert "研究报告正文" in report
        assert _types(events)[-1] == "final_answer"


# ── 反思补查 ─────────────────────────────────────────────────────────────────

class TestReflect:
    def test_followups_trigger_extra_subagents(self, monkeypatch) -> None:
        monkeypatch.setattr(_cfg, "DEEP_RESEARCH_REFLECT_ENABLED", True)
        reflect = '{"sufficient": false, "gap": "缺一块", "followups": ["补查问题"]}'
        monkeypatch.setattr(re_mod, "chat", _make_fake_chat(reflect=reflect))
        events: list = []
        ResearchEngine(FakeHistory()).run("q", session_id="s", event_callback=events.append)
        starts = [e for e in events if e.type == "research_subagent_start"]
        # 初始 2 + 补查 1 = 3
        assert len(starts) == 3
        assert any(s.payload["question"] == "补查问题" for s in starts)
        reflect_ev = next(e for e in events if e.type == "research_reflect")
        assert reflect_ev.payload["followups"] == ["补查问题"]

    def test_sufficient_no_followups(self, monkeypatch) -> None:
        monkeypatch.setattr(_cfg, "DEEP_RESEARCH_REFLECT_ENABLED", True)
        monkeypatch.setattr(re_mod, "chat", _make_fake_chat())
        events: list = []
        ResearchEngine(FakeHistory()).run("q", session_id="s", event_callback=events.append)
        starts = [e for e in events if e.type == "research_subagent_start"]
        assert len(starts) == 2  # 无补查


# ── 子代理工具调用路径 ───────────────────────────────────────────────────────

class TestSubagentToolCalls:
    def test_tool_call_then_summary_counts_sources(self, monkeypatch, reflect_off) -> None:
        monkeypatch.setattr(_cfg, "DEEP_RESEARCH_MAX_SUBQUESTIONS", 1)
        monkeypatch.setattr(_cfg, "DEEP_RESEARCH_SUBAGENT_MAX_ROUNDS", 3)

        calls = {"n": 0}

        class _Result:
            status = "ok"

            def to_llm_str(self) -> str:
                return "[1] 网页结果"

        def fake_execute_tool(name, args, **kwargs):
            calls["n"] += 1
            assert kwargs.get("cite_web") is True
            assert kwargs.get("citation_builder") is not None
            return _Result()

        def fake_chat(messages, tools=None, temperature=0.7, on_token_chunk=None):
            sys = messages[0]["content"]
            if "研究规划助手" in sys:
                return FakeResp('{"subquestions": ["唯一子问题"]}')
            if "研究子代理" in sys:
                has_tool_result = any(m.get("role") == "tool" for m in messages)
                if tools is not None and not has_tool_result:
                    return FakeResp(tool_calls=[_FakeToolCall("web_search", '{"query": "x"}')])
                return FakeResp("发现 [1]")
            if "研究报告撰写助手" in sys:
                if on_token_chunk:
                    on_token_chunk("报告")
                return FakeResp("报告")
            return FakeResp("")

        monkeypatch.setattr(re_mod, "chat", fake_chat)
        monkeypatch.setattr(re_mod, "execute_tool", fake_execute_tool)

        events: list = []
        ResearchEngine(FakeHistory()).run("q", session_id="s", event_callback=events.append)

        assert calls["n"] == 1  # execute_tool 被调一次
        end = next(e for e in events if e.type == "research_subagent_end")
        assert end.payload["status"] == "ok"
        assert end.payload["sources"] == 1


# ── get_research_tools ───────────────────────────────────────────────────────

class TestGetResearchTools:
    def test_only_three_retrieval_tools(self) -> None:
        from src.agent.tools import get_research_tools

        names = {t["function"]["name"] for t in get_research_tools()}
        assert names <= {"search_knowledge", "web_search", "fetch_url"}
        # 默认名单门全开时三者都在；至少不应混入 plan / 业务 / skill tool
        assert "update_step" not in names
        assert "load_skill" not in names


# ── _parse_json ──────────────────────────────────────────────────────────────

class TestParseJson:
    def test_plain_json(self) -> None:
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_wrapped_in_text(self) -> None:
        assert _parse_json('解释一下：{"a": 1} 完毕') == {"a": 1}

    def test_array(self) -> None:
        assert _parse_json("[1, 2, 3]") == [1, 2, 3]

    def test_empty_or_garbage(self) -> None:
        assert _parse_json("") == {}
        assert _parse_json("完全不是 json") == {}
