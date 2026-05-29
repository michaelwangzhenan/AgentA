"""
LLM 统一调用接口

上层业务代码只需调用 chat() 函数，完全不感知底层 Provider 差异。
通过 config.py 中的 ACTIVE_PROVIDER 决定实际调用哪个服务。

支持两种调用模式：
    1. 普通对话：直接返回文本内容
    2. Function Calling：传入 tools 参数，返回原始 response 供 Agent 处理
"""

from collections.abc import Callable
from typing import Any

import json
import httpx
import openai

import src.config as config


def _openai_call(
    provider_cfg: config.ProviderConfig,
    need_proxy: bool,
    fn: Callable[[openai.OpenAI], Any],
) -> Any:
    """
    创建 OpenAI 客户端并调用 fn(client)，按需注入 HTTP 代理。

    代理模式下用 with 确保 httpx.Client 生命周期与 fn 调用绑定。
    """
    if need_proxy:
        with httpx.Client(proxy=config.LLM_PROXY) as http_client:
            client = openai.OpenAI(
                api_key=provider_cfg.api_key,
                base_url=provider_cfg.base_url or None,
                http_client=http_client,
            )
            return fn(client)
    return fn(openai.OpenAI(
        api_key=provider_cfg.api_key,
        base_url=provider_cfg.base_url or None,
    ))


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    统一 LLM 调用入口。

    Args:
        messages: 对话历史，格式遵循 OpenAI messages 规范。
        tools: Function Calling 工具列表（JSON Schema 格式），为 None 时走普通对话。
        temperature: 采样温度，0.0 ~ 1.0，越低越确定性。
        on_token_chunk: 每收到一段正文 token 时的回调，为 None 时走非流式。

    Returns:
        始终返回完整的 ChatCompletion response 对象，供 Agent 读取 choices 和 usage。

    Raises:
        openai.APIError: OpenAI 兼容 API 调用失败时抛出。
        anthropic.APIError: ACTIVE_PROVIDER 为 'claude' 时，原生 SDK 调用失败时抛出。
    """
    if config.ACTIVE_PROVIDER == "claude":
        return _chat_claude(messages, tools, temperature, on_token_chunk)

    provider_config = config.get_active_config()

    # 个别模型对 temperature 有硬约束（如 kimi-k2.6 强制要求 = 1，非 1 直接 400），
    # 由 ProviderConfig.force_temperature 声明，此处统一覆盖，避免在每个调用点重复处理。
    effective_temperature = (
        provider_config.force_temperature
        if provider_config.force_temperature is not None
        else temperature
    )

    kwargs: dict[str, Any] = {
        "model": provider_config.model,
        "messages": messages,
        "temperature": effective_temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if provider_config.extra_body:
        kwargs["extra_body"] = provider_config.extra_body

    need_proxy = config.ACTIVE_PROVIDER in config.PROXIED_PROVIDERS and bool(config.LLM_PROXY)

    if on_token_chunk is not None:
        # 工作绕道：qwen 在 streaming 模式下，当 LLM 同时输出 content + tool_call 时，
        # 所有 tool_call delta 的 function.name 字段一律为 None（args 拼接正常），导致
        # ToolCallEngine 报"未知工具：''"循环失败。非流式模式则返回完整 name。
        # 因此：传 tools 时禁用 streaming，一次性拿完整 message；之后把 content 通过
        # on_token_chunk 一次性回灌，保持 CLI / Chainlit 渲染入口一致。
        if tools:
            response = _openai_call(
                provider_config,
                need_proxy,
                lambda client: client.chat.completions.create(**kwargs),
            )
            try:
                msg_content = response.choices[0].message.content or ""
                if msg_content:
                    on_token_chunk(msg_content)
            except Exception:
                pass
            return response

        kwargs["stream"] = True
        # 流式响应里把 usage 放到最后一个 chunk，否则 prompt_tokens / completion_tokens
        # 都拿不到（kimi / qwen 等 OpenAI 兼容 provider 默认不推 usage）
        kwargs["stream_options"] = {"include_usage": True}
        return _openai_call(
            provider_config,
            need_proxy,
            lambda client: _run_openai_stream(client, kwargs, on_token_chunk),
        )

    return _openai_call(
        provider_config,
        need_proxy,
        lambda client: client.chat.completions.create(**kwargs),
    )


def _run_openai_stream(
    client: Any,
    kwargs: dict[str, Any],
    on_token_chunk: Callable[[str], None],
) -> Any:
    """OpenAI 兼容协议流式调用，逐 token 回调并返回与非流式相同结构的 response 对象。"""
    from types import SimpleNamespace

    content_parts: list[str] = []
    tool_calls_map: dict[int, dict[str, Any]] = {}
    prompt_tokens = completion_tokens = 0

    stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        if getattr(chunk, "usage", None):
            prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0

        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        if getattr(delta, "content", None):
            content_parts.append(delta.content)
            on_token_chunk(delta.content)

        if getattr(delta, "tool_calls", None):
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": tc_delta.id or "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                if tc_delta.id:
                    tool_calls_map[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_calls_map[idx]["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        tool_calls_map[idx]["function"]["arguments"] += tc_delta.function.arguments

    tool_calls = None
    if tool_calls_map:
        tool_calls = [
            SimpleNamespace(
                id=tc["id"],
                type="function",
                function=SimpleNamespace(
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ),
            )
            for tc in (tool_calls_map[k] for k in sorted(tool_calls_map))
        ]

    final_content = "".join(content_parts) or None
    message = SimpleNamespace(content=final_content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _chat_claude(
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

    provider_config = config.get_active_config()

    system_prompt, filtered_messages = _split_system_messages(messages)

    kwargs: dict[str, Any] = {
        "model": provider_config.model,
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


# ── Extended Thinking ────────────────────────────────────────────────────────

# Adaptive Thinking 三档 budget（tokens）
_BUDGET_LOW: int = 1_500     # 简单事实、短问（< 25 字符）
_BUDGET_MEDIUM: int = 8_000  # 分析、解释、对比（默认档）
_BUDGET_HIGH: int = 32_000   # 架构、规划、多步骤深度推理

# 高复杂度关键词 → HIGH 档
_HIGH_KEYWORDS: frozenset[str] = frozenset([
    "设计", "架构", "规划", "深入", "详细分析", "多步骤",
    "优化方案", "如何实现", "算法", "权衡", "对比分析",
    "全面", "综合", "系统方案", "路线图",
    "trade-off", "implement", "design", "architect",
    "optimize", "strategy", "roadmap",
])

# 低复杂度关键词 → LOW 档
_LOW_KEYWORDS: frozenset[str] = frozenset([
    "什么是", "是什么", "定义", "谢谢", "好的", "知道了", "明白了",
    "define", "what is", "who is", "thanks",
])


def estimate_thinking_budget(messages: list[dict[str, Any]], max_budget: int = 32_000) -> int:
    """
    根据对话中最后一条用户消息的内容自动估算合适的 thinking budget_tokens。

    分三档（均不超过 max_budget）：
        LOW    (1 500)  —— 短问或简单事实类
        MEDIUM (8 000)  —— 分析、解释、对比（默认）
        HIGH   (32 000) —— 架构、规划、多步骤深度推理

    Args:
        messages: 包含对话历史的 OpenAI 格式消息列表。
        max_budget: thinking budget 上限，通常取 Agent.thinking_budget。

    Returns:
        估算出的 budget_tokens，已被 max_budget 截断。
    """
    # 取最后一条 user 消息作为复杂度评估依据
    user_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_text = (msg.get("content") or "").strip()
            break

    text_lower = user_text.lower()

    # 极短问题 → LOW
    if len(user_text) < 25:
        estimated = _BUDGET_LOW
    # 含高复杂度关键词 → HIGH
    elif any(kw in text_lower for kw in _HIGH_KEYWORDS):
        estimated = _BUDGET_HIGH
    # 含低复杂度关键词 → LOW
    elif any(kw in text_lower for kw in _LOW_KEYWORDS):
        estimated = _BUDGET_LOW
    # 超长问题（多步骤描述）也升为 HIGH
    elif len(user_text) > 200:
        estimated = _BUDGET_HIGH
    else:
        estimated = _BUDGET_MEDIUM

    return min(estimated, max_budget)


def call_with_thinking(
    messages: list[dict[str, Any]],
    budget_tokens: int = 8000,
    tools: list[dict[str, Any]] | None = None,
    on_thinking_chunk: Callable[[str], None] | None = None,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    通用 Extended Thinking 入口。

    - Claude：走流式 thinking 分支，通过 on_thinking_chunk 回调实时输出思考过程。
    - Qwen3：走流式 thinking 分支，thinking 内容位于 delta.reasoning_content。
    - 其他 provider：静默降级为普通 chat()，保持向后兼容。

    Args:
        messages: 对话历史（OpenAI 格式）。
        budget_tokens: thinking 预算 tokens（Claude / Qwen3 有效）。
        tools: Function Calling 工具列表，可为 None。
        on_thinking_chunk: 每收到一段 thinking 文本时的回调，可为 None。
        on_token_chunk: 每收到一段正文 token 时的回调，可为 None。

    Returns:
        与 chat() 相同格式的 response 对象。
    """
    if config.ACTIVE_PROVIDER == "claude":
        return _chat_claude_thinking(messages, budget_tokens, tools, on_thinking_chunk, on_token_chunk)
    if config.ACTIVE_PROVIDER == "qwen":
        return _chat_qwen_thinking(messages, budget_tokens, tools, on_thinking_chunk, on_token_chunk)
    # 其他 provider 静默降级，thinking 不可用
    return chat(messages, tools=tools, on_token_chunk=on_token_chunk)


def _chat_claude_thinking(
    messages: list[dict[str, Any]],
    budget_tokens: int,
    tools: list[dict[str, Any]] | None,
    on_thinking_chunk: Callable[[str], None] | None,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """Claude 原生 Extended Thinking 流式调用。"""
    import anthropic

    provider_config = config.get_active_config()

    system_prompt, filtered_messages = _split_system_messages(messages)

    kwargs: dict[str, Any] = {
        "model": provider_config.model,
        # max_tokens 必须 > budget_tokens，Anthropic 强制要求
        "max_tokens": budget_tokens + 4096,
        "temperature": 1,  # Extended Thinking 要求 temperature=1
        "thinking": {"type": "enabled", "budget_tokens": budget_tokens},
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


def _chat_qwen_thinking(
    messages: list[dict[str, Any]],
    budget_tokens: int,
    tools: list[dict[str, Any]] | None,
    on_thinking_chunk: Callable[[str], None] | None,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    Qwen3 Extended Thinking 流式调用。

    Qwen3 API 要求 enable_thinking=True 时必须使用 stream=True；
    thinking 内容位于 delta.reasoning_content，正文位于 delta.content。
    thinking 内容仅通过回调输出到终端，不写入返回值，不进 messages，防止 prompt injection。
    """
    from types import SimpleNamespace

    provider_config = config.get_active_config()

    kwargs: dict[str, Any] = {
        "model": provider_config.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},  # 确保最后一个 chunk 携带 usage 统计
        "extra_body": {"enable_thinking": True},
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    need_proxy = config.ACTIVE_PROVIDER in config.PROXIED_PROVIDERS and bool(config.LLM_PROXY)
    return _openai_call(
        provider_config,
        need_proxy,
        lambda client: _run_qwen_thinking_stream(client, kwargs, on_thinking_chunk, on_token_chunk),
    )


def _run_qwen_thinking_stream(
    client: Any,
    kwargs: dict[str, Any],
    on_thinking_chunk: Callable[[str], None] | None,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    消费 Qwen3 流式响应，分离 reasoning_content（thinking）和 content（正文），
    拼接后返回与普通 chat() 兼容的 SimpleNamespace response 对象。

    thinking 内容只通过回调透传，不写入返回对象，防止 prompt injection。
    """
    from types import SimpleNamespace

    content_parts: list[str] = []
    tool_calls_map: dict[int, dict[str, Any]] = {}
    prompt_tokens = completion_tokens = 0

    stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        # usage 在任意 chunk 上（含 choices=[] 的最后一个 chunk），必须在 continue 前读取
        if getattr(chunk, "usage", None):
            prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
            completion_tokens = getattr(chunk.usage, "completion_tokens", 0)

        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue

        # thinking 内容：回调输出，不收集进正文
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning and on_thinking_chunk is not None:
            on_thinking_chunk(reasoning)

        # 正文内容
        if delta.content:
            content_parts.append(delta.content)
            if on_token_chunk is not None:
                on_token_chunk(delta.content)

        # tool_calls 增量拼接
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": tc_delta.id or "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                if tc_delta.id:
                    tool_calls_map[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_calls_map[idx]["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        tool_calls_map[idx]["function"]["arguments"] += tc_delta.function.arguments

    # 组装 tool_calls
    tool_calls = None
    if tool_calls_map:
        tool_calls = [
            SimpleNamespace(
                id=tc["id"],
                type="function",
                function=SimpleNamespace(
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ),
            )
            for tc in tool_calls_map.values()
        ]

    final_content = "".join(content_parts) or None
    message = SimpleNamespace(content=final_content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)
