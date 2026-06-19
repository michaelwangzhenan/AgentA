"""
provider.py 的 function name sanitize / restore 适配层单测。

锁住三类行为：
1. MCP namespaced tool name（带 `.`）→ `__`，可逆还原
2. 历史 messages 里的空 name tool_call 整条丢弃 + 对应 tool response 也丢
3. 注册 tools 列表里的非法 name 同样 sanitize
"""

from types import SimpleNamespace

from src.llm.openai_provider import (
    _restore_function_name,
    _restore_tool_call_names,
    _sanitize_function_name,
    _sanitize_tools_for_llm,
)
from src.llm.provider import _sanitize_messages_for_llm


class TestSanitizeFunctionName:
    def test_dot_becomes_double_underscore(self) -> None:
        assert _sanitize_function_name("filesystem.read_file") == "filesystem__read_file"

    def test_valid_name_unchanged(self) -> None:
        assert _sanitize_function_name("make_plan") == "make_plan"

    def test_special_chars_to_underscore(self) -> None:
        assert _sanitize_function_name("foo bar/baz") == "foo_bar_baz"

    def test_non_alpha_prefix_gets_t_prefix(self) -> None:
        assert _sanitize_function_name("123_tool") == "t_123_tool"


class TestRestoreFunctionName:
    def test_double_underscore_back_to_dot(self) -> None:
        assert _restore_function_name("filesystem__read_file") == "filesystem.read_file"

    def test_single_underscore_preserved(self) -> None:
        assert _restore_function_name("make_plan") == "make_plan"

    def test_bidirectional(self) -> None:
        for raw in ["filesystem.read_file", "make_plan", "fetch.fetch_url"]:
            assert _restore_function_name(_sanitize_function_name(raw)) == raw


class TestSanitizeMessagesForLlm:
    def test_dot_in_tool_call_name_sanitized(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "filesystem.read_file",
                            "arguments": "{}",
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "OK"},
        ]
        out = _sanitize_messages_for_llm(messages)
        assert len(out) == 2
        assert out[0]["tool_calls"][0]["function"]["name"] == "filesystem__read_file"
        assert out[1]["tool_call_id"] == "call_1"

    def test_empty_name_tool_call_dropped(self) -> None:
        """空 name tool_call 整条丢弃，对应 tool response 也丢。"""
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "thinking...",
                "tool_calls": [
                    {
                        "id": "call_bad",
                        "type": "function",
                        "function": {"name": "", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_bad", "content": "stale"},
            {"role": "assistant", "content": "final answer"},
        ]
        out = _sanitize_messages_for_llm(messages)
        roles = [m["role"] for m in out]
        # 应该保留：user, assistant(只剩 content), assistant(final)
        # 丢弃：tool response (call_bad)
        assert roles == ["user", "assistant", "assistant"]
        # 第二条 assistant 不应再含 tool_calls
        assert "tool_calls" not in out[1]
        # 没 content 也没 tool_calls 的 assistant 会被整条丢；本例 content 非空所以保留
        assert out[1]["content"] == "thinking..."

    def test_assistant_only_bad_tool_call_no_content_dropped(self) -> None:
        """assistant message 只有空 name tool_call 且 content 空 → 整条丢。"""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_bad",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                ],
            },
        ]
        out = _sanitize_messages_for_llm(messages)
        assert out == []

    def test_mixed_valid_and_invalid_tool_calls_in_one_message(self) -> None:
        """同一 assistant message 内多个 tool_call，只丢空名，留合法的。"""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_ok",
                        "type": "function",
                        "function": {"name": "make_plan", "arguments": "{}"},
                    },
                    {
                        "id": "call_bad",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                ],
            },
        ]
        out = _sanitize_messages_for_llm(messages)
        assert len(out) == 1
        assert len(out[0]["tool_calls"]) == 1
        assert out[0]["tool_calls"][0]["id"] == "call_ok"

    def test_does_not_mutate_input(self) -> None:
        """sanitize 返回新 list，不改原 messages。"""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "x",
                        "type": "function",
                        "function": {"name": "a.b", "arguments": "{}"},
                    },
                ],
            },
        ]
        _ = _sanitize_messages_for_llm(messages)
        assert messages[0]["tool_calls"][0]["function"]["name"] == "a.b"


class TestSanitizeToolsForLlm:
    def test_dot_in_tool_name_sanitized(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "filesystem.read_file",
                    "description": "Read a file",
                },
            },
        ]
        out = _sanitize_tools_for_llm(tools)
        assert out[0]["function"]["name"] == "filesystem__read_file"

    def test_empty_name_tool_dropped(self) -> None:
        tools = [
            {"type": "function", "function": {"name": "make_plan"}},
            {"type": "function", "function": {"name": ""}},
        ]
        out = _sanitize_tools_for_llm(tools)
        assert len(out) == 1
        assert out[0]["function"]["name"] == "make_plan"


class TestRestoreToolCallNames:
    def test_restores_dot_in_response_tool_calls(self) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="x",
                                type="function",
                                function=SimpleNamespace(
                                    name="filesystem__read_file",
                                    arguments="{}",
                                ),
                            ),
                        ],
                    ),
                ),
            ],
        )
        _restore_tool_call_names(response)
        assert response.choices[0].message.tool_calls[0].function.name == "filesystem.read_file"

    def test_response_without_tool_calls_no_error(self) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="plain text", tool_calls=None),
                ),
            ],
        )
        _restore_tool_call_names(response)
        assert response.choices[0].message.content == "plain text"
