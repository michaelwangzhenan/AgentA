"""OpenAI 兼容协议调用实现（覆盖 kimi/qwen/deepseek/glm/minimax/openai/grok/gemini/ollama）。

由 `provider.py` 门面在非 anthropic provider 时分发到这里。入参 messages 已由门面做过
`_sanitize_messages_for_llm` 清洗；本模块额外负责 tool/function name 的 sanitize 与
返回时的 restore（OpenAI 协议对 name 的 `^[a-zA-Z0-9_-]+$` 约束特有）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx
import openai

import src.config as config
from src.llm.thinking_params import openai_thinking_extra_body


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
    tools: list[dict[str, Any]] | None,
    temperature: float,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """OpenAI 兼容协议普通对话 / Function Calling 调用。

    messages 已由门面清洗；这里额外 sanitize tools 的 function name（`.` 分隔等），
    返回时由 `_restore_tool_call_names` 反向还原。
    """
    provider_config, model_config = config.get_active_model()

    if tools:
        tools = _sanitize_tools_for_llm(tools)

    # 个别模型对 temperature 有硬约束（如 kimi 强制要求 = 0.6，非约束值直接 400），
    # 由 ModelConfig.force_temperature 声明，此处统一覆盖，避免在每个调用点重复处理。
    effective_temperature = (
        model_config.force_temperature
        if model_config.force_temperature is not None
        else temperature
    )

    kwargs: dict[str, Any] = {
        "model": model_config.model_id,
        "messages": messages,
        "temperature": effective_temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if model_config.extra_body:
        kwargs["extra_body"] = model_config.extra_body

    need_proxy = provider_config.proxied and bool(config.LLM_PROXY)

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
    last_tc_idx = 0  # index 缺失时（Gemini 兼容层）记住上一个 tool_call 槽位
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
                # 部分 OpenAI 兼容层（如 Gemini）流式不给 per-call index：每个带 id 的
                # chunk 即一个完整调用，纯 arguments 续传挂到上一个槽位。否则多个并行调用
                # 会因 index 都为 None 而塌成一个（name/arguments 被拼接成垃圾）。
                if idx is None:
                    if tc_delta.id or not tool_calls_map:
                        idx = len(tool_calls_map)
                        last_tc_idx = idx
                    else:
                        idx = last_tc_idx
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


def chat_reasoning(
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

    messages 已由门面清洗；thinking 内容通过 on_thinking_chunk 透传，并由 _run_openai_stream
    挂到 message.reasoning_content，供 agent 多轮工具调用时回传（kimi 等不回传会 400）；
    但不写入 session_store（防 prompt injection）。
    """
    provider_config, model_config = config.get_active_model()

    if tools:
        tools = _sanitize_tools_for_llm(tools)

    model_id, extra_body = openai_thinking_extra_body(model_config, spec, budget_tokens)

    kwargs: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},  # 确保最后一个 chunk 携带 usage 统计
        "extra_body": extra_body,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    need_proxy = provider_config.proxied and bool(config.LLM_PROXY)
    response = _openai_call(
        provider_config,
        need_proxy,
        lambda client: _run_openai_stream(client, kwargs, on_token_chunk, on_thinking_chunk),
    )
    _restore_tool_call_names(response)
    return response
