"""
测试：LLM 配置层 & 统一调用接口

测试内容：
    - config.py：两档配置（厂商 PROVIDER_CONFIGS + 模型 MODEL_CONFIGS）加载、get_active_model
    - llm/provider.py：当前激活模型 API 调用（普通对话 & 带 system prompt）
    - TestAllProviders / TestAllModels：厂商与模型的 key 完整性（单元）及 API 连通性（集成）
    - TestModelConfigExtraBody：extra_body 字段与 THINKING 配置
    - TestCallWithThinking：call_with_thinking() 路由逻辑
"""

from unittest.mock import patch

import pytest
import src.config as config
from src.config import ACTIVE_MODEL, get_active_model
from src.llm.provider import chat

# 各厂商代表模型（设 ACTIVE_MODEL 用）
_PROVIDER_MODEL = {
    "kimi": "kimi-k2.5",
    "qwen": "qwen3.5-flash",
    "deepseek": "deepseek-chat",
    "minimax": "MiniMax-Text-01",
    "glm": "glm-4-flash",
    "openai": "gpt-4o",
    "grok": "grok-3-latest",
    "claude": "claude-sonnet-4-5",
    "ollama": "qwen2.5:7b",
}


class TestConfig:
    """测试 config.py 两档配置加载"""

    def test_active_model_is_set(self) -> None:
        """ACTIVE_MODEL 必须有值"""
        assert ACTIVE_MODEL, "ACTIVE_MODEL 未在 .env 中配置"

    def test_active_model_is_supported(self) -> None:
        """ACTIVE_MODEL 必须是支持的值"""
        supported = set(config.MODEL_CONFIGS.keys())
        assert ACTIVE_MODEL in supported, (
            f"ACTIVE_MODEL='{ACTIVE_MODEL}' 不在支持列表 {supported} 中"
        )

    def test_get_active_model_returns_provider_and_model(self) -> None:
        """get_active_model() 应返回 (ProviderConfig, ModelConfig)。"""
        prov, model = get_active_model()
        assert prov.api_key, f"模型 '{ACTIVE_MODEL}' 所属厂商 api_key 为空，请检查 .env"
        assert model.model_id, f"模型 '{ACTIVE_MODEL}' 的 model_id 为空"
        assert model.provider in config.PROVIDER_CONFIGS

    def test_invalid_model_raises_value_error(self) -> None:
        """非法 model id 应抛出 ValueError"""
        original = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "invalid_model_xyz"
        with pytest.raises(ValueError, match="不支持的 ACTIVE_MODEL"):
            get_active_model()
        config.ACTIVE_MODEL = original  # 还原

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
    """遍历全部厂商 / 模型的测试"""

    def test_all_providers_registered(self) -> None:
        """PROVIDER_CONFIGS 应包含全部 10 个厂商"""
        expected = {"kimi", "openai", "deepseek", "grok", "ollama", "claude",
                    "qwen", "minimax", "glm", "gemini"}
        actual = set(config.PROVIDER_CONFIGS.keys())
        assert expected == actual, (
            f"缺少厂商: {expected - actual}；多出厂商: {actual - expected}"
        )

    def test_every_model_points_to_known_provider(self) -> None:
        """每个模型的 provider 都必须在 PROVIDER_CONFIGS 中。"""
        for mid, m in config.MODEL_CONFIGS.items():
            assert m.provider in config.PROVIDER_CONFIGS, (
                f"模型 '{mid}' 指向未知厂商 '{m.provider}'"
            )
            assert m.model_id, f"模型 '{mid}' 的 model_id 为空"

    def test_all_providers_have_base_url(self) -> None:
        """所有厂商都必须配置 base_url（claude 走原生 SDK、ollama 本地除外）。"""
        for name, cfg in config.PROVIDER_CONFIGS.items():
            if name in ("claude", "ollama"):
                continue
            assert cfg.base_url, f"[{name}] base_url 未配置"

    def test_default_providers_api_key_not_empty(self) -> None:
        """默认必配的 kimi / qwen 厂商 api_key 必须在 .env 中配置。

        其余厂商按需配置，不在默认 UT 里强校验（要验全部走真实连通性 integration 测试）。
        """
        for provider in ("kimi", "qwen"):
            assert config.PROVIDER_CONFIGS[provider].api_key, (
                f"[{provider}] api_key 为空，请在 .env 中配置对应的 KEY"
            )

    @pytest.mark.integration
    @pytest.mark.parametrize("provider", [
        "kimi", "deepseek", "qwen", "minimax", "glm",
    ])
    def test_domestic_provider_chat(self, provider: str) -> None:
        """遍历国内厂商，各发一条消息验证 API 连通（需真实网络）"""
        original = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = _PROVIDER_MODEL[provider]
        try:
            reply = chat([{"role": "user", "content": "用一句话介绍你自己"}])
            assert isinstance(reply, str) and len(reply) > 0, \
                f"[{provider}] 返回内容为空"
        finally:
            config.ACTIVE_MODEL = original

    @pytest.mark.integration
    @pytest.mark.parametrize("provider", [
        "openai", "grok", "claude",
    ])
    def test_foreign_provider_chat(self, provider: str) -> None:
        """遍历国外厂商，各发一条消息验证 API 连通（需代理）"""
        original = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = _PROVIDER_MODEL[provider]
        try:
            reply = chat([{"role": "user", "content": "用一句话介绍你自己"}])
            assert isinstance(reply, str) and len(reply) > 0, \
                f"[{provider}] 返回内容为空"
        finally:
            config.ACTIVE_MODEL = original


# ── extra_body / thinking 配置新增测试 ───────────────────────────────────────

class TestModelConfigExtraBody:
    """测试 ModelConfig.extra_body 字段及 THINKING 相关配置"""

    def test_qwen_model_has_enable_thinking_false(self) -> None:
        """qwen3.5-flash 必须配置 extra_body={'enable_thinking': False}，
        避免非流式调用返回 400 错误。"""
        m = config.MODEL_CONFIGS["qwen3.5-flash"]
        assert m.extra_body == {"enable_thinking": False}

    def test_kimi_extra_body_disables_thinking(self) -> None:
        """kimi 默认会开启 thinking，需通过 extra_body 显式关闭，避免非流式调用 400。"""
        m = config.MODEL_CONFIGS["kimi-k2.5"]
        assert m.extra_body == {"thinking": {"type": "disabled"}}

    def test_only_kimi_qwen_have_extra_body(self) -> None:
        """只有 kimi / qwen 系模型用 extra_body 关 thinking，其余厂商的模型应为 None。"""
        for mid, m in config.MODEL_CONFIGS.items():
            if m.provider in ("kimi", "qwen"):
                continue  # 这两家按模型需要关 thinking，不在此强校验
            assert m.extra_body is None, f"[{mid}] extra_body 应为 None"

    def test_thinking_enabled_is_bool(self) -> None:
        """THINKING_ENABLED 应为 bool 类型。"""
        assert isinstance(config.THINKING_ENABLED, bool)

    def test_thinking_budget_is_positive_int(self) -> None:
        """THINKING_BUDGET 应为正整数。"""
        assert isinstance(config.THINKING_BUDGET, int)
        assert config.THINKING_BUDGET > 0


class TestCallWithThinking:
    """测试 call_with_thinking() 按 ModelConfig.thinking 分发的路由逻辑（不消耗真实 API）"""

    def test_no_thinking_spec_falls_back_to_chat(self) -> None:
        """thinking 未声明的模型（如 ollama 的 qwen2.5:7b）应静默降级为 chat()。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "qwen2.5:7b"
        try:
            with patch("src.llm.provider.chat", return_value="fallback") as mock_chat:
                result = call_with_thinking([{"role": "user", "content": "hi"}])
            mock_chat.assert_called_once()
            assert result == "fallback"
        finally:
            config.ACTIVE_MODEL = original

    def test_claude_routes_to_claude_thinking(self) -> None:
        """kind='anthropic' 的模型（claude）应调用 _chat_claude_thinking 分支。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "claude-sonnet-4-5"
        try:
            with patch("src.llm.provider._chat_claude_thinking",
                       return_value="claude_resp") as mock_ct:
                result = call_with_thinking([{"role": "user", "content": "hi"}],
                                            budget_tokens=2000)
            mock_ct.assert_called_once()
            assert result == "claude_resp"
        finally:
            config.ACTIVE_MODEL = original

    def test_openai_reasoning_models_route_to_reasoning(self) -> None:
        """kind='openai_reasoning' 的模型（qwen/kimi/glm/minimax/deepseek）应走 _chat_openai_reasoning。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_MODEL
        for mid in ("qwen3.5-flash", "kimi-k2.5", "glm-4-flash",
                    "MiniMax-Text-01", "deepseek-chat"):
            config.ACTIVE_MODEL = mid
            try:
                with patch("src.llm.provider._chat_openai_reasoning",
                           return_value=f"{mid}_resp") as mock_r:
                    result = call_with_thinking([{"role": "user", "content": "hi"}],
                                                budget_tokens=4000)
                mock_r.assert_called_once()
                assert result == f"{mid}_resp"
            finally:
                config.ACTIVE_MODEL = original

    def test_tools_passed_to_fallback_chat(self) -> None:
        """降级 chat() 调用时，tools 参数应透传。"""
        from src.llm.provider import call_with_thinking

        original = config.ACTIVE_MODEL
        config.ACTIVE_MODEL = "qwen2.5:7b"
        dummy_tools = [{"type": "function", "function": {"name": "dummy"}}]
        try:
            with patch("src.llm.provider.chat", return_value="ok") as mock_chat:
                call_with_thinking([{"role": "user", "content": "q"}],
                                   tools=dummy_tools)
            _, kwargs = mock_chat.call_args
            assert kwargs.get("tools") == dummy_tools
        finally:
            config.ACTIVE_MODEL = original


class TestUTLLMModel:
    """UT 专用配置 resolve_ut_llm_model()：UT 跑真实 LLM 时该用哪个 model。"""

    def test_empty_falls_back_to_active_model(self) -> None:
        """UT_LLM_MODEL 为空 → 回落 ACTIVE_MODEL。"""
        orig = config.UT_LLM_MODEL
        config.UT_LLM_MODEL = ""
        try:
            assert config.resolve_ut_llm_model() == config.ACTIVE_MODEL
        finally:
            config.UT_LLM_MODEL = orig

    def test_valid_model_used(self) -> None:
        """UT_LLM_MODEL 合法 → 用它。"""
        orig = config.UT_LLM_MODEL
        target = next(iter(config.MODEL_CONFIGS))
        config.UT_LLM_MODEL = target
        try:
            assert config.resolve_ut_llm_model() == target
        finally:
            config.UT_LLM_MODEL = orig

    def test_invalid_model_falls_back(self) -> None:
        """UT_LLM_MODEL 非法 → 回落 ACTIVE_MODEL。"""
        orig = config.UT_LLM_MODEL
        config.UT_LLM_MODEL = "__not_a_real_model__"
        try:
            assert config.resolve_ut_llm_model() == config.ACTIVE_MODEL
        finally:
            config.UT_LLM_MODEL = orig
