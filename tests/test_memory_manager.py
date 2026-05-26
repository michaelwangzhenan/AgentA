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
  · is_explicit=True → 调 extract_memories，传非空 context_history
  · AUTO_EXTRACT=true & is_explicit=False → 调 extract，但 context_history=""
  · 都不满足 → 直接 skip
  · extract 抛异常 → 静默吞掉
  · 多 entries 全部 upsert
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.config as _cfg
from src.agent.core.memory_manager import MemoryManager


def _mk_mgr(user_memory=None, recent_messages=None) -> MemoryManager:
    """构造 MemoryManager + mock ChatHistoryStore + mock llm_chat。"""
    ch = MagicMock()
    ch.load_last_n_messages.return_value = recent_messages or []
    llm_chat = MagicMock()
    return MemoryManager(
        user_memory=user_memory,
        chat_history=ch,
        session_id="test-session",
        llm_chat=llm_chat,
    )


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


# ── try_extract：触发逻辑 ────────────────────────────────────────────────

class TestTryExtract:

    def test_no_user_memory_skips_extraction(self) -> None:
        mgr = _mk_mgr(user_memory=None)
        with patch("src.agent.core.memory_manager.extract_memories") as mock_extract:
            mgr.try_extract("用户输入", "Agent 回答")
        mock_extract.assert_not_called()

    def test_not_explicit_and_auto_off_skips(self) -> None:
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", False),
            patch("src.agent.core.memory_manager.extract_memories") as mock_extract,
        ):
            mgr.try_extract("普通对话", "普通回答")
        mock_extract.assert_not_called()
        um.upsert.assert_not_called()

    def test_explicit_trigger_extracts_with_context(self) -> None:
        """显式触发时，附带最近若干轮历史作为 context_history。"""
        um = MagicMock()
        recent = [
            {"role": "user", "content": "前一轮问题"},
            {"role": "assistant", "content": "前一轮回答"},
        ]
        mgr = _mk_mgr(user_memory=um, recent_messages=recent)
        fake_entries = [{"category": "preference", "key": "k", "value": "v"}]
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memories", return_value=fake_entries) as mock_extract,
        ):
            mgr.try_extract("请记住我喜欢简洁", "好的")

        mock_extract.assert_called_once()
        args = mock_extract.call_args.args
        assert args[0] == "请记住我喜欢简洁"
        assert args[1] == "好的"
        # 第 4 个参数是 extract_context，应包含前一轮内容
        assert "前一轮问题" in args[3] or "前一轮回答" in args[3]
        um.upsert.assert_called_once_with("preference", "k", "v", source="explicit")

    def test_auto_extract_passes_empty_context(self) -> None:
        """AUTO_EXTRACT 路径：context_history 必须为 ""（用严格 prompt）。

        Phase 1.2 节流（iter_2.md §4.9.2）：every_n=1 + min_len=0 等价于旧的"每轮触发"行为。
        """
        um = MagicMock()
        recent = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]
        mgr = _mk_mgr(user_memory=um, recent_messages=recent)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", 1),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", 0),
            patch("src.agent.core.memory_manager.extract_memories", return_value=[]) as mock_extract,
        ):
            mgr.try_extract("普通问题", "普通回答")
        mock_extract.assert_called_once()
        assert mock_extract.call_args.args[3] == ""

    def test_extract_exception_swallowed(self) -> None:
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memories", side_effect=RuntimeError("LLM 挂了")),
        ):
            mgr.try_extract("请记住 X", "好的")  # 不应抛异常
        um.upsert.assert_not_called()

    def test_multiple_entries_all_upserted(self) -> None:
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        entries = [
            {"category": "preference", "key": "a", "value": "1"},
            {"category": "background", "key": "b", "value": "2"},
        ]
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memories", return_value=entries),
        ):
            mgr.try_extract("请记住", "好")
        assert um.upsert.call_count == 2
        um.upsert.assert_any_call("preference", "a", "1", source="explicit")
        um.upsert.assert_any_call("background", "b", "2", source="explicit")


# ── Phase 1.2 触发节流策略（iter_2.md §4.9.2） ───────────────────────────

class TestExtractTriggerPolicy:
    """auto 模式下的"每 N 轮 + min_len"节流，显式触发必须不受影响。"""

    @staticmethod
    def _auto_mgr(input_str: str, every_n: int, min_len: int, *, call_times: int = 1):
        """跑 call_times 次 try_extract，返回 extract_memories 被调用次数。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", every_n),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", min_len),
            patch("src.agent.core.memory_manager.extract_memories", return_value=[]) as mock_extract,
        ):
            for _ in range(call_times):
                mgr.try_extract(input_str, "reply")
            return mock_extract.call_count

    def test_auto_n_throttle_skips_first_n_minus_1(self) -> None:
        """N=5：前 4 次不触发，第 5 次才触发。"""
        long_input = "x" * 100
        # 跑 4 次 → 0
        assert self._auto_mgr(long_input, every_n=5, min_len=0, call_times=4) == 0
        # 跑 5 次 → 1
        assert self._auto_mgr(long_input, every_n=5, min_len=0, call_times=5) == 1

    def test_auto_n_throttle_window_resets(self) -> None:
        """每达 N 次后计数重置，第 2N 次再次触发，共 2 次。"""
        long_input = "x" * 100
        assert self._auto_mgr(long_input, every_n=3, min_len=0, call_times=6) == 2

    def test_auto_min_len_skips_short_input(self) -> None:
        """短输入 < min_len 时即使到了 N 轮也不触发。"""
        short_input = "短"  # 1 字符
        assert self._auto_mgr(short_input, every_n=1, min_len=20, call_times=5) == 0

    def test_auto_min_len_zero_disables_length_filter(self) -> None:
        """min_len=0 时长度过滤被禁用（等同旧行为）。"""
        assert self._auto_mgr("", every_n=1, min_len=0, call_times=1) == 1

    def test_auto_every_n_one_triggers_every_turn(self) -> None:
        """N=1 + min_len=0 等同每轮触发（向后兼容旧 default）。"""
        long_input = "x" * 100
        assert self._auto_mgr(long_input, every_n=1, min_len=0, call_times=3) == 3

    def test_explicit_trigger_bypasses_throttle(self) -> None:
        """显式触发（"请记住"）即使 N=999、min_len=999 也立即触发。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", 999),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", 999),
            patch("src.agent.core.memory_manager.extract_memories", return_value=[]) as mock_extract,
        ):
            mgr.try_extract("短", "reply")
        mock_extract.assert_called_once()

    def test_explicit_trigger_does_not_consume_auto_counter(self) -> None:
        """显式触发不消耗也不重置 auto 计数器（混合场景下 auto 节流不被打乱）。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        long_input = "x" * 100
        with (
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", 3),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", 0),
            patch("src.agent.core.memory_manager.extract_memories", return_value=[]) as mock_extract,
        ):
            # 序列：auto, auto, explicit(不消耗), auto → 第 3 次 auto 应触发
            with patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False):
                mgr.try_extract(long_input, "r")  # counter=1
                mgr.try_extract(long_input, "r")  # counter=2
            with patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True):
                mgr.try_extract(long_input, "r")  # explicit，不动 counter
            with patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False):
                mgr.try_extract(long_input, "r")  # counter=3, 触发

        # 共 2 次：1 次 explicit + 1 次 auto-3rd
        assert mock_extract.call_count == 2

    def test_source_field_in_upsert(self) -> None:
        """auto 路径传 source='auto'，explicit 路径传 source='explicit'。"""
        um = MagicMock()
        mgr = _mk_mgr(user_memory=um)
        entries = [{"category": "preference", "key": "k", "value": "v"}]

        # auto
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_EVERY_N", 1),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_EXTRACT_MIN_INPUT_LEN", 0),
            patch("src.agent.core.memory_manager.extract_memories", return_value=entries),
        ):
            mgr.try_extract("hello world", "r")
        um.upsert.assert_called_with("preference", "k", "v", source="auto")

        # explicit
        um.reset_mock()
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=True),
            patch("src.agent.core.memory_manager.extract_memories", return_value=entries),
        ):
            mgr.try_extract("请记住 X", "r")
        um.upsert.assert_called_with("preference", "k", "v", source="explicit")
