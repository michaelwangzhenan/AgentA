"""
LLM 统一调用接口（门面）

上层业务代码只需调用 chat() / call_with_thinking()，完全不感知底层 Provider 差异。
通过 config.py 中的 ACTIVE_MODEL 决定实际调用哪个模型（厂商从模型反推），按 provider
的 sdk / thinking 声明分发到 `openai_provider` 或 `claude_provider`。

支持两种调用模式：
    1. 普通对话：直接返回文本内容
    2. Function Calling：传入 tools 参数，返回原始 response 供 Agent 处理

本模块只负责「派发」与「provider 无关的消息加固」（_sanitize_messages_for_llm），
两家 provider 的具体实现分别在 openai_provider.py / claude_provider.py。
"""

import logging
from collections.abc import Callable
from typing import Any

import src.config as config
from src.llm import claude_provider, openai_provider
# function name 规则属 OpenAI 协议，消息清洗复用其判定（依赖单向：provider → openai_provider）
from src.llm.openai_provider import _VALID_FN_NAME, _sanitize_function_name

logger = logging.getLogger(__name__)


def _log_llm_error(
    provider_config: config.ProviderConfig,
    model_config: config.ModelConfig,
    exc: Exception,
) -> None:
    """LLM API 调用异常时记一行带 provider / model 的日志（异常仍向上抛）。

    放在门面层是因为所有调用方（agent / rag / ...）都经此入口，且这里能从
    get_active_model() 准确拿到当前 provider 与 model，便于定位是哪家挂了。
    """
    logger.error(
        "[LLM] 调用异常 provider=%s model=%s sdk=%s: %s",
        provider_config.label or model_config.provider,
        model_config.model_id,
        provider_config.sdk,
        exc,
    )


def _sanitize_messages_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """加固历史 messages：丢弃空 name 的 tool_call + 修正非法 function name。

    provider 无关，门面在派发前统一执行；返回**新** list（不改原 messages），
    跟 agent 内部状态隔离。
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


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    on_token_chunk: Callable[[str], None] | None = None,
) -> Any:
    """
    统一 LLM 调用入口，按当前模型的 provider 自动选择 OpenAI 兼容 API 或 anthropic 原生 SDK。

    Args:
        messages: 对话历史，入参统一用 OpenAI messages 格式（anthropic 分支内部自行适配）。
        tools: Function Calling 工具列表（JSON Schema 格式），为 None 时走普通对话。
        temperature: 采样温度，0.0 ~ 1.0，越低越确定性。
        on_token_chunk: 每收到一段正文 token 时的回调，为 None 时走非流式。

    Returns:
        统一返回 OpenAI 结构的 response 对象（anthropic 分支会包装成兼容结构），供 Agent 读取 choices 和 usage。

    Raises:
        openai.APIError: 走 OpenAI 兼容 API 调用失败时抛出。
        anthropic.APIError: 走 anthropic 原生 SDK 调用失败时抛出。
    """
    provider_config, model_config = config.get_active_model()

    # 历史 messages 可能含空名 / 非法 function name 的 tool_call（早期 bug 或 MCP 带来）。
    # 两条分支都先清理：空名 tool_call 整条丢 + 对应 orphan tool 响应丢，否则 provider 端 400。
    messages = _sanitize_messages_for_llm(messages)

    try:
        if provider_config.sdk == "anthropic":
            return claude_provider.chat(messages, tools, temperature, on_token_chunk)
        return openai_provider.chat(messages, tools, temperature, on_token_chunk)
    except Exception as exc:
        _log_llm_error(provider_config, model_config, exc)
        raise


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
    provider_config, model_config = config.get_active_model()
    spec = model_config.thinking
    if spec is None:
        # 降级路径走 chat()，由其自行记录异常 provider，避免重复日志
        return chat(messages, tools=tools, on_token_chunk=on_token_chunk)

    # thinking 分支的 messages 同样要清洗（chat() 走自己的清洗，这里两条 thinking 路径统一处理）
    messages = _sanitize_messages_for_llm(messages)
    try:
        if spec.kind == "anthropic":
            return claude_provider.chat_thinking(
                messages, budget_tokens, tools, on_thinking_chunk, on_token_chunk,
            )
        return openai_provider.chat_reasoning(
            messages, budget_tokens, tools, on_thinking_chunk, on_token_chunk, spec,
        )
    except Exception as exc:
        _log_llm_error(provider_config, model_config, exc)
        raise
