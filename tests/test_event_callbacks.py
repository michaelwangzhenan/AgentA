"""
测试：事件回调契约 —— `set_thinking_callback` / `set_token_callback` / `_on_thinking_chunk`

把当前 `Agent` 的事件流注册接口固化为"契约 UT"。

§4.5 重构后，将抽出 `EventBus` helper 统一三种 Agent 实现的事件流；届时本文件
重命名为 `test_event_bus.py`，把测试主语从 `agent` 改为 `agent.events`（EventBus 实例），
其余断言（默认 None / 替换语义 / 透传顺序 / 异常分支）保持不变即可。

覆盖：
- 默认值：thinking / token callback 初始为 None；`on_thinking_chunk` ctor 参数可预置 thinking callback
- `set_thinking_callback(fn)` / `set_thinking_callback(None)` 安装/重置
- `set_token_callback(fn)` / `set_token_callback(None)` 安装/重置
- 替换语义：连续 set，last-set wins
- `_on_thinking_chunk` 在 callback 存在时透传 chunk 并置位 `_thinking_started`
- `_on_thinking_chunk` 在 callback 不存在时走 CLI stdout 分支（不报错）
- 多 chunk 顺序保持

已落地的契约：
- 单订阅者抛异常时，事件分发吞掉异常并降级到 stdout（不影响 Agent 主流程）

EventBus 重构时还需新增的契约（本期 placeholder，标 xfail）：
- 多订阅者扇出（需把 callback 字段从 callable|None 改为 list[callable]）
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.agent import Agent


def _mk_agent(on_thinking_chunk=None) -> Agent:
    """构造最小 Agent，绕开 SQLite / user_memory。"""
    mock_history = MagicMock()
    mock_history.load_last_n_messages.return_value = []
    return Agent(
        verbose=False,
        chat_history=mock_history,
        user_memory=None,
        on_thinking_chunk=on_thinking_chunk,
    )


# ── 默认状态 ─────────────────────────────────────────────────────────────────

class TestDefaultCallbackState:

    def test_token_callback_defaults_to_none(self) -> None:
        agent = _mk_agent()
        assert agent._token_chunk_callback is None

    def test_thinking_callback_defaults_to_none(self) -> None:
        agent = _mk_agent()
        assert agent._thinking_chunk_callback is None

    def test_ctor_on_thinking_chunk_preinstalled(self) -> None:
        """ctor 的 on_thinking_chunk 参数应作为初始 thinking callback。"""
        fn = MagicMock()
        agent = _mk_agent(on_thinking_chunk=fn)
        assert agent._thinking_chunk_callback is fn


# ── set_thinking_callback / set_token_callback 安装与替换 ──────────────────

class TestCallbackInstallation:

    def test_set_thinking_callback_installs_fn(self) -> None:
        agent = _mk_agent()
        fn = lambda chunk: None
        agent.set_thinking_callback(fn)
        assert agent._thinking_chunk_callback is fn

    def test_set_thinking_callback_none_resets(self) -> None:
        agent = _mk_agent(on_thinking_chunk=lambda c: None)
        agent.set_thinking_callback(None)
        assert agent._thinking_chunk_callback is None

    def test_set_token_callback_installs_fn(self) -> None:
        agent = _mk_agent()
        fn = lambda chunk: None
        agent.set_token_callback(fn)
        assert agent._token_chunk_callback is fn

    def test_set_token_callback_none_resets(self) -> None:
        agent = _mk_agent()
        agent.set_token_callback(lambda c: None)
        agent.set_token_callback(None)
        assert agent._token_chunk_callback is None

    def test_replacement_semantics_last_wins(self) -> None:
        """连续 set，最后一个生效（覆盖语义；多订阅者需待 EventBus 重构）。"""
        agent = _mk_agent()
        fn1 = lambda c: None
        fn2 = lambda c: None
        agent.set_thinking_callback(fn1)
        agent.set_thinking_callback(fn2)
        assert agent._thinking_chunk_callback is fn2
        # token 同理
        agent.set_token_callback(fn1)
        agent.set_token_callback(fn2)
        assert agent._token_chunk_callback is fn2


# ── _on_thinking_chunk 透传与状态置位 ───────────────────────────────────────

class TestOnThinkingChunkDispatch:

    def test_chunk_passed_to_installed_callback(self) -> None:
        captured: list[str] = []
        agent = _mk_agent(on_thinking_chunk=captured.append)
        agent._on_thinking_chunk("hello ")
        agent._on_thinking_chunk("world")
        assert captured == ["hello ", "world"]
        assert agent._thinking_started is True

    def test_no_callback_falls_back_to_stdout(self, capsys) -> None:
        """无 callback 时走 CLI stdout 分支：首 chunk 打印头部 + chunk 内容本身。"""
        agent = _mk_agent()  # 无 thinking callback → CLI 模式
        agent._on_thinking_chunk("第一段")
        agent._on_thinking_chunk("第二段")
        out = capsys.readouterr().out
        # 头部应只打印一次
        assert out.count("思考中") == 1
        assert "第一段" in out
        assert "第二段" in out
        assert agent._thinking_started is True

    def test_callback_replacement_after_first_chunk(self) -> None:
        """运行中替换 callback：替换后新 chunk 走新 callback。"""
        captured_a: list[str] = []
        captured_b: list[str] = []
        agent = _mk_agent(on_thinking_chunk=captured_a.append)
        agent._on_thinking_chunk("to_A")
        agent.set_thinking_callback(captured_b.append)
        agent._on_thinking_chunk("to_B")
        assert captured_a == ["to_A"]
        assert captured_b == ["to_B"]


# ── 订阅者异常隔离（已落地） ───────────────────────────────────────────────

class TestSubscriberExceptionIsolation:
    """订阅者抛异常应被吞掉并降级到 stdout，不影响 Agent 主流程。"""

    def test_subscriber_exception_isolated(self, capsys) -> None:
        agent = _mk_agent()

        def bad(_chunk: str) -> None:
            raise RuntimeError("订阅者炸了")

        agent.set_thinking_callback(bad)
        # 不应抛 RuntimeError
        agent._on_thinking_chunk("降级到 stdout 的内容")
        out = capsys.readouterr().out
        # 异常分支降级到 stdout，原始 chunk 仍应被输出
        assert "降级到 stdout 的内容" in out


# ── EventBus 重构后必加的多订阅扇出契约（当前 placeholder） ───────────────

class TestFutureEventBusContract:
    """
    多订阅者扇出在当前单 callback 模式下不成立 —— callback 字段是 `callable | None`，
    `set_thinking_callback` 是覆盖语义，不是追加。
    用 xfail(strict=True) 锁定：§4.5 把字段改为 `list[callable]` + 加 subscribe/unsubscribe
    后，此用例会立即变绿。
    """

    @pytest.mark.xfail(strict=True, reason="多订阅者扇出待 EventBus helper 抽出后实现（需把 callback 改为 list）")
    def test_multiple_subscribers_fan_out(self) -> None:
        agent = _mk_agent()
        sub1: list[str] = []
        sub2: list[str] = []
        agent.set_thinking_callback(sub1.append)
        agent.set_thinking_callback(sub2.append)  # 当前是覆盖，未来应是追加
        agent._on_thinking_chunk("x")
        assert sub1 == ["x"]
        assert sub2 == ["x"]
