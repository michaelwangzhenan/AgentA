"""
测试：CLI `run_query` 中的 thinking 渲染状态机（Phase 3.1）

承接 [`docs/iter_2_agent.md §4.9.11`](../docs/iter_2_agent.md#4911-thinking-cli-渲染-phase-31)
6 条验收标准，覆盖：

- 验收 ① 段起止可识别（header / footer）
- 验收 ② 流式分块 + `│ ` 行前缀（跨行 / 多 chunk 单行）
- 验收 ③ thinking 不与正文混（最终 reply 不含渲染 artifact）
- 验收 ④ 多轮独立 + 带轮次编号（首轮无编号，第 N≥2 轮带 `（第 N 轮）`）
- 验收 ⑤ 关 thinking 零 artifact（不发 thinking 事件 = CLI 静默）
- 验收 ⑥ 不阻塞主流程（state 机异常 → run_query 仍正常返回）

测试用 `_FakeAgent` 替代真实 Agent：精简实现 `set_event_callback` + `run()` 按预定
脚本依次 publish 事件，避开 SQLite / user_memory / LLM provider 依赖。
"""
from __future__ import annotations

from typing import Any

import pytest

from src.agent.core.event_bus import ALL_EVENT_TYPES, AgentEvent, EventBus
from src.cli import handlers


class _FakeAgent:
    """模拟最小 AgentAPI：用真实 EventBus 走异常隔离链路。

    `set_event_callback(cb)` 仿 Agent.set_event_callback：清空再为 ALL_EVENT_TYPES
    各注册一个 wrapper handler，wrapper 把 payload 与 event_type 包装成 AgentEvent
    转发给 cb；run() 按 script 用 events.publish() 派发事件。
    """

    def __init__(self, script: list[tuple[str, dict[str, Any]]], reply: str = "") -> None:
        self._script = script
        self._reply = reply
        self.events = EventBus()
        self.last_usage = None

    def set_event_callback(self, callback) -> None:  # noqa: D401
        self.events.clear()
        if callback is None:
            return
        for evt_type in ALL_EVENT_TYPES:
            def _wrapper(payload: Any, _t: str = evt_type) -> None:
                callback(AgentEvent(type=_t, payload=payload))
            self.events.subscribe(evt_type, _wrapper)

    def run(self, question: str) -> str:  # noqa: ARG002
        for evt_type, payload in self._script:
            self.events.publish(AgentEvent(type=evt_type, payload=payload))
        return self._reply


# ── 验收 ① 段起止可识别 / 验收 ⑤ 关 thinking 零 artifact ────────────────────────


class TestSingleRoundMarkers:
    def test_header_footer_around_single_thinking_segment(self, capsys) -> None:
        script = [
            ("thinking_chunk", {"text": "推理中"}),
            ("token_chunk", {"text": "答案"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="答案"), "问题")
        out = capsys.readouterr().out
        assert "思考中..." in out
        # 单轮 footer 不带编号
        assert "─── 思考结束 ───" in out
        assert "（第 1 轮）" not in out
        # 顺序：header → thinking 内容 → footer → 正文
        assert out.index("思考中...") < out.index("推理中") < out.index("─── 思考结束 ───") < out.index("答案")

    def test_no_thinking_event_no_artifact(self, capsys) -> None:
        """关 thinking 时不发 thinking_chunk → CLI 看不到任何 thinking 痕迹。"""
        script = [("token_chunk", {"text": "正常答案"})]
        handlers.run_query(_FakeAgent(script, reply="正常答案"), "问题")
        out = capsys.readouterr().out
        assert "思考中" not in out
        assert "思考中" not in out
        assert "思考结束" not in out
        assert "│" not in out
        assert "正常答案" in out


# ── 验收 ② 流式分块 + 行前缀 ─────────────────────────────────────────────────


class TestLinePrefix:
    def test_line_prefix_injected_on_each_line(self, capsys) -> None:
        """thinking 文本含 `\\n` → 换行后再写正常字符时插 `│ ` 前缀。"""
        script = [
            ("thinking_chunk", {"text": "第一行\n第二行\n第三行"}),
            ("token_chunk", {"text": "x"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="x"), "问题")
        out = capsys.readouterr().out
        assert "│ 第一行" in out
        assert "│ 第二行" in out
        assert "│ 第三行" in out

    def test_line_prefix_across_chunk_boundary(self, capsys) -> None:
        """chunk 跨行（一段不带 \\n、下一段以非 \\n 开头）：仅在真换行后注入前缀，不重复。"""
        script = [
            ("thinking_chunk", {"text": "abc"}),
            ("thinking_chunk", {"text": "def\n"}),
            ("thinking_chunk", {"text": "ghi"}),
            ("token_chunk", {"text": "x"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="x"), "问题")
        out = capsys.readouterr().out
        # 第一行：abcdef（前缀只在行首打一次，跨 chunk 不重复）
        assert "│ abcdef" in out
        assert "│ ghi" in out
        # 不应出现 │ a│ b 这种重复前缀
        assert "│ a│" not in out

    def test_empty_chunk_does_not_open_segment(self, capsys) -> None:
        """空 chunk 不应触发 header（避免空段 artifact）。"""
        script = [
            ("thinking_chunk", {"text": ""}),
            ("token_chunk", {"text": "x"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="x"), "问题")
        out = capsys.readouterr().out
        assert "思考中" not in out
        assert "思考结束" not in out


# ── 验收 ③ thinking 不与正文混 ────────────────────────────────────────────────


class TestThinkingIsolatedFromAnswer:
    def test_thinking_text_not_in_reply_render(self, capsys) -> None:
        """正文 reply 通过 token_chunk 流式 + run() 返回，最终 stdout 应严格分段。"""
        script = [
            ("thinking_chunk", {"text": "内部推理"}),
            ("token_chunk", {"text": "公开答案"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="公开答案"), "问题")
        out = capsys.readouterr().out
        # footer 必须在 token 之前打出
        assert out.index("─── 思考结束 ───") < out.index("公开答案")
        # 正文打印不含思考前缀
        agent_section = out[out.index("Agent:"):]
        assert "│" not in agent_section
        assert "内部推理" not in agent_section


# ── 验收 ④ 多轮独立 + 带轮次编号 ─────────────────────────────────────────────


class TestMultiRoundNumbering:
    def test_first_round_no_label_second_round_labeled(self, capsys) -> None:
        """首轮 thinking header / footer 不带编号；第 2 轮带 `（第 2 轮）`。"""
        script = [
            ("thinking_chunk", {"text": "第一轮思考"}),
            ("tool_call_start", {"name": "search_knowledge", "args": {}, "call_id": "c1"}),
            ("thinking_chunk", {"text": "第二轮思考"}),
            ("token_chunk", {"text": "最终答"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="最终答"), "问题")
        out = capsys.readouterr().out

        # 首轮：不带编号
        assert "思考中..." in out
        assert "─── 思考结束 ───" in out
        # 第 2 轮：带编号
        assert "思考中（第 2 轮）..." in out
        assert "─── 第 2 轮思考结束 ───" in out
        # 顺序：首轮 header → 首轮 footer → 第 2 轮 header → 第 2 轮 footer → 答案
        order = [
            "思考中...",
            "─── 思考结束 ───",
            "思考中（第 2 轮）...",
            "─── 第 2 轮思考结束 ───",
            "最终答",
        ]
        positions = [out.index(s) for s in order]
        assert positions == sorted(positions)

    def test_three_rounds_labels_2_and_3(self, capsys) -> None:
        script = [
            ("thinking_chunk", {"text": "r1"}),
            ("token_chunk", {"text": "a1"}),
            ("thinking_chunk", {"text": "r2"}),
            ("token_chunk", {"text": "a2"}),
            ("thinking_chunk", {"text": "r3"}),
            ("token_chunk", {"text": "a3"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="a3"), "问题")
        out = capsys.readouterr().out
        assert "思考中..." in out  # 首轮
        assert "思考中（第 2 轮）..." in out
        assert "思考中（第 3 轮）..." in out
        assert "─── 第 3 轮思考结束 ───" in out


# ── 段切换：plan / token / tool_call_start 触发 thinking footer ───────────────


class TestSegmentBreakEvents:
    def test_plan_created_closes_thinking(self, capsys) -> None:
        script = [
            ("thinking_chunk", {"text": "想要做计划"}),
            ("plan_created", {"steps": [{"id": 1, "text": "查 KB"}, {"id": 2, "text": "汇总"}]}),
            ("token_chunk", {"text": "完成"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="完成"), "问题")
        out = capsys.readouterr().out
        assert out.index("─── 思考结束 ───") < out.index("Plan：")

    def test_plan_step_end_closes_thinking(self, capsys) -> None:
        script = [
            ("thinking_chunk", {"text": "执行中"}),
            ("plan_step_end", {"step_id": 1, "status": "success", "note": ""}),
            ("token_chunk", {"text": "ok"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="ok"), "问题")
        out = capsys.readouterr().out
        # footer 在 [完成] 之前
        assert out.index("─── 思考结束 ───") < out.index("[完成]")

    def test_tool_call_start_closes_thinking_silently(self, capsys) -> None:
        """tool_call_start 关闭 thinking，但事件本身 CLI 不渲染（无图标 / 文本）。"""
        script = [
            ("thinking_chunk", {"text": "决定调工具"}),
            ("tool_call_start", {"name": "search_knowledge", "args": {}, "call_id": "c1"}),
            ("token_chunk", {"text": "查到结果"}),
        ]
        handlers.run_query(_FakeAgent(script, reply="查到结果"), "问题")
        out = capsys.readouterr().out
        # footer 出现，但 tool 事件本身无 CLI 渲染
        assert "─── 思考结束 ───" in out
        assert "search_knowledge" not in out
        assert "tool_call_start" not in out


# ── run() 结束兜底：仅 thinking 无 token / 异常 / 中断 三种边界 ────────────────


class TestFinallyClose:
    def test_thinking_only_run_closes_at_end(self, capsys) -> None:
        """LLM 仅产 thinking 无 final answer 时（理论边界），run_query finally 兜底关 footer。"""
        script = [("thinking_chunk", {"text": "卡住了"})]
        handlers.run_query(_FakeAgent(script, reply=""), "问题")
        out = capsys.readouterr().out
        assert "思考中..." in out
        assert "─── 思考结束 ───" in out

    def test_exception_during_run_still_closes_thinking(self, capsys) -> None:
        """run() 抛异常时 finally 兜底关 footer，错误信息正常显示。"""

        class _ExplodingAgent(_FakeAgent):
            def run(self, question: str) -> str:  # noqa: ARG002
                self.events.publish(AgentEvent(type="thinking_chunk", payload={"text": "出错前"}))
                raise RuntimeError("LLM 挂了")

        handlers.run_query(_ExplodingAgent([], reply=""), "问题")
        out = capsys.readouterr().out
        assert "思考中..." in out
        assert "─── 思考结束 ───" in out
        assert "出错了" in out


# ── 验收 ⑥ 不阻塞主流程 ──────────────────────────────────────────────────────


class TestExceptionIsolation:
    def test_router_exception_isolated_by_eventbus(self) -> None:
        """thinking 渲染抛异常时由 EventBus 吞掉（_event_router 注册路径），不传播到 agent.run。

        路径：handlers.run_query → set_event_callback → EventBus.subscribe → publish 时
        try/except 隔离单订阅者抛异常（详 test_event_bus.py::TestEventBusExceptionIsolation）。
        本 case 仅 sanity-check CLI 层确实走 EventBus 订阅路径而非直连 callback。
        """

        class _BombAgent(_FakeAgent):
            def run(self, question: str) -> str:  # noqa: ARG002
                # 第 1 次 publish 时模拟订阅者抛错（EventBus 吞）；
                # 第 2 次 publish 仍能被订阅者收到 → run_query 不挂
                self.events.publish(AgentEvent(type="thinking_chunk", payload={"text": "x"}))
                self.events.publish(AgentEvent(type="token_chunk", payload={"text": "正常"}))
                return "正常"

        agent = _BombAgent([], reply="正常")
        # 主动注入一个抛异常的订阅者，模拟 router 渲染失败的边界
        agent.events.subscribe("thinking_chunk", lambda _p: (_ for _ in ()).throw(RuntimeError("renderer 炸了")))
        # 不应抛
        handlers.run_query(agent, "问题")
