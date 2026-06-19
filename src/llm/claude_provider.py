"""Claude（Anthropic 原生 SDK）调用实现。

由 `provider.py` 门面在 ``provider.sdk == "anthropic"`` 时分发到这里。入参统一是
OpenAI messages 格式（且已由门面做过 `_sanitize_messages_for_llm` 清洗），本模块负责
适配为 Anthropic API 格式、调用、并把响应包装回 OpenAI 兼容结构供上层统一处理。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

import src.config as config
from src.llm.thinking_params import claude_thinking_budget


def _split_system_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """把 OpenAI messages 拆成 ``(system_prompt, 其余消息)``。

    Anthropic 的 system 是独立顶层参数，messages 里不能带 system role。多条 system
    按出现顺序用换行拼成一段。
    """
    system_parts: list[str] = []
    filtered: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content") or ""
            if content:
                system_parts.append(content)
        else:
            filtered.append(msg)
    return "\n".join(system_parts), filtered


def _convert_tools_to_anthropic(
    openai_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将 OpenAI Function Calling 格式的 tools 转换为 Anthropic tools 格式。"""
    anthropic_tools: list[dict[str, Any]] = []
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
    usage = SimpleNamespace(
        prompt_tokens=response.usage.input_tokens,
        completion_tokens=response.usage.output_tokens,
        total_tokens=response.usage.input_tokens + response.usage.output_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    Claude 原生 SDK 调用分支（anthropic 库）。

    将 OpenAI 格式的 messages/tools 适配为 Anthropic API 格式后调用，
    返回值统一包装为与 OpenAI response 结构兼容的对象，供上层统一处理。
    当 on_token_chunk 非 None 时走流式调用，逐 token 回调。
    """
    import anthropic

    provider_config, model_config = config.get_active_model()

    system_prompt, filtered_messages = _split_system_messages(messages)

    kwargs: dict[str, Any] = {
        "model": model_config.model_id,
        "max_tokens": config.CLAUDE_MAX_TOKENS,
        "messages": filtered_messages,
        "temperature": temperature,
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    if tools:
        kwargs["tools"] = _convert_tools_to_anthropic(tools)

    def _do_call(client: Any) -> Any:
        if on_token_chunk is not None:
            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if getattr(event, "type", None) == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta and getattr(delta, "type", None) == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                on_token_chunk(text)
                return _wrap_anthropic_response(stream.get_final_message())
        response = client.messages.create(**kwargs)
        return _wrap_anthropic_response(response)

    if config.LLM_PROXY:
        with httpx.Client(proxy=config.LLM_PROXY) as http_client:
            client = anthropic.Anthropic(
                api_key=provider_config.api_key,
                http_client=http_client,
            )
            return _do_call(client)
    client = anthropic.Anthropic(api_key=provider_config.api_key)
    return _do_call(client)


def chat_thinking(
    messages: list[dict[str, Any]],
    budget_tokens: int,
    tools: list[dict[str, Any]] | None,
    on_thinking_chunk: Callable[[str], None] | None,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """Claude 原生 Extended Thinking 流式调用。"""
    import anthropic

    provider_config, model_config = config.get_active_model()

    system_prompt, filtered_messages = _split_system_messages(messages)
    budget, max_tokens = claude_thinking_budget(model_config, budget_tokens)

    kwargs: dict[str, Any] = {
        "model": model_config.model_id,
        "max_tokens": max_tokens,
        "temperature": 1,  # Extended Thinking 要求 temperature=1
        "thinking": {"type": "enabled", "budget_tokens": budget},
        "messages": filtered_messages,
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    if tools:
        kwargs["tools"] = _convert_tools_to_anthropic(tools)

    if config.LLM_PROXY:
        with httpx.Client(proxy=config.LLM_PROXY) as http_client:
            client = anthropic.Anthropic(
                api_key=provider_config.api_key,
                http_client=http_client,
            )
            final = _run_thinking_stream(client, kwargs, on_thinking_chunk, on_token_chunk)
    else:
        client = anthropic.Anthropic(api_key=provider_config.api_key)
        final = _run_thinking_stream(client, kwargs, on_thinking_chunk, on_token_chunk)

    return _wrap_anthropic_response(final)


def _run_thinking_stream(
    client: Any,
    kwargs: dict[str, Any],
    on_thinking_chunk: Callable[[str], None] | None,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    以流式方式驱动 Claude thinking 调用，通过回调逐块输出思考过程和正文 token。

    thinking 内容仅通过回调透传给调用方（打印到终端），不写入返回值，
    不进入 messages 历史，防止 prompt injection。
    """
    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            event_type = getattr(event, "type", None)
            if event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta:
                    if on_thinking_chunk is not None and getattr(delta, "type", None) == "thinking_delta":
                        thinking_text = getattr(delta, "thinking", "")
                        if thinking_text:
                            on_thinking_chunk(thinking_text)
                    elif on_token_chunk is not None and getattr(delta, "type", None) == "text_delta":
                        text = getattr(delta, "text", "")
                        if text:
                            on_token_chunk(text)
        return stream.get_final_message()
