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
import re

import httpx
import openai

import src.config as config


# ── Function name sanitize adapter ────────────────────────────────────────────
#
# OpenAI 兼容协议规定 tool/function name 必须匹配 `^[a-zA-Z0-9_-]+$`。
# 历史 / 当前数据源里两类违规：
#   1. MCP namespaced tool 用 `.` 做 server/tool 分隔（如 `filesystem.read_file`）
#   2. 历史 messages 里早期写入的 tool_calls 偶有 `name=""`（早期 bug 残留）
#
# 策略：发给 LLM 前 sanitize（`.` → `__`、其他非法字符 → `_`、空 name 整条 tool_call
# 丢弃），LLM 返回 tool_calls 后 reverse（`__` → `.`），让 agent 派发层（`tools.py`
# 的 `if "." in name` 判 MCP）以及 `mcp_manager.call_tool` 完全不感知本层翻译。

_VALID_FN_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def _sanitize_function_name(raw: str) -> str:
    """把任意字符串转成符合 LLM function name 规则的形式。

    规则（与 `_restore_function_name` 互为逆，前提是原始名不含 `__`）：
      - `.` → `__`（用双下划线保证可逆，普通 tool 名不会自然出现 `__`）
      - 其他非法字符 → `_`
      - 首字符非字母 → 加 `t_` 前缀（OpenAI 要求字母开头）
    """
    s = raw.replace(".", "__")
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", s)
    if s and not s[0].isalpha():
        s = "t_" + s
    return s


def _restore_function_name(sanitized: str) -> str:
    """LLM 返回 tool_calls.function.name 时反向还原（`__` → `.`）。"""
    return sanitized.replace("__", ".")


def _sanitize_messages_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """加固历史 messages：丢弃空 name 的 tool_call + 修正非法 function name。

    返回**新** list（不改原 messages），跟 agent 内部状态隔离。
    """
    skipped_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        # tool response：若 tool_call_id 对应已被丢弃的 tool_call，本条也丢
        if role == "tool" and msg.get("tool_call_id") in skipped_ids:
            continue

        tcs = msg.get("tool_calls")
        if not tcs:
            out.append(msg)
            continue

        new_tcs: list[dict[str, Any]] = []
        for tc in tcs:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            if not name:
                # 空名 tool_call：整条丢，记下 id 让对应 tool response 也丢
                tc_id = tc.get("id") or ""
                if tc_id:
                    skipped_ids.add(tc_id)
                continue
            if not _VALID_FN_NAME.match(name):
                new_fn = {**fn, "name": _sanitize_function_name(name)}
                tc = {**tc, "function": new_fn}
            new_tcs.append(tc)

        new_msg = dict(msg)
        if new_tcs:
            new_msg["tool_calls"] = new_tcs
        else:
            # 整条 assistant message 只有空/非法 tool_call；保留 content（若有），
            # 否则整条丢（不带 content 也不带 tool_calls 的 assistant 没意义）
            new_msg.pop("tool_calls", None)
            if not (new_msg.get("content") or "").strip():
                continue
        out.append(new_msg)
    return out


def _sanitize_tools_for_llm(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """sanitize 注册 tools 里的 function.name（同上规则）。"""
    out: list[dict[str, Any]] = []
    for t in tools:
        fn = t.get("function") or {}
        name = fn.get("name") or ""
        if not name:
            continue
        if not _VALID_FN_NAME.match(name):
            t = {**t, "function": {**fn, "name": _sanitize_function_name(name)}}
        out.append(t)
    return out


def _restore_tool_call_names(response: Any) -> None:
    """LLM response 里 tool_calls.function.name 反向还原（in-place 修改 SimpleNamespace）。"""
    try:
        for choice in getattr(response, "choices", []) or []:
            tcs = getattr(getattr(choice, "message", None), "tool_calls", None) or []
            for tc in tcs:
                fn = getattr(tc, "function", None)
                if fn is not None and getattr(fn, "name", None):
                    fn.name = _restore_function_name(fn.name)
    except Exception:
        # 防御：异常 SDK 结构不应破坏主链路，最差情况就是 name 不还原
        pass


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

    # 历史 messages / MCP tools 可能含非法 function name（`.` 分隔、空名等）
    # 在发给 LLM 前 sanitize，返回时由 _restore_tool_call_names 反向还原
    messages = _sanitize_messages_for_llm(messages)
    if tools:
        tools = _sanitize_tools_for_llm(tools)

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
        kwargs["stream"] = True
        # 流式响应里把 usage 放到最后一个 chunk，否则 prompt_tokens / completion_tokens
        # 都拿不到（kimi / qwen 等 OpenAI 兼容 provider 默认不推 usage）
        kwargs["stream_options"] = {"include_usage": True}
        response = _openai_call(
            provider_config,
            need_proxy,
            lambda client: _run_openai_stream(client, kwargs, on_token_chunk),
        )
    else:
        response = _openai_call(
            provider_config,
            need_proxy,
            lambda client: client.chat.completions.create(**kwargs),
        )

    _restore_tool_call_names(response)
    return response


def _run_openai_stream(
    client: Any,
    kwargs: dict[str, Any],
    on_token_chunk: Callable[[str], None] | None = None,
    on_thinking_chunk: Callable[[str], None] | None = None,
) -> Any:
    """OpenAI 兼容协议流式调用，逐 token 回调并返回与非流式相同结构的 response 对象。

    同时服务普通对话与 thinking：reasoning provider（qwen/kimi/glm/minimax/deepseek）的思考
    内容位于 delta.reasoning_content，经 on_thinking_chunk 实时透传，并收集后挂到返回
    message.reasoning_content —— 供 agent 在多轮工具调用时回传（部分 provider 不回传会 400）。
    """
    from types import SimpleNamespace

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
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

        # thinking 内容（OpenAI SDK 不暴露此字段，须用 getattr）：实时回调 + 收集
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            reasoning_parts.append(reasoning)
            if on_thinking_chunk is not None:
                on_thinking_chunk(reasoning)

        if getattr(delta, "content", None):
            content_parts.append(delta.content)
            if on_token_chunk is not None:
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
    message = SimpleNamespace(
        content=final_content,
        tool_calls=tool_calls,
        reasoning_content="".join(reasoning_parts) or None,
    )
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

# claude-sonnet-4-5 单次最大输出 tokens；thinking 时 max_tokens 必须 > budget_tokens
# 且不能超模型上限，否则 Anthropic 直接 400。
_CLAUDE_OUTPUT_CAP: int = 64_000


def call_with_thinking(
    messages: list[dict[str, Any]],
    budget_tokens: int = 8000,
    tools: list[dict[str, Any]] | None = None,
    on_thinking_chunk: Callable[[str], None] | None = None,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    通用 Extended Thinking 入口，按当前 provider 的 thinking 能力声明分发。

    - kind="anthropic"        → Claude 原生 thinking（budget_tokens 原生生效）。
    - kind="openai_reasoning" → OpenAI 兼容，读 delta.reasoning_content（qwen/kimi/glm/minimax/deepseek）。
    - thinking 未声明（None）  → 静默降级为普通 chat()。

    Args:
        messages: 对话历史（OpenAI 格式）。
        budget_tokens: thinking 预算 tokens（仅 claude 与设了 budget_key 的 provider 如 qwen 生效）。
        tools: Function Calling 工具列表，可为 None。
        on_thinking_chunk: 每收到一段 thinking 文本时的回调，可为 None。
        on_token_chunk: 每收到一段正文 token 时的回调，可为 None。

    Returns:
        与 chat() 相同格式的 response 对象。
    """
    spec = config.get_active_config().thinking
    if spec is None:
        return chat(messages, tools=tools, on_token_chunk=on_token_chunk)
    if spec.kind == "anthropic":
        return _chat_claude_thinking(messages, budget_tokens, tools, on_thinking_chunk, on_token_chunk)
    return _chat_openai_reasoning(
        messages, budget_tokens, tools, on_thinking_chunk, on_token_chunk, spec,
    )


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

    # budget 下限 1024（Anthropic 最小值）、上限留 4096 给正文，保证 max_tokens 不超模型上限
    budget = max(1024, min(budget_tokens, _CLAUDE_OUTPUT_CAP - 4096))

    kwargs: dict[str, Any] = {
        "model": provider_config.model,
        # max_tokens 必须 > budget_tokens 且 <= 模型上限，Anthropic 强制要求
        "max_tokens": budget + 4096,
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


def _chat_openai_reasoning(
    messages: list[dict[str, Any]],
    budget_tokens: int,
    tools: list[dict[str, Any]] | None,
    on_thinking_chunk: Callable[[str], None] | None,
    on_token_chunk: Callable[[str], None] | None,
    spec: "config.ThinkingSpec",
) -> Any:
    """
    OpenAI 兼容协议的 thinking 流式调用，按 ThinkingSpec 拼装请求。

    覆盖 qwen / kimi / glm / minimax / deepseek：
    - 思考内容位于 delta.reasoning_content，开启 thinking 时多数 provider 强制 stream=True；
    - spec.enable_extra_body 覆盖 provider 基础 extra_body 中的同名键（如把 qwen 的
      enable_thinking=False 翻成 True、kimi 的 thinking.type 从 disabled 翻成 enabled）；
    - spec.budget_key 不为 None 时把 budget_tokens 透传（目前仅 qwen 的 thinking_budget）；
    - spec.thinking_model 不为 None 时切到该 provider 的专用思考模型（如 deepseek-reasoner）。

    thinking 内容通过 on_thinking_chunk 透传，并由 _run_openai_stream 挂到 message.reasoning_content，
    供 agent 多轮工具调用时回传（kimi 等不回传会 400）；但不写入 chat_history（防 prompt injection）。
    """
    provider_config = config.get_active_config()

    messages = _sanitize_messages_for_llm(messages)
    if tools:
        tools = _sanitize_tools_for_llm(tools)

    extra_body = dict(provider_config.extra_body or {})
    extra_body.update(spec.enable_extra_body or {})
    if spec.budget_key:
        extra_body[spec.budget_key] = budget_tokens

    kwargs: dict[str, Any] = {
        "model": spec.thinking_model or provider_config.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},  # 确保最后一个 chunk 携带 usage 统计
        "extra_body": extra_body,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    need_proxy = config.ACTIVE_PROVIDER in config.PROXIED_PROVIDERS and bool(config.LLM_PROXY)
    response = _openai_call(
        provider_config,
        need_proxy,
        lambda client: _run_openai_stream(client, kwargs, on_token_chunk, on_thinking_chunk),
    )
    _restore_tool_call_names(response)
    return response
