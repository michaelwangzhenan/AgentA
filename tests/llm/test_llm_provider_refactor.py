"""provider 拆分后的新行为单测。

锁住：
1. claude_provider._split_system_messages 的系统消息拆分
2. thinking_params 的 Claude budget / OpenAI extra_body 翻译
3. 门面 call_with_thinking 在 thinking 两条路径派发前都做了 _sanitize_messages_for_llm
"""

import logging
from unittest.mock import patch

import pytest

import src.config as config
from src.llm.claude_provider import _split_system_messages
from src.llm.thinking_params import (
    claude_thinking_budget,
    openai_thinking_extra_body,
)


class TestSplitSystemMessages:
    def test_extracts_single_system(self) -> None:
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "hi"},
        ]
        system, rest = _split_system_messages(msgs)
        assert system == "你是助手"
        assert rest == [{"role": "user", "content": "hi"}]

    def test_joins_multiple_system_in_order(self) -> None:
        msgs = [
            {"role": "system", "content": "规则A"},
            {"role": "user", "content": "q"},
            {"role": "system", "content": "规则B"},
        ]
        system, rest = _split_system_messages(msgs)
        assert system == "规则A\n规则B"
        assert [m["role"] for m in rest] == ["user"]

    def test_no_system_returns_empty_str(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        system, rest = _split_system_messages(msgs)
        assert system == ""
        assert rest == msgs

    def test_empty_system_content_ignored(self) -> None:
        msgs = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "hi"},
        ]
        system, rest = _split_system_messages(msgs)
        assert system == ""
        assert [m["role"] for m in rest] == ["user"]


class TestClaudeThinkingBudget:
    def _model(self, max_output_tokens):
        return config.ModelConfig(
            provider="claude", model_id="claude-x", max_output_tokens=max_output_tokens,
        )

    def test_budget_within_cap(self) -> None:
        budget, max_tokens = claude_thinking_budget(self._model(64_000), 8000)
        assert budget == 8000
        assert max_tokens == 8000 + 4096

    def test_budget_floor_1024(self) -> None:
        budget, max_tokens = claude_thinking_budget(self._model(64_000), 100)
        assert budget == 1024
        assert max_tokens == 1024 + 4096

    def test_budget_clamped_by_cap(self) -> None:
        # cap=8192 → 上限 8192-4096=4096
        budget, max_tokens = claude_thinking_budget(self._model(8192), 8000)
        assert budget == 4096
        assert max_tokens == 4096 + 4096

    def test_none_max_output_falls_back_to_64000(self) -> None:
        budget, _ = claude_thinking_budget(self._model(None), 100_000)
        assert budget == 64_000 - 4096


class TestOpenaiThinkingExtraBody:
    def test_merges_enable_extra_body_and_budget(self) -> None:
        model = config.ModelConfig(
            provider="qwen", model_id="qwen-x",
            extra_body={"enable_thinking": False},
        )
        spec = config.ThinkingSpec(
            kind="openai_reasoning",
            enable_extra_body={"enable_thinking": True},
            budget_key="thinking_budget",
        )
        model_id, extra_body = openai_thinking_extra_body(model, spec, 4000)
        assert model_id == "qwen-x"
        assert extra_body["enable_thinking"] is True
        assert extra_body["thinking_budget"] == 4000

    def test_thinking_model_overrides_model_id(self) -> None:
        model = config.ModelConfig(provider="deepseek", model_id="deepseek-chat")
        spec = config.ThinkingSpec(kind="openai_reasoning", thinking_model="deepseek-reasoner")
        model_id, _ = openai_thinking_extra_body(model, spec, 4000)
        assert model_id == "deepseek-reasoner"


class TestThinkingPathsSanitize:
    """call_with_thinking 两条 thinking 路径派发前都应清洗 messages（含空名 tool_call 丢弃）。"""

    _DIRTY = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "bad", "type": "function", "function": {"name": "", "arguments": ""}},
            ],
        },
        {"role": "user", "content": "hi"},
    ]

    def test_claude_thinking_receives_sanitized(self) -> None:
        from src.llm.provider import call_with_thinking

        orig = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "claude-sonnet-4-5"
        try:
            with patch("src.llm.claude_provider.chat_thinking", return_value="ok") as m:
                call_with_thinking(list(self._DIRTY), budget_tokens=2000)
            sent = m.call_args.args[0]
            # 空名 tool_call 的 assistant（无 content）应被清掉，只剩 user
            assert [msg["role"] for msg in sent] == ["user"]
        finally:
            config.ACTIVE_MODEL = orig

    def test_openai_reasoning_receives_sanitized(self) -> None:
        from src.llm.provider import call_with_thinking

        orig = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "qwen3.6-flash"
        try:
            with patch("src.llm.openai_provider.chat_reasoning", return_value="ok") as m:
                call_with_thinking(list(self._DIRTY), budget_tokens=4000)
            sent = m.call_args.args[0]
            assert [msg["role"] for msg in sent] == ["user"]
        finally:
            config.ACTIVE_MODEL = orig


class TestLlmErrorLogsProvider:
    """LLM API 调用异常时，门面应记一行带 provider/model 的日志并原样抛出。"""

    def test_chat_logs_provider_and_reraises(self, caplog) -> None:
        from src.llm.provider import chat

        orig = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "qwen3.6-flash"
        try:
            with patch("src.llm.openai_provider.chat",
                       side_effect=RuntimeError("boom 503")):
                with caplog.at_level(logging.ERROR, logger="src.llm.provider"):
                    with pytest.raises(RuntimeError, match="boom 503"):
                        chat([{"role": "user", "content": "hi"}])
        finally:
            config.ACTIVE_MODEL = orig
        assert "[LLM] 调用异常" in caplog.text
        assert "qwen3.6-flash" in caplog.text  # model 出现在日志里

    def test_thinking_logs_provider_and_reraises(self, caplog) -> None:
        from src.llm.provider import call_with_thinking

        orig = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "claude-sonnet-4-5"
        try:
            with patch("src.llm.claude_provider.chat_thinking",
                       side_effect=RuntimeError("boom claude")):
                with caplog.at_level(logging.ERROR, logger="src.llm.provider"):
                    with pytest.raises(RuntimeError, match="boom claude"):
                        call_with_thinking([{"role": "user", "content": "hi"}],
                                           budget_tokens=2000)
        finally:
            config.ACTIVE_MODEL = orig
        assert "[LLM] 调用异常" in caplog.text
        assert "claude-sonnet-4-5" in caplog.text
