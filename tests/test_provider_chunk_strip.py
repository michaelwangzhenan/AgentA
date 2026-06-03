"""测试 provider._strip_provider_chunk_echo 对 GLM 等 provider 的 chunk echo 剥离

GLM quirk：第一轮带 tool_call 的非流式响应里，message.content 偶发会拼一段
streaming chunk 的 raw JSON。这里测剥离 helper 既能剥干净，也不误伤正常内容。
"""

from src.llm.provider import _strip_provider_chunk_echo


# 典型 GLM chunk echo 样本（精简自截图实测）
_GLM_CHUNK_ECHO = (
    '{"index":0,"finish_reason":"tool_calls",'
    '"delta":{"role":"assistant","content":null,'
    '"reasoning_content":null,"reasoning":null,"audio":null,'
    '"tool_calls":[{"id":"call_xxx","index":0,"type":"function",'
    '"function":{"name":"search_knowledge",'
    '"arguments":"{\\"query\\": \\"hi\\"}"},"outputs":null}],'
    '"tool_call_id":null,"attachments":null,"metadata":null}}'
)


class TestStripEcho:
    def test_clean_content_unchanged(self):
        clean = "您叫汪振安，您具备的能力包括：作为 5G RAN 架构专家..."
        assert _strip_provider_chunk_echo(clean) == clean

    def test_empty_string_unchanged(self):
        assert _strip_provider_chunk_echo("") == ""

    def test_strips_single_echo_after_narration(self):
        polluted = (
            "您叫 Michael Wang。我将立即为您查找。" + _GLM_CHUNK_ECHO
        )
        cleaned = _strip_provider_chunk_echo(polluted)
        assert cleaned == "您叫 Michael Wang。我将立即为您查找。"
        assert '"finish_reason"' not in cleaned
        assert '{"index":' not in cleaned

    def test_strips_multiple_echoes(self):
        polluted = (
            "前段正文" + _GLM_CHUNK_ECHO + "中间正文" + _GLM_CHUNK_ECHO + "后段正文"
        )
        cleaned = _strip_provider_chunk_echo(polluted)
        assert cleaned == "前段正文中间正文后段正文"

    def test_pure_echo_yields_empty(self):
        assert _strip_provider_chunk_echo(_GLM_CHUNK_ECHO) == ""

    def test_does_not_strip_json_without_chunk_markers(self):
        """合法 JSON 块但不含 finish_reason / delta 标志 —— 不剥（避免误伤）"""
        polluted = '正常回答里给了个 JSON 示例：{"index": 1, "name": "demo"}'
        cleaned = _strip_provider_chunk_echo(polluted)
        assert cleaned == polluted

    def test_does_not_strip_truncated_echo(self):
        """JSON 不完整 / 解析失败时保守不动，避免误伤"""
        polluted = '前段{"index":0,"finish_reason":"tool_calls",'  # 缺尾巴
        cleaned = _strip_provider_chunk_echo(polluted)
        assert cleaned == polluted

    def test_does_not_strip_when_index_appears_in_prose(self):
        """字符串里如果只有 `{"index":` 但实际不是 JSON 起始 —— 不应崩溃"""
        polluted = '正文里提到 {"index":xx} 这种说法但不是合法 JSON'
        cleaned = _strip_provider_chunk_echo(polluted)
        assert cleaned == polluted
