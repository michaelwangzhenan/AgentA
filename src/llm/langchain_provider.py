import httpx
import src.config as config

def build_chat_model(temperature: float = 0.7):
    prov, model = config.get_active_model()
    if prov.sdk == 'anthropic':
        return _build_anthropic(prov, model, temperature)
    return _build_openai(prov, model, temperature)

def _build_openai(prov, model, temperature: float):
    from langchain_openai import ChatOpenAI
    http_client = None
    if prov.proxied and config.LLM_PROXY:
        http_client = httpx.Client(proxy=config.LLM_PROXY)
    model_kwargs = dict()
    if model.extra_body:
        model_kwargs['extra_body'] = model.extra_body
    return ChatOpenAI(model=model.model_id, api_key=prov.api_key,
        base_url=prov.base_url or None, temperature=temperature,
        http_client=http_client, model_kwargs=model_kwargs if model_kwargs else dict())

def _build_anthropic(prov, model, temperature: float):
    from langchain_anthropic import ChatAnthropic
    http_client = None
    if config.LLM_PROXY: http_client = httpx.Client(proxy=config.LLM_PROXY)
    return ChatAnthropic(model=model.model_id, api_key=prov.api_key,
        max_tokens=config.CLAUDE_MAX_TOKENS,
        temperature=temperature, http_client=http_client)
