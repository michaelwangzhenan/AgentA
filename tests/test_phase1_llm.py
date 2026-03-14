"""
Phase 1 测试：LLM 配置层 & 统一调用接口

测试内容：
    - config.py：Provider 配置加载、active config 获取
    - llm/provider.py：Kimi API 调用（普通对话 & 带 system prompt）
"""

import pytest
import config
from config import ACTIVE_PROVIDER, get_active_config
from llm.provider import chat


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
        # 应返回 response 对象，有 choices 属性
        assert hasattr(response, "choices"), "传入 tools 时应返回 response 对象"
        assert len(response.choices) > 0
