from typing import Any
import httpx
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
import src.config as config

def build_chat_model(temperature: float = 0.7):
    cfg = config.get_active_config()
    if config.ACTIVE_PROVIDER == 'claude':
        return _build_anthropic(cfg, temperature)
    return _build_openai(cfg, temperature)

def _build_openai(cfg, temperature: float):
    from langchain_openai import ChatOpenAI
    http_client = None
    if config.ACTIVE_PROVIDER in config.PROXIED_PROVIDERS and config.LLM_PROXY:
        http_client = httpx.Client(proxy=config.LLM_PROXY)
    model_kwargs = dict()
    eb = 'extra_body'
    if getattr(cfg, eb, None): model_kwargs[eb] = cfg.extra_body
    return ChatOpenAI(model=cfg.model, api_key=cfg.api_key,
        base_url=cfg.base_url or None, temperature=temperature,
        http_client=http_client, model_kwargs=model_kwargs if model_kwargs else dict())

def _build_anthropic(cfg, temperature: float):
    from langchain_anthropic import ChatAnthropic
    http_client = None
    if config.LLM_PROXY: http_client = httpx.Client(proxy=config.LLM_PROXY)
    return ChatAnthropic(model=cfg.model, api_key=cfg.api_key,
        max_tokens=config.CLAUDE_MAX_TOKENS,
        temperature=temperature, http_client=http_client)
