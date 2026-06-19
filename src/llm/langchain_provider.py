"""
langchain_provider —— 构造 LangChain ChatModel（与公共层 provider 同源配置）

复用 `config.get_active_model()` 的 provider / model 单一真相源，只把它翻译成
LangChain 的 `ChatOpenAI` / `ChatAnthropic`。streaming / thinking 行为尽量对齐
`src/llm/provider.py`，但受 LangChain 框架能力限制（见 build_chat_model docstring）。
"""
import httpx

import src.config as config


def build_chat_model(temperature: float = 0.7, streaming: bool = False, thinking_cfg=None):
    """构造 LangChain ChatModel。

    Args:
        temperature: 采样温度（thinking 开启时 Anthropic 会被强制为 1）。
        streaming:   True 时模型按 token 推流，配合 BaseCallbackHandler.on_llm_new_token
                     把 token_chunk 桥接到 EventBus。
        thinking_cfg: `ThinkingConfig | None`。enabled 时按当前 provider 的 thinking 声明
                     启用 Extended Thinking（best-effort，详 iter_a_LangChain.md §4）：
                     - Anthropic：原生 `thinking={"type":"enabled","budget_tokens":N}`；
                     - OpenAI 兼容（qwen/kimi/glm/...）：合并 model.thinking 的 extra_body。
                     注：LangChain 不区分 thinking / 正文 delta，故 thinking_chunk 不单独发。
    """
    prov, model = config.get_active_model()
    enabled = bool(thinking_cfg is not None and getattr(thinking_cfg, "enabled", False))
    budget = int(getattr(thinking_cfg, "budget", 8000)) if thinking_cfg is not None else 8000
    if prov.sdk == 'anthropic':
        return _build_anthropic(prov, model, temperature, streaming, enabled, budget)
    return _build_openai(prov, model, temperature, streaming, enabled, budget)


def _build_openai(prov, model, temperature: float, streaming: bool = False,
                  thinking_enabled: bool = False, budget: int = 8000):
    from langchain_openai import ChatOpenAI

    from src.llm.thinking_params import openai_thinking_extra_body
    http_client = None
    if prov.proxied and config.LLM_PROXY:
        http_client = httpx.Client(proxy=config.LLM_PROXY)
    model_id = model.model_id
    extra_body = dict(model.extra_body or {})
    spec = getattr(model, 'thinking', None)
    if thinking_enabled and spec is not None:
        # 与 provider 直连路径同源（enable_extra_body 翻开关 / budget_key 透传 / 切思考模型）
        model_id, extra_body = openai_thinking_extra_body(model, spec, budget)
    # extra_body 作为显式参数传给 ChatOpenAI（放 model_kwargs 会触发 UserWarning）
    return ChatOpenAI(model=model_id, api_key=prov.api_key,
        base_url=prov.base_url or None, temperature=temperature,
        streaming=streaming,
        http_client=http_client,
        extra_body=extra_body if extra_body else None)


def _build_anthropic(prov, model, temperature: float, streaming: bool = False,
                     thinking_enabled: bool = False, budget: int = 8000):
    from langchain_anthropic import ChatAnthropic

    from src.llm.thinking_params import claude_thinking_budget
    http_client = None
    if config.LLM_PROXY:
        http_client = httpx.Client(proxy=config.LLM_PROXY)
    kwargs = dict(model=model.model_id, api_key=prov.api_key,
        max_tokens=config.CLAUDE_MAX_TOKENS,
        temperature=temperature, streaming=streaming, http_client=http_client)
    if thinking_enabled:
        # 与 provider 直连路径同源：max_tokens 跟着 budget 走（budget+4096），保证严格
        # 大于 budget_tokens（Anthropic 强制）；temperature 须为 1。
        budget_eff, max_tokens = claude_thinking_budget(model, budget)
        kwargs['max_tokens'] = max_tokens
        kwargs['thinking'] = {'type': 'enabled', 'budget_tokens': budget_eff}
        kwargs['temperature'] = 1
    return ChatAnthropic(**kwargs)
