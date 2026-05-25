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
        um.upsert.assert_called_once_with("preference", "k", "v")

    def test_auto_extract_passes_empty_context(self) -> None:
        """AUTO_EXTRACT 路径：context_history 必须为 ""（用严格 prompt）。"""
        um = MagicMock()
        recent = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]
        mgr = _mk_mgr(user_memory=um, recent_messages=recent)
        with (
            patch("src.agent.core.memory_manager.should_extract_immediately", return_value=False),
            patch("src.agent.core.memory_manager._cfg.USER_MEMORY_AUTO_EXTRACT", True),
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
        um.upsert.assert_any_call("preference", "a", "1")
        um.upsert.assert_any_call("background", "b", "2")
