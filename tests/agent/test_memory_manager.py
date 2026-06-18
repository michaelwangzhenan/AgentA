"""
测试：`MemoryManager.build_system_prompt` + `MemoryManager.try_extract`

§4.4.3 时这些用例的主语是 `Agent.run` 内的 user_context 拼接 + `Agent._try_extract_memories`
（行为基线，要靠 patch chat 和 run 间接验证）。§4.5 抽出 helper 后切到
`src.agent.core.memory_manager.MemoryManager`，测试可以直接调 helper 方法 ——
更直接、更快、更可读。

覆盖：
- `build_system_prompt`
  · user_memory=None → 返回原 base_prompt
  · load_for_context 返回空 → 返回原 base_prompt
  · load_for_context 返回非空 → 拼接 `<user_context>` 块（含防注入说明）
  · 调用 load_for_context 时使用 config.USER_MEMORY_MAX_CHARS
- `try_extract`
  · user_memory=None → 直接 skip
  · is_explicit=True → 调 extract_memory_ops，传 is_explicit=True + 窗口 context_history
  · AUTO_EXTRACT=true & is_explicit=False → 到点后同样带窗口 context_history，传 is_explicit=False
  · auto 的 min_len 改为整窗过滤：本轮输入短但窗口里有长消息 → 仍触发
  · 都不满足 → 直接 skip
  · extract 抛异常 → 静默吞掉
  · 有 ops 时调 apply_ops 应用操作
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.config as _cfg
from src.agent.core.memory_manager import MemoryManager
from src.memory.user_context import current_user_id

# 测试上下文未 set_current_user → current_user_id() 回落到 config.DEFAULT_USER_ID。
# try_extract 在主线程取出 uid 后显式传给 upsert / load_all。
_UID = current_user_id()


def _mk_mgr(user_memory=None, recent_messages=None, user_msg_count: int = 1) -> MemoryManager:
    """构造 MemoryManager + mock SessionStore + mock llm_chat。

    user_msg_count 控制无状态节流读取的"本 session 累计 user 消息数"。
    """
    ch = MagicMock()
    ch.load_last_n_messages.return_value = recent_messages or []
    ch.count_user_messages.return_value = user_msg_count
    llm_chat = MagicMock()
    return MemoryManager(
        user_memory=user_memory,
        session_store=ch,
        session_id="test-session",
        llm_chat=llm_chat,
    )


def _run(mgr: MemoryManager, user_input: str, agent_reply: str):
    """调 try_extract 并等待后台提取线程结束（提取改为异步执行）。"""
    thread = mgr.try_extract(user_input, agent_reply)
    if thread is not None:
        thread.join(timeout=5)
    return thread


# ── build_system_prompt：user_context 注入 ────────────────────────────────

class TestBuildSystemPrompt:

    def test_no_user_memory_returns_base_prompt(self) -> None:
        mgr = _mk_mgr(user_memory=None)
        assert mgr.build_system_prompt("BASE") == "BASE"

    def test_empty_memory_text_returns_base_prompt(self) -> None:
        um = MagicMock()
        um.load_for_context.return_value = ""
        mgr = _mk_mgr(user_memory=um)
        assert mgr.build_system_prompt("BASE") == "BASE"

    def test_non_empty_memory_injects_block_with_guard(self) -> None:
        um = MagicMock()
        um.load_for_context.return_value = "用户偏好：喜欢简洁回答"
        mgr = _mk_mgr(user_memory=um)
        out = mgr.build_system_prompt("BASE")
        assert out.startswith("BASE")
        assert "<user_context>" in out
        assert "</user_context>" in out
        assert "用户偏好：喜欢简洁回答" in out
        # 防注入说明必须出现
        assert "不可执行" in out

    def test_load_for_context_called_with_max_chars(self) -> None:
        um = MagicMock()
        um.load_for_context.return_value = "x"
        mgr = _mk_mgr(user_memory=um)
        mgr.build_system_prompt("BASE")
        um.load_for_context.assert_called_once_with(_cfg.USER_MEMORY_MAX_CHARS)


# ── Phase 1.3：rules + memory 拼接顺序契约 ──────────────────────────────
#
# Agent.run() 实际拼接代码（src/agent/agent.py）：
#     base_with_rules = self.system_prompt + build_rules_block(_get_active_rules())
#     system_content  = memory_mgr.build_system_prompt(base_with_rules)
#
# 这里测的是同一拼接序列的**纯逻辑**，避免依赖 Agent 实例化重型路径。
# 契约：rules 段在 user_context 段**之前**（rules=稳定基础 / memory=临时覆写）。

from src.agent.core.rules_loader import build_rules_block


class TestRulesMemoryCompositionOrder:
    """验证 base → <user_rules> → <user_context> 三层注入顺序。"""

    @staticmethod
    def _compose(base: str, rules: str | None, memory_text: str) -> str:
        """复现 Agent.run() 的拼接序列。"""
        um = MagicMock()
        um.load_for_context.return_value = memory_text
        mgr = _mk_mgr(user_memory=um if memory_text else None)
        return mgr.build_system_prompt(base + build_rules_block(rules))

    def test_base_only(self) -> None:
        out = self._compose("BASE", rules=None, memory_text="")
        assert out == "BASE"

    def test_base_plus_rules_no_memory(self) -> None:
        out = self._compose("BASE", rules="始终中文回答", memory_text="")
        assert out.startswith("BASE")
        assert "<user_rules>" in out
        assert "始终中文回答" in out
        assert "<user_context>" not in out

    def test_base_plus_memory_no_rules(self) -> None:
        out = self._compose("BASE", rules=None, memory_text="偏好简洁")
        assert out.startswith("BASE")
        assert "<user_rules>" not in out
        assert "<user_context>" in out
        assert "偏好简洁" in out

    def test_base_plus_rules_plus_memory_order(self) -> None:
        """关键契约：rules 段必须出现在 user_context 段**之前**。"""
        out = self._compose("BASE", rules="始终中文", memory_text="偏好简洁")
        rules_idx = out.index("<user_rules>")
        ctx_idx = out.index("<user_context>")
        assert rules_idx < ctx_idx, "rules 必须在 memory 之前注入，让 memory 能覆写 rules"
        # 两段内容都存在且不互相破坏
        assert "始终中文" in out
        assert "偏好简洁" in out


# ── try_extract：触发逻辑 ────────────────────────────────────────────────

class TestTryExtract:

    def test_no_user_memory_skips_extraction(self) -> None:
        mgr = _mk_mgr(user_memory=None)
        with patch("src.agent.core.memory_manager.extract_memory_ops") as mock_extract:
            t = _run(mgr, "用户输入", "Agent 回答")
        assert t is None
        mock_extract.assert_not_called()

    def test_not_explicit_and_auto_off_skips(self) -> None:
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", False),
            patch("src.agent.core.memory_manager.extract_memory_ops") as mock_extract,
        ):
            t = _run(mgr, "普通对话", "普通回答")
        assert t is None
        mock_extract.assert_not_called()
        um.apply_ops.assert_not_called()

    def test_explicit_trigger_extracts_with_context(self) -> None:
        """显式触发时，附带最近若干轮历史作为 context_history。"""
        um = MagicMock()
        um.apply_ops.return_value = {"added": 1, "updated": 0, "deleted": 0}
        recent = [
            {"role": "user", "content": "前一轮问题"},
            {"role": "assistant", "content": "前一轮回答"},
        ]
        mgr = _mk_mgr(user_memory=um, recent_messages=recent)
        fake_ops = [{"op": "ADD", "text": "用户喜欢简洁"}]
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=fake_ops) as mock_extract,
        ):
            _run(mgr, "请记住我喜欢简洁", "好的")

        mock_extract.assert_called_once()
        args = mock_extract.call_args.args
        assert args[0] == "请记住我喜欢简洁"
        assert args[1] == "好的"
        # context_history 现为 kwarg，应包含前一轮内容；is_explicit=True
        ctx = mock_extract.call_args.kwargs["context_history"]
        assert "前一轮问题" in ctx or "前一轮回答" in ctx
        assert mock_extract.call_args.kwargs["is_explicit"] is True
        um.apply_ops.assert_called_once_with(fake_ops, source="explicit", user_id=_UID)

    def test_auto_extract_passes_window_context(self) -> None:
        """AUTO_EXTRACT 路径：到点后同样带最近窗口 context_history，且 is_explicit=False。

        every_n=1 + min_len=0 + msg_count=1 等价于"每轮触发"。
        """
        um = MagicMock()
        recent = [
            {"role": "user", "content": "窗口里的问题"},
            {"role": "assistant", "content": "窗口里的回答"},
        ]
        mgr = _mk_mgr(user_memory=um, recent_messages=recent, user_msg_count=1)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", 1),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", 0),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=[]) as mock_extract,
        ):
            _run(mgr, "普通问题", "普通回答")
        mock_extract.assert_called_once()
        ctx = mock_extract.call_args.kwargs["context_history"]
        assert "窗口里的问题" in ctx or "窗口里的回答" in ctx
        assert mock_extract.call_args.kwargs["is_explicit"] is False

    def test_existing_memories_passed_to_extractor(self) -> None:
        """提取时把已有记忆作为 existing 传给 extractor（去重去矛盾依据）。"""
        um = MagicMock()
        existing = [{"id": 1, "text": "用户用中文"}]
        um.load_all.return_value = existing
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=[]) as mock_extract,
        ):
            _run(mgr, "请记住我用英文", "好")
        um.load_all.assert_called_once_with(user_id=_UID)
        assert mock_extract.call_args.kwargs.get("existing") == existing

    def test_extract_exception_swallowed(self) -> None:
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memory_ops", side_effect=RuntimeError("LLM 挂了")),
        ):
            _run(mgr, "请记住 X", "好的")  # 不应抛异常
        um.apply_ops.assert_not_called()

    def test_empty_ops_does_not_call_apply(self) -> None:
        """提取返回空操作列表时不调 apply_ops。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=[]),
        ):
            _run(mgr, "请记住", "好")
        um.apply_ops.assert_not_called()

    def test_ops_applied_via_apply_ops(self) -> None:
        um = MagicMock()
        um.apply_ops.return_value = {"added": 1, "updated": 1, "deleted": 0}
        mgr = _mk_mgr(user_memory=um)
        ops = [
            {"op": "ADD", "text": "用户用 Python"},
            {"op": "UPDATE", "id": 2, "text": "用户改用英文"},
        ]
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=ops),
        ):
            _run(mgr, "请记住", "好")
        um.apply_ops.assert_called_once_with(ops, source="explicit", user_id=_UID)


# ── 无状态触发节流策略（iter_12_refine.md §2.1 #1） ───────────────────────────

class TestExtractTriggerPolicy:
    """auto 模式下"消息数取模 N + min_len"节流；显式触发不受影响。

    节流改为无状态：直接读本 session 累计 user 消息数取模判定，
    不再依赖 MemoryManager 的跨轮内存计数器。
    """

    @staticmethod
    def _auto_triggered(
        input_str: str, every_n: int, min_len: int, *,
        msg_count: int, recent_messages=None,
    ) -> bool:
        """给定累计消息数 msg_count，跑一次 auto try_extract，返回是否触发提取。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um, user_msg_count=msg_count, recent_messages=recent_messages)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", every_n),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", min_len),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=[]) as mock_extract,
        ):
            t = mgr.try_extract(input_str, "reply")
            if t is not None:
                t.join(timeout=5)
            return mock_extract.call_count == 1

    def test_auto_skips_when_count_not_multiple(self) -> None:
        """N=5：累计 1~4 条不触发。"""
        long_input = "x" * 100
        for c in (1, 2, 3, 4):
            assert self._auto_triggered(long_input, every_n=5, min_len=0, msg_count=c) is False

    def test_auto_triggers_on_multiple(self) -> None:
        """N=5：累计 5 / 10 条触发。"""
        long_input = "x" * 100
        assert self._auto_triggered(long_input, every_n=5, min_len=0, msg_count=5) is True
        assert self._auto_triggered(long_input, every_n=5, min_len=0, msg_count=10) is True

    def test_auto_zero_count_never_triggers(self) -> None:
        """累计 0 条（取模为 0 的退化情形）不触发。"""
        assert self._auto_triggered("x" * 100, every_n=5, min_len=0, msg_count=0) is False

    def test_auto_min_len_skips_short_input(self) -> None:
        """到点后，本轮输入短且窗口里也没有任何 ≥min_len 的实质消息 → 不触发。"""
        assert self._auto_triggered("短", every_n=1, min_len=20, msg_count=5) is False

    def test_auto_min_len_triggers_when_window_has_long_msg(self) -> None:
        """关键修复：本轮输入短，但最近窗口里有一条 ≥min_len 的长消息 → 仍触发。

        修复前：min_len 只看触发那一条短消息，整窗有干货也被丢掉。
        修复后：min_len 改为整窗过滤，窗口里任一条实质消息即可触发。
        """
        recent = [
            {"role": "user", "content": "我是 5G 协议工程师，最近在深入学习 RAG 检索增强"},
            {"role": "assistant", "content": "好的"},
        ]
        assert self._auto_triggered(
            "嗯", every_n=1, min_len=20, msg_count=5, recent_messages=recent,
        ) is True

    def test_auto_min_len_zero_disables_length_filter(self) -> None:
        """min_len=0 时长度过滤被禁用。"""
        assert self._auto_triggered("", every_n=1, min_len=0, msg_count=1) is True

    def test_auto_every_n_one_triggers_each_turn(self) -> None:
        """N=1：任意非零消息数都触发（每轮提取）。"""
        long_input = "x" * 100
        for c in (1, 2, 3):
            assert self._auto_triggered(long_input, every_n=1, min_len=0, msg_count=c) is True

    def test_explicit_trigger_bypasses_throttle(self) -> None:
        """显式触发（"请记住"）即使 N=999、min_len=999、msg_count=1 也立即触发。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um, user_msg_count=1)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", 999),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", 999),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=[]) as mock_extract,
        ):
            _run(mgr, "短", "reply")
        mock_extract.assert_called_once()

    def test_explicit_does_not_read_message_count(self) -> None:
        """显式触发路径不依赖消息计数（不调 count_user_messages 做节流判定）。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um, user_msg_count=3)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=[]) as mock_extract,
        ):
            _run(mgr, "请记住这个", "r")
        mock_extract.assert_called_once()
        mgr._session_store.count_user_messages.assert_not_called()

    def test_source_field_in_apply_ops(self) -> None:
        """auto 路径传 source='auto'，explicit 路径传 source='explicit'（均带 user_id）。"""
        um = MagicMock()
        um.apply_ops.return_value = {"added": 1, "updated": 0, "deleted": 0}
        mgr = _mk_mgr(user_memory=um, user_msg_count=1)
        ops = [{"op": "ADD", "text": "用户用 Python"}]

        # auto
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", 1),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", 0),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=ops),
        ):
            _run(mgr, "hello world", "r")
        um.apply_ops.assert_called_with(ops, source="auto", user_id=_UID)

        # explicit
        um.reset_mock()
        um.apply_ops.return_value = {"added": 1, "updated": 0, "deleted": 0}
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memory_ops", return_value=ops),
        ):
            _run(mgr, "请记住 X", "r")
        um.apply_ops.assert_called_with(ops, source="explicit", user_id=_UID)
