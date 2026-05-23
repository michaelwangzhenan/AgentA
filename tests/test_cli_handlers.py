"""CLI handlers 辅助函数单测。"""

from src.cli.handlers import (
    _conversation_messages,
    _is_visible_assistant_message,
    _sanitize_cli_text,
)


def test_sanitize_cli_text_strips_carriage_returns() -> None:
    assert _sanitize_cli_text("根据\r\n知识库") == "根据\n知识库"
    assert _sanitize_cli_text("Agent:\r开头") == "Agent:\n开头"


def test_is_visible_assistant_skips_tool_only() -> None:
    assert not _is_visible_assistant_message(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}
    )
    assert _is_visible_assistant_message(
        {"role": "assistant", "content": "最终回答", "tool_calls": [{"id": "1"}]}
    )


def test_conversation_messages_filters_tool_only_assistant() -> None:
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "assistant", "content": "最终回答"},
    ]
    out = _conversation_messages(msgs)
    assert len(out) == 2
    assert out[1]["content"] == "最终回答"
