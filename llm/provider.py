"""
LLM 统一调用接口

上层业务代码只需调用 chat() 函数，完全不感知底层 Provider 差异。
通过 config.py 中的 ACTIVE_PROVIDER 决定实际调用哪个服务。

支持两种调用模式：
    1. 普通对话：直接返回文本内容
    2. Function Calling：传入 tools 参数，返回原始 response 供 Agent 处理
"""

from collections.abc import Sequence
from typing import Any

import openai

import config
from config import ACTIVE_PROVIDER


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
) -> Any:
    """
    统一 LLM 调用入口。

    Args:
        messages: 对话历史，格式遵循 OpenAI messages 规范。
        tools: Function Calling 工具列表（JSON Schema 格式），为 None 时走普通对话。
        temperature: 采样温度，0.0 ~ 1.0，越低越确定性。

    Returns:
        - 若未传入 tools：返回 LLM 回复的文本字符串。
        - 若传入 tools：返回完整的 ChatCompletion response 对象，供 Agent 判断是否有 tool_calls。

    Raises:
        ValueError: 当 ACTIVE_PROVIDER 为 'claude' 时走原生 SDK 分支。
        openai.APIError: API 调用失败时抛出。
    """
    if ACTIVE_PROVIDER == "claude":
        return _chat_claude(messages, tools, temperature)

    provider_config = config.get_active_config()

    client = openai.OpenAI(
        api_key=provider_config.api_key,
        base_url=provider_config.base_url,
    )

    kwargs: dict[str, Any] = {
        "model": provider_config.model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    response = client.chat.completions.create(**kwargs)

    # 若没有传入 tools，直接返回文本，方便简单场景使用
    if not tools:
        return response.choices[0].message.content

    return response


def _chat_claude(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
) -> Any:
    """
    Claude 原生 SDK 调用分支（anthropic 库）。

    将 OpenAI 格式的 messages/tools 适配为 Anthropic API 格式后调用，
    返回值统一包装为与 OpenAI response 结构兼容的对象，供上层统一处理。
    """
    import anthropic

    provider_config = config.get_active_config()
    client = anthropic.Anthropic(api_key=provider_config.api_key)

    # 分离 system prompt 和对话历史（Anthropic API 要求分开传）
    system_prompt = ""
    filtered_messages: list[dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            filtered_messages.append(msg)

    kwargs: dict[str, Any] = {
        "model": provider_config.model,
        "max_tokens": 4096,
        "messages": filtered_messages,
        "temperature": temperature,
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    if tools:
        # 将 OpenAI tools 格式转换为 Anthropic 格式
        kwargs["tools"] = _convert_tools_to_anthropic(tools)

    response = client.messages.create(**kwargs)

    # 若无 tools，直接返回文本
    if not tools:
        return response.content[0].text

    return _wrap_anthropic_response(response)


def _convert_tools_to_anthropic(
    openai_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将 OpenAI Function Calling 格式的 tools 转换为 Anthropic tools 格式。"""
    anthropic_tools = []
    for tool in openai_tools:
        func = tool["function"]
        anthropic_tools.append({
            "name": func["name"],
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        })
    return anthropic_tools


def _wrap_anthropic_response(response: Any) -> Any:
    """
    将 Anthropic response 包装为与 OpenAI ChatCompletion 结构兼容的简单对象，
    使 Agent 层可以用统一方式处理 tool_calls。
    """
    from types import SimpleNamespace

    tool_calls = []
    text_content = ""

    for block in response.content:
        if block.type == "tool_use":
            import json
            tool_calls.append(SimpleNamespace(
                id=block.id,
                type="function",
                function=SimpleNamespace(
                    name=block.name,
                    arguments=json.dumps(block.input),
                ),
            ))
        elif block.type == "text":
            text_content = block.text

    message = SimpleNamespace(
        content=text_content or None,
        tool_calls=tool_calls if tool_calls else None,
    )
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])
