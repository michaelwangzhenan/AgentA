"""GET /api/config 只读视图 UT。

主要覆盖：
1. 返回结构对齐 ConfigResponse
2. monkeypatch 改 src.config 常量后 response 反映
3. **响应里不出现任何 API key 字段**
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_config_returns_all_groups(client: TestClient) -> None:
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    expected_keys = {
        "llm",
        "rag",
        "memory",
        "rules",
        "mcp",
        "security",
        "web",
        "log",
    }
    assert set(body.keys()) == expected_keys


def test_config_reflects_active_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cfg, "ACTIVE_PROVIDER", "qwen")
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["llm"]["active_provider"] == "qwen"
    assert body["llm"]["model"] == _cfg.PROVIDER_CONFIGS["qwen"].model


def test_config_available_providers_sorted(client: TestClient) -> None:
    body = client.get("/api/config").json()
    providers = body["llm"]["available_providers"]
    assert providers == sorted(providers)
    assert "kimi" in providers


def test_config_reflects_rag_flags(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cfg, "RAG_TOP_K", 42)
    monkeypatch.setattr(_cfg, "RERANKER_ENABLED", False)
    body = client.get("/api/config").json()
    assert body["rag"]["top_k"] == 42
    assert body["rag"]["reranker_enabled"] is False


def test_config_no_api_keys_in_response(client: TestClient) -> None:
    """响应里不能出现任何 API key 字段或值（即使脱敏后也不允许）。"""
    body = client.get("/api/config").json()
    text = json.dumps(body, ensure_ascii=False).lower()

    forbidden_keys = [
        "api_key",
        "moonshot_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "deepseek_api_key",
        "qwen_api_key",
        "glm_api_key",
        "grok_api_key",
        "minimax_api_key",
        "serpapi_api_key",
    ]
    for k in forbidden_keys:
        assert k not in text, f"响应里不应包含 {k!r}：{text}"

    for provider_name, provider_cfg in _cfg.PROVIDER_CONFIGS.items():
        # 跳过 ollama 这种本地占位 key（key 值跟 provider 名重名，不是真 key）
        if provider_cfg.api_key and len(provider_cfg.api_key) > 10:
            assert provider_cfg.api_key not in text, (
                f"{provider_name} 的真实 api_key 泄漏到了响应里"
            )


def test_config_force_temperature_can_be_null(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deepseek / glm 等 provider 的 force_temperature=None，response 应该是 null。"""
    monkeypatch.setattr(_cfg, "ACTIVE_PROVIDER", "deepseek")
    body = client.get("/api/config").json()
    assert body["llm"]["force_temperature"] is None
