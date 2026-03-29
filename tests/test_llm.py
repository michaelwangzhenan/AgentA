"""
测试：LLM 配置层 & 统一调用接口

测试内容：
    - config.py：Provider 配置加载、active config 获取
    - llm/provider.py：当前激活 Provider API 调用（普通对话 & 带 system prompt）
    - TestAllProviders：遍历全部 9 个 provider 的 key 完整性（单元）及 API 连通性（集成）
    - TestProviderConfigExtraBody：extra_body 字段与 THINKING 配置
    - TestCallWithThinking：call_with_thinking() 路由逻辑
"""

from unittest.mock import patch

import pytest
import src.config as config
from src.config import ACTIVE_PROVIDER, get_active_config
from src.llm.provider import chat

# 不走代理的国内 provider（直连）
_DOMESTIC_PROVIDERS = {"kimi", "deepseek", "qwen", "minimax", "glm", "ollama"}
# 需要走代理的国外 provider
_FOREIGN_PROVIDERS = {"openai", "grok", "claude"}


class TestConfig:
    """测试 config.py 配置加载"""

    def test_active_provider_is_set(self) -> None:
        """ACTIVE_PROVIDER 必须有值"""
        assert ACTIVE_PROVIDER, "LLM_PROVIDER 未在 .env 中配置"

    def test_active_provider_is_supported(self) -> None:
        """ACTIVE_PROVIDER 必须是支持的值"""
        supported = set(config.PROVIDER_CONFIGS.keys())
        assert ACTIVE_PROVIDER in supported, (
            f"LLM_PROVIDER='{ACTIVE_PROVIDER}' 不在支持列表 {supported} 中"
        )

    def test_get_active_config_returns_provider_config(self) -> None:
        """get_active_config() 应返回 ProviderConfig 实例"""
        cfg = get_active_config()
        assert cfg.api_key, f"Provider '{ACTIVE_PROVIDER}' 的 api_key 为空，请检查 .env"
        assert cfg.model, f"Provider '{ACTIVE_PROVIDER}' 的 model 为空"

    def test_invalid_provider_raises_value_error(self) -> None:
        """非法 provider 名称应抛出 ValueError"""
        original = config.ACTIVE_PROVIDER
        config.ACTIVE_PROVIDER = "invalid_provider_xyz"
        with pytest.raises(ValueError, match="不支持的 LLM_PROVIDER"):
            get_active_config()
        config.ACTIVE_PROVIDER = original  # 还原

    def test_chroma_db_path_is_set(self) -> None:
        """ChromaDB 路径必须配置"""
        assert config.CHROMA_DB_PATH

    def test_chunk_size_greater_than_overlap(self) -> None:
        """chunk size 必须大于 overlap，否则分块逻辑无意义"""
        assert config.CHUNK_SIZE > config.CHUNK_OVERLAP, (
            f"CHUNK_SIZE({config.CHUNK_SIZE}) 应大于 CHUNK_OVERLAP({config.CHUNK_OVERLAP})"
        )


class TestLLMProvider:
    """测试 llm/provider.py 统一调用接口（需要真实 API，标记为 integration）"""

    @pytest.mark.integration
    def test_simple_chat_returns_string(self) -> None:
        """普通对话应返回非空字符串"""
        reply = chat([{"role": "user", "content": "用一句话介绍你自己"}])
        assert isinstance(reply, str), f"返回类型应为 str，实际为 {type(reply)}"
        assert len(reply) > 0, "返回内容不应为空"

    @pytest.mark.integration
    def test_chat_with_system_prompt(self) -> None:
        """带 system prompt 的对话应正常工作"""
        messages = [
            {"role": "system", "content": "你是一个只会回答'是'或'否'的机器人。"},
            {"role": "user", "content": "天空是蓝色的吗？"},
        ]
        reply = chat(messages)
        assert isinstance(reply, str)
        assert len(reply) > 0

    @pytest.mark.integration
    def test_chat_with_tools_returns_response_object(self) -> None:
        """传入 tools 时应返回 response 对象（含 choices）而非字符串"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "获取指定城市的天气",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名称"}
                        },
                        "required": ["city"],
                    },
                },
            }
        ]
        response = chat(
            [{"role": "user", "content": "北京今天天气怎么样？"}],
            tools=tools,
        )
        assert hasattr(response, "choices"), "传入 tools 时应返回 response 对象"
        assert len(response.choices) > 0


class TestAllProviders:
    """遍历全部 provider 的测试"""

    def test_all_providers_registered(self) -> None:
        """PROVIDER_CONFIGS 应包含全部 9 个 provider"""
        expected = {"kimi", "openai", "deepseek", "grok", "ollama", "claude",
                    "qwen", "minimax", "glm"}
        actual = set(config.PROVIDER_CONFIGS.keys())
        assert expected == actual, f"缺少 provider: {expected - actual}"

    @pytest.mark.parametrize("provider", [
        "kimi", "deepseek", "qwen", "minimax", "glm",   # 国内
        "openai", "grok", "claude",                       # 国外
    ])
    def test_provider_has_model_and_url(self, provider: str) -> None:
        """每个 provider 都必须配置 model 和 base_url（claude 除外）"""
        cfg = config.PROVIDER_CONFIGS[provider]
        assert cfg.model, f"[{provider}] model 未配置"
        if provider != "claude":  # claude 的 base_url 允许为空（使用原生 SDK）
            assert cfg.base_url, f"[{provider}] base_url 未配置"

    @pytest.mark.parametrize("provider", [
        "kimi", "deepseek", "qwen", "minimax", "glm",
        "openai", "grok", "claude",
    ])
    def test_provider_api_key_not_empty(self, provider: str) -> None:
        """每个非 ollama provider 的 api_key 都必须在 .env 中配置"""
        cfg = config.PROVIDER_CONFIGS[provider]
        assert cfg.api_key, (
            f"[{provider}] api_key 为空，请在 .env 中配置对应的 KEY"
        )

    @pytest.mark.integration
    @pytest.mark.parametrize("provider", [
        "kimi", "deepseek", "qwen", "minimax", "glm",
    ])
    def test_domestic_provider_chat(self, provider: str) -> None:
        """遍历国内 provider，各发一条消息验证 API 连通（需真实网络）"""
        original = config.ACTIVE_PROVIDER
        config.ACTIVE_PROVIDER = provider
        try:
            reply = chat([{"role": "user", "content": "用一句话介绍你自己"}])
            assert isinstance(reply, str) and len(reply) > 0, \
                f"[{provider}] 返回内容为空"
        finally:
            config.ACTIVE_PROVIDER = original

    @pytest.mark.integration
    @pytest.mark.parametrize("provider", [
        "openai", "grok", "claude",
    ])
    def test_foreign_provider_chat(self, provider: str) -> None:
        """遍历国外 provider，各发一条消息验证 API 连通（需代理）"""
        original = config.ACTIVE_PROVIDER
        config.ACTIVE_PROVIDER = provider
        try:
            reply = chat([{"role": "user", "content": "用一句话介绍你自己"}])
            assert isinstance(reply, str) and len(reply) > 0, \
                f"[{provider}] 返回内容为空"
        finally:
            config.ACTIVE_PROVIDER = original


# ── extra_body / thinking 配置新增测试 ───────────────────────────────────────

class TestProviderConfigExtraBody:
    """测试 ProviderConfig.extra_body 字段及 THINKING 相关配置"""

    def test_qwen_config_has_enable_thinking_false(self) -> None:
        """qwen provider 必须配置 extra_body={'enable_thinking': False}，
        避免非流式调用返回 400 错误。"""
        cfg = config.PROVIDER_CONFIGS["qwen"]
        assert cfg.extra_body == {"enable_thinking": False}

    def test_other_providers_have_no_extra_body(self) -> None:
        """其余 provider 不需要 extra_body，应为 None。"""
        for name in ("kimi", "deepseek", "openai", "claude", "glm", "minimax"):
            cfg = config.PROVIDER_CONFIGS[name]
            assert cfg.extra_body is None, f"[{name}] extra_body 应为 None"

    def test_thinking_enabled_is_bool(self) -> None:
        """THINKING_ENABLED 应为 bool 类型。"""
        assert isinstance(config.THINKING_ENABLED, bool)

    def test_thinking_budget_is_positive_int(self) -> None:
        """THINKING_BUDGET 应为正整数。"""
        assert isinstance(config.THINKING_BUDGET, int)
        assert config.THINKING_BUDGET > 0

    def test_thinking_adaptive_is_bool(self) -> None:
        """THINKING_ADAPTIVE 应为 bool 类型。"""
        assert isinstance(config.THINKING_ADAPTIVE, bool)


class TestCallWithThinking:
    """测试 call_with_thinking() 路由逻辑（不消耗真实 API）"""

    def test_non_claude_non_qwen_falls_back_to_chat(self) -> None:
        """非 claude / qwen provider 时，call_with_thinking 应静默降级为 chat()。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_PROVIDER
        config.ACTIVE_PROVIDER = "kimi"
        try:
            with patch("src.llm.provider.chat", return_value="fallback") as mock_chat:
                result = call_with_thinking([{"role": "user", "content": "hi"}])
            mock_chat.assert_called_once()
            assert result == "fallback"
        finally:
            config.ACTIVE_PROVIDER = original

    def test_claude_routes_to_claude_thinking(self) -> None:
        """ACTIVE_PROVIDER == 'claude' 时，应调用 _chat_claude_thinking 分支。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_PROVIDER
        config.ACTIVE_PROVIDER = "claude"
        try:
            with patch("src.llm.provider._chat_claude_thinking",
                       return_value="claude_resp") as mock_ct:
                result = call_with_thinking([{"role": "user", "content": "hi"}],
                                            budget_tokens=2000)
            mock_ct.assert_called_once()
            assert result == "claude_resp"
        finally:
            config.ACTIVE_PROVIDER = original

    def test_qwen_routes_to_qwen_thinking(self) -> None:
        """ACTIVE_PROVIDER == 'qwen' 时，应调用 _chat_qwen_thinking 分支。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_PROVIDER
        config.ACTIVE_PROVIDER = "qwen"
        try:
            with patch("src.llm.provider._chat_qwen_thinking",
                       return_value="qwen_resp") as mock_qt:
                result = call_with_thinking([{"role": "user", "content": "hi"}],
                                            budget_tokens=4000)
            mock_qt.assert_called_once()
            assert result == "qwen_resp"
        finally:
            config.ACTIVE_PROVIDER = original

    def test_tools_passed_to_fallback_chat(self) -> None:
        """降级 chat() 调用时，tools 参数应透传。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_PROVIDER
        config.ACTIVE_PROVIDER = "deepseek"
        dummy_tools = [{"type": "function", "function": {"name": "dummy"}}]
        try:
            with patch("src.llm.provider.chat", return_value="ok") as mock_chat:
                call_with_thinking([{"role": "user", "content": "q"}],
                                   tools=dummy_tools)
            _, kwargs = mock_chat.call_args
            assert kwargs.get("tools") == dummy_tools
        finally:
            config.ACTIVE_PROVIDER = original


class TestEstimateThinkingBudget:
    """测试 estimate_thinking_budget() 各分支路由逻辑"""

    @staticmethod
    def _msgs(content: str) -> list[dict]:
        return [{"role": "user", "content": content}]

    def test_short_question_returns_low(self) -> None:
        """长度 < 25 字符的短问应返回 LOW (1500)。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_LOW
        result = estimate_thinking_budget(self._msgs("今天天气？"))
        assert result == _BUDGET_LOW

    def test_long_question_over_200_returns_high(self) -> None:
        """长度 > 200 字符的详细题应返回 HIGH (32000)。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_HIGH
        long_text = "请详细" + "分析该问题" * 40
        result = estimate_thinking_budget(self._msgs(long_text))
        assert result == _BUDGET_HIGH

    def test_high_keyword_returns_high(self) -> None:
        """含高复杂度关键词应返回 HIGH (32000)。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_HIGH
        # 28 字符，含"设计""规划""架构"，确保 >= 25 不触发 SHORT 规则
        result = estimate_thinking_budget(
            self._msgs("请为我们的新平台详细设计并规划一个分布式缓存架构体系方案")
        )
        assert result == _BUDGET_HIGH

    def test_low_keyword_returns_low(self) -> None:
        """含低复杂度关键词应返回 LOW (1500)。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_LOW
        result = estimate_thinking_budget(self._msgs("什么是向量数据库？请简单解释一下。"))
        assert result == _BUDGET_LOW

    def test_medium_question_returns_medium(self) -> None:
        """中等长度且无特征关键词，应返回 MEDIUM (8000)。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_MEDIUM
        result = estimate_thinking_budget(
            self._msgs("在向量检索结果上如何进行二阶段精排，常用方法有哪些？")
        )
        assert result == _BUDGET_MEDIUM

    def test_max_budget_cap_applied(self) -> None:
        """估算值不应超过 max_budget。"""
        from src.llm.provider import estimate_thinking_budget
        long_text = "请设计" + "架构" * 50
        result = estimate_thinking_budget(self._msgs(long_text), max_budget=5000)
        assert result == 5000

    def test_no_user_message_returns_low(self) -> None:
        """没有 user 消息时，user_text=''，长度 0 < 25 应返回 LOW。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_LOW
        result = estimate_thinking_budget([{"role": "system", "content": "系统提示"}])
        assert result == _BUDGET_LOW

    def test_last_user_message_used(self) -> None:
        """应取最后一条 user 消息作为估算依据。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_HIGH
        msgs = [
            {"role": "user", "content": "好的"},          # 短问，LOW
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "请详细设计并分析该系统架构的核心模块划分、接口设计与优化方案"},  # HIGH
        ]
        result = estimate_thinking_budget(msgs)
        assert result == _BUDGET_HIGH

    def test_english_high_keyword(self) -> None:
        """英文高复杂度关键词应正确番入 HIGH。"""
        from src.llm.provider import estimate_thinking_budget, _BUDGET_HIGH
        result = estimate_thinking_budget(
            self._msgs("Please design a scalable microservices architecture for our platform")
        )
        assert result == _BUDGET_HIGH
